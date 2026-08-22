"""The headless run: video in, velocity field out.

Order follows the reference workflow (Bro/Dorthe/Schalko 2026): stabilise first,
then build the static background, subtract, normalise, enhance, then velocimetry,
then the correlation filter.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from pxv.geometry.world import GeoTransform, build_transform
from pxv.io import metadata as meta_io
from pxv.io import video as video_io
from pxv.preprocess.enhance import (
    AdaptiveBackground, crop, preprocess_frame, rolling_background, static_background, to_gray,
)
from pxv.preprocess.mask import analysis_mask, static_feature_mask
from pxv.project import Project
from pxv.stabilize.homography import StabilizationResult, stabilize, warp_to_reference
from pxv.velocimetry import fuse as fuse_mod
from pxv.velocimetry.piv import PIVField, piv_ensemble, piv_pair
from pxv.velocimetry.ptv import Tracks, detect, run_ptv


@dataclass
class RunResult:
    project: Project
    info: video_io.VideoInfo
    telemetry: meta_io.Telemetry
    stabilization: StabilizationResult
    field: fuse_mod.VelocityField
    transform: GeoTransform
    dt: float                       # seconds between the frames actually compared
    density: float = float("nan")
    engine_note: str = ""
    static_fraction: float = float("nan")
    static_verdict: str = ""
    log: list[str] = field(default_factory=list)
    timings: dict = field(default_factory=dict)

    # ---- world-unit accessors -------------------------------------------

    def world_grid(self) -> tuple[np.ndarray, np.ndarray]:
        return self.transform.to_world(self.field.x, self.field.y)

    def velocity_world(self) -> tuple[np.ndarray, np.ndarray]:
        """Velocity in world units per second (m/s when georeferenced)."""
        du, dv = self.transform.displacement_to_world(
            self.field.x, self.field.y, self.field.u, self.field.v
        )
        return du / self.dt, dv / self.dt

    def speed_world(self) -> np.ndarray:
        u, v = self.velocity_world()
        return np.hypot(u, v)

    def summary(self) -> str:
        u, v = self.velocity_world()
        sp = np.hypot(u, v)[self.field.valid]
        unit = f"{self.transform.units}/s"
        lines = [
            self.info.describe(),
            self.stabilization.summary(),
            self.transform.describe(),
            self.field.summary(),
        ]
        if sp.size:
            lines.append(
                f"speed: median {np.median(sp):.4f}, p95 {np.percentile(sp, 95):.4f}, "
                f"max {sp.max():.4f} {unit}"
            )
        if self.engine_note:
            lines.append(f"seeding: {self.engine_note}")
        if np.isfinite(self.static_fraction):
            lines.append(f"static-motion check [{self.static_verdict}]: "
                         f"{self.static_fraction:.0%} of cells report no motion")
        return "\n".join(lines)


def _log(result_log: list[str], msg: str, verbose: bool) -> None:
    result_log.append(msg)
    if verbose:
        print(msg, flush=True)


def load_frames(project: Project, verbose: bool = False) -> tuple[np.ndarray, video_io.VideoInfo, list[int]]:
    """Decode, crop and grayscale the selected frames into one uint8 stack."""
    cfg = project.video
    info = video_io.probe(cfg.path)
    idx = video_io.selected_indices(info, cfg.start, cfg.stop, cfg.step)
    frames = []
    for _, bgr in video_io.iter_frames(cfg.path, cfg.start, cfg.stop, cfg.step):
        frames.append(crop(to_gray(bgr), cfg.roi))
    if not frames:
        raise RuntimeError("no frames decoded; check video.start/stop/step and the ROI")
    stack = np.stack(frames)
    if verbose:
        print(f"decoded {len(stack)} frames of {stack.shape[2]}x{stack.shape[1]}", flush=True)
    return stack, info, idx[: len(stack)]


def run(project: Project, verbose: bool = False, progress=None) -> RunResult:
    log: list[str] = []
    timings: dict = {}

    t0 = time.time()
    raw, info, indices = load_frames(project, verbose)
    timings["decode"] = time.time() - t0
    _log(log, info.describe(), verbose)

    telemetry = meta_io.read_telemetry(project.video.path)
    if len(telemetry):
        _log(log, telemetry.describe(), verbose)
    else:
        _log(log, "no DJI SRT sidecar found; height and position must come from the project file", verbose)

    fps = project.video.fps or info.fps
    if fps <= 0:
        raise RuntimeError("frame rate unknown; set video.fps in the project file")
    dt = project.video.step / fps

    # ---- stabilisation, before background and enhancement ----------------
    t0 = time.time()
    scfg = project.stabilize
    mask = static_feature_mask(raw.shape[1:], scfg.static_mask, scfg.water_mask)
    if scfg.enabled and scfg.model != "none":
        rough = (raw.astype(np.float32) / 255.0)
        stab = stabilize(rough, scfg, mask, progress=progress)
        _log(log, stab.summary(), verbose)
        warped = np.empty_like(raw)
        for i, H in enumerate(stab.transforms):
            warped[i] = np.clip(warp_to_reference(raw[i].astype(np.float32), H), 0, 255).astype(np.uint8)
        raw = warped
        motion = stab.apparent_motion_px()
        _log(log, f"apparent aircraft motion: peak-to-peak "
                  f"{np.ptp(motion[:, 0]):.1f} x {np.ptp(motion[:, 1]):.1f} px "
                  f"(no wind data available; this is the direct proxy)", verbose)
    else:
        stab = StabilizationResult(
            transforms=[np.eye(3) for _ in raw], residuals=[0.0] * len(raw), inliers=[0] * len(raw)
        )
        _log(log, "stabilisation disabled", verbose)
    timings["stabilize"] = time.time() - t0

    # ---- background, normalise, enhance ----------------------------------
    t0 = time.time()
    pcfg = project.preprocess
    background = None
    backgrounds = None
    if pcfg.background == "static":
        background = static_background(raw, pcfg.bg_stride)
        _log(log, f"static background: mean of every {pcfg.bg_stride}th frame "
                  f"({len(raw[::pcfg.bg_stride])} frames)", verbose)
    elif pcfg.background == "rolling":
        backgrounds = rolling_background(raw, pcfg.bg_window)
        _log(log, f"rolling background over {pcfg.bg_window} frames "
                  f"({pcfg.bg_window / fps * 1000:.0f} ms): a temporal high-pass that "
                  "suppresses a stationary bed seen through clear water", verbose)
    elif pcfg.background == "adaptive":
        ab = AdaptiveBackground(pcfg.bg_speed)
        for f in raw:
            ab.update(f)
        background = ab.update(raw[-1])
        _log(log, f"adaptive background, speed {pcfg.bg_speed}", verbose)

    # run one frame with per-stage reporting, so each step of the chain can be
    # judged on its own rather than as one opaque block
    stages: list = []
    preprocess_frame(raw[len(raw) // 2], pcfg,
                     backgrounds[len(raw) // 2] if pcfg.background == "rolling" else background,
                     report=stages)
    for label, m in stages:
        _log(log, f"  preprocess {label:26s} contrast {m['contrast']:7.4f}  "
                  f"texture {m['texture']:7.4f}  saturated {m['saturated']:5.1%}", verbose)

    if pcfg.background == "rolling":
        proc = np.stack([preprocess_frame(f, pcfg, b) for f, b in zip(raw, backgrounds)])
    else:
        proc = np.stack([preprocess_frame(f, pcfg, background) for f in raw])

    # PIV and PTV want different preprocessing, so keep both.
    # The four-stage chain exists to amplify faint texture so correlation can lock
    # onto it. Injected tracers are the opposite case: they are the brightest thing
    # in the frame, and amplifying faint texture destroys the contrast that
    # identifies them. Measured on the Isar clip, the chain turns a 33x
    # tracer-to-background contrast into 1.3x or worse. So PTV bypasses it.
    ptv_frames = (raw.astype(np.float32) / 255.0) if project.ptv.use_raw else proc

    amask = analysis_mask(proc.shape[1:], project.analysis.include, project.analysis.exclude)
    if amask is not None:
        _log(log, f"analysis mask covers {amask.mean():.0%} of the frame", verbose)
        proc = proc * amask[None, :, :].astype(np.float32)
    else:
        amask = None
    timings["preprocess"] = time.time() - t0

    # ---- seeding diagnostic ---------------------------------------------
    window = list(project.piv.windows)[-1]
    n_det = float(np.mean([len(detect(ptv_frames[i], project.ptv, amask)) for i in
                           range(0, len(proc), max(1, len(proc) // 5))]))
    density = fuse_mod.seeding_density(n_det, proc.shape[1:], window)
    engine, note = fuse_mod.recommend_engine(density)
    _log(log, f"seeding diagnostic: {n_det:.0f} objects/frame -> {note}", verbose)

    # ---- velocimetry -----------------------------------------------------
    t0 = time.time()
    piv_fields: list[PIVField] = []
    if project.piv.enabled:
        pairs = [(proc[i], proc[i + 1]) for i in range(len(proc) - 1)]
        for k, (a, b) in enumerate(pairs):
            piv_fields.append(piv_pair(a, b, project.piv))
            if progress is not None:
                progress(k + 1, len(pairs))
        y = float(np.mean([f.yield_fraction() for f in piv_fields]))
        _log(log, f"PIV: {len(piv_fields)} pairs, mean vector yield {y:.0%} "
                  f"at correlation threshold {project.piv.corr_threshold}", verbose)
    timings["piv"] = time.time() - t0

    # Bed-locking is spatial: over clear water the bed wins, over rippled water the
    # surface does, and both happen in one frame. Judge each PIV cell on its own
    # rather than discarding the whole field on one verdict.
    piv_static = float("nan")
    piv_drop = None
    if piv_fields:
        piv_drop = fuse_mod.piv_static_cells(
            piv_fields, project.grid.piv_static_threshold_px,
            project.grid.piv_static_fraction)
        alive = piv_fields[0].valid.size - int(piv_drop.sum())
        piv_static = float(piv_drop.sum()) / max(piv_drop.size, 1)
        _log(log, f"PIV per-cell health: {int(piv_drop.sum())} of {piv_drop.size} cells "
                  f"are persistently static and were dropped; {alive} kept", verbose)

    t0 = time.time()
    tracks: Tracks | None = None
    if project.ptv.enabled:
        seed = piv_fields[0] if (piv_fields and project.ptv.piv_guided) else None
        if seed is not None and np.isfinite(piv_static) and piv_static > 0.9:
            seed = None
            _log(log, f"PIV guidance withheld: {piv_static:.0%} of PIV cells are "
                      "persistently static, so seeding the tracer motion model from that "
                      "field would bias tracking toward zero", verbose)
        tracks = run_ptv(ptv_frames, project.ptv, piv_seed=seed, mask=amask,
                         progress=progress)
        _log(log, f"PTV: {tracks.n} vectors from {len(proc) - 1} frame pairs"
                  + (" (PIV-guided)" if seed is not None else ""), verbose)
    timings["ptv"] = time.time() - t0

    if not piv_fields and tracks is None:
        raise RuntimeError("both PIV and PTV are disabled; nothing to compute")

    field = fuse_mod.fuse(proc.shape[1:], project.grid.cell_px, piv_fields, tracks,
                          project.grid.min_samples, piv_drop=piv_drop)

    # "PIV first, then correct with PTV": PIV supplies coverage, PTV supplies
    # accuracy, and where they coincide their ratio carries to the PIV-only cells.
    if project.grid.correct_piv_with_ptv and piv_fields and tracks is not None:
        field, corr = fuse_mod.correct_piv_with_ptv(
            field, min_overlap=project.grid.correction_min_overlap)
        if corr["applied"]:
            _log(log, f"PIV corrected with PTV: gain {corr['gain']:.3f} from "
                      f"{corr['overlap']} overlapping cells, applied to "
                      f"{corr['applied']} PIV cells", verbose)
        else:
            _log(log, f"PIV not corrected: {corr['reason']}", verbose)
    if amask is not None:
        keep = amask[np.clip(field.y.astype(int), 0, amask.shape[0] - 1),
                     np.clip(field.x.astype(int), 0, amask.shape[1] - 1)]
        field.n = np.where(keep, field.n, 0)
        field.source = np.where(keep, field.source, fuse_mod.SOURCE_NONE)
    _log(log, field.summary(), verbose)

    if project.grid.interpolate_radius_px > 0:
        before = int(field.measured.sum())
        field = fuse_mod.interpolate_gaps(field, project.grid.interpolate_radius_px,
                                          min_neighbours=project.grid.interpolate_min_neighbours)
        after = int(field.valid.sum())
        _log(log, f"gap filling within {project.grid.interpolate_radius_px:.0f} px: "
                  f"{before} measured cells -> {after} rendered "
                  f"({after - before} interpolated, tagged and excluded from statistics)",
             verbose)

    static_frac = fuse_mod.static_fraction(field)
    verdict, note = fuse_mod.assess_static(static_frac)
    _log(log, f"static-motion check [{verdict}]: {note}", verbose)

    transform = build_transform(project.geometry, proc.shape[1:], telemetry=telemetry)
    _log(log, transform.describe(), verbose)

    return RunResult(
        project=project, info=info, telemetry=telemetry, stabilization=stab,
        field=field, transform=transform, dt=dt, density=density,
        engine_note=note, static_fraction=static_frac, static_verdict=verdict,
        log=log, timings=timings,
    )

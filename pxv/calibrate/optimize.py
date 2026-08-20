"""Tier 1 search over the image-processing parameters."""

from __future__ import annotations

import copy
from dataclasses import dataclass, field

import numpy as np

from pxv.calibrate.objective import Comparison, compare, objective_value, sample_field
from pxv.calibrate.profile import convert_to_surface
from pxv.io.groundtruth import GroundTruth
from pxv.pipeline import run
from pxv.project import Project

#: Tier 1 search space, seeded from the reference workflow's published values
SEARCH_SPACE = {
    "piv.windows": [[32, 32], [64, 32], [64, 64], [96, 48], [128, 64]],
    "piv.overlap": [0.25, 0.5, 0.75],
    "piv.corr_threshold": [0.4, 0.5, 0.6, 0.7, 0.8, 0.9],
    "piv.snr_threshold": [1.1, 1.3, 1.6],
    "preprocess.bg_stride": [5, 10, 20, 50],
    "preprocess.clahe_clip": [1.0, 2.0, 4.0, 8.0],
    "video.step": [1, 2, 3, 5],
    "ptv.conv_size": [1.0, 2.0, 4.0, 8.0],
    "ptv.filter_std": [1.0, 1.5, 2.0, 3.0],
    "ptv.motion_neighbours": [5, 10, 20, 30],
}


@dataclass
class Trial:
    params: dict
    objective: float
    calibration: Comparison
    validation: Comparison | None = None


@dataclass
class CalibrationResult:
    best: Trial | None = None
    trials: list[Trial] = field(default_factory=list)
    scale: float = 1.0
    z0_site: float = float("nan")
    log: list[str] = field(default_factory=list)

    def describe(self) -> str:
        if self.best is None:
            return "calibration produced no usable trial"
        lines = [
            f"Tier 0.5 scale factor: {self.scale:.4f}",
            f"site roughness z0 from two-point verticals: {self.z0_site:.5f} m",
            f"best parameters: {self.best.params}",
            self.best.calibration.describe("calibration"),
        ]
        if self.best.validation is not None:
            lines.append(self.best.validation.describe("validation "))
            gap = self.best.validation.rmse - self.best.calibration.rmse
            lines.append(
                f"generalisation gap: {gap:+.4f} m/s"
                + ("  <- validation is much worse, suspect overfitting"
                   if gap > 0.5 * max(self.best.calibration.rmse, 1e-9) else "")
            )
        return "\n".join(lines)


def _prepare_truth(gt: GroundTruth, cfg) -> tuple[np.ndarray, np.ndarray, float]:
    """Convert the gauged speeds to surface speeds, returning (speed, sigma, z0)."""
    if cfg.conversion == "none" or gt.z_over_h is None or gt.depth is None:
        return gt.speed, (gt.sigma if gt.sigma is not None else np.zeros(len(gt))), float("nan")
    conv = convert_to_surface(
        gt.speed, gt.z_over_h, gt.depth, gt.sigma, gt.vertical_id,
        method=cfg.conversion, z0_fallback=cfg.z0_fallback_m,
    )
    return conv.u_surface, conv.sigma, conv.z0_estimate


def calibrate(project: Project, gt: GroundTruth, verbose: bool = False) -> CalibrationResult:
    from pxv.calibrate.objective import fit_scale

    ccfg = project.calibrate
    res = CalibrationResult()
    rng = np.random.default_rng(ccfg.seed)

    obs_speed, obs_sigma, z0 = _prepare_truth(gt, project.groundtruth)
    res.z0_site = z0
    res.log.append(f"ground truth converted to surface velocity, z0 = {z0:.5f} m")

    idx = np.arange(len(gt))
    rng.shuffle(idx)
    n_hold = int(round(ccfg.holdout_fraction * len(idx)))
    hold = idx[:n_hold]
    calib = idx[n_hold:]
    res.log.append(f"split: {len(calib)} calibration points, {len(hold)} held out")

    radius = project.groundtruth.search_radius_m

    # ---- Tier 0.5: one scale factor, alone and first ---------------------
    base = copy.deepcopy(project)
    if ccfg.fit_scale:
        r0 = run(base, verbose=False)
        scale, rmse = fit_scale(r0, gt.x[calib], gt.y[calib], obs_speed[calib],
                                radius, obs_sigma[calib])
        res.scale = scale
        base.geometry.scale_correction = float(base.geometry.scale_correction or 1.0) * scale
        res.log.append(
            f"Tier 0.5 scale {scale:.4f} fitted alone (RMSE after {rmse:.4f}); frozen for Tier 1"
        )
        if abs(scale - 1.0) > 0.15:
            res.log.append(
                f"WARNING: fitted scale departs from the metadata scale by {abs(scale - 1) * 100:.0f}%. "
                "That normally means the takeoff-to-water-surface offset is wrong. "
                "Treat this as a finding, not a nuisance."
            )

    # ---- Tier 1 search ---------------------------------------------------
    names = [p for p in ccfg.params if p in SEARCH_SPACE]
    if not names:
        res.log.append("no Tier 1 parameters selected; stopping after the scale fit")
        return res

    space = {n: SEARCH_SPACE[n] for n in names}
    trials_params = _sample_space(space, ccfg.n_trials, rng)

    for k, params in enumerate(trials_params):
        trial_project = copy.deepcopy(base)
        for key, value in params.items():
            trial_project.set(key, value)
        try:
            r = run(trial_project, verbose=False)
        except Exception as exc:  # a bad parameter combination is a failed trial, not a crash
            res.log.append(f"trial {k}: failed ({exc})")
            continue

        su, sv = sample_field(r, gt.x[calib], gt.y[calib], radius)
        cmp_cal = compare(
            obs_speed[calib], su, sv,
            gt.u[calib] if gt.has_direction else None,
            gt.v[calib] if gt.has_direction else None,
            obs_sigma[calib],
        )
        obj = objective_value(cmp_cal, ccfg.w_magnitude, ccfg.w_direction, ccfg.w_yield,
                              r.field.coverage())
        trial = Trial(params=params, objective=obj, calibration=cmp_cal)

        if len(hold):
            hu, hv = sample_field(r, gt.x[hold], gt.y[hold], radius)
            trial.validation = compare(
                obs_speed[hold], hu, hv,
                gt.u[hold] if gt.has_direction else None,
                gt.v[hold] if gt.has_direction else None,
                obs_sigma[hold],
            )
        res.trials.append(trial)
        if res.best is None or obj < res.best.objective:
            res.best = trial
        if verbose:
            print(f"trial {k + 1}/{len(trials_params)}  J={obj:.4f}  {params}", flush=True)

    return res


def _sample_space(space: dict, n: int, rng) -> list[dict]:
    """Latin-hypercube-style sampling of a discrete space, seeded with the reference point."""
    names = list(space)
    reference = {
        "piv.windows": [64, 64], "piv.overlap": 0.5, "piv.corr_threshold": 0.7,
        "piv.snr_threshold": 1.3, "preprocess.bg_stride": 10, "preprocess.clahe_clip": 2.0,
        "video.step": 1, "ptv.conv_size": 2.0, "ptv.filter_std": 1.5,
        "ptv.motion_neighbours": 10,
    }
    out = [{k: reference[k] for k in names if k in reference}]
    columns = {}
    for name in names:
        values = space[name]
        reps = int(np.ceil(n / len(values)))
        col = np.tile(np.arange(len(values)), reps)[:n]
        rng.shuffle(col)
        columns[name] = [values[i] for i in col]
    for i in range(n - 1):
        out.append({name: columns[name][i] for name in names})
    return out

"""Tier 1 search over the image-processing parameters."""

from __future__ import annotations

import copy
from dataclasses import dataclass, field

import numpy as np

from pxv.calibrate.objective import (
    Comparison, compare, diagnose_bias, objective_value, sample_field,
)
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
    "ptv.bright_threshold": [0.78, 0.82, 0.86, 0.90],
    "ptv.min_area_px": [8, 15, 20, 30],
    "ptv.max_displacement": [8.0, 12.0, 20.0, 30.0],
    "grid.cell_px": [24, 32, 48, 64],
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
    diagnosis: str = ""
    aborted: str = ""
    #: relative spread of the objective across trials; small means the search
    #: could not tell the parameter sets apart
    objective_spread: float = float("nan")
    log: list[str] = field(default_factory=list)

    def describe(self) -> str:
        if self.aborted:
            return f"calibration stopped before the Tier 1 search: {self.aborted}"
        if self.best is None:
            return "calibration produced no usable trial"
        lines = [
            f"Tier 0.5 scale factor: {self.scale:.4f}",
            f"site roughness z0 from two-point verticals: {self.z0_site:.5f} m",
            f"best parameters: {self.best.params}",
            self.best.calibration.describe("calibration"),
        ]
        if np.isfinite(self.objective_spread) and self.objective_spread < 0.05:
            lines.append(
                f"WARNING: objective spread {self.objective_spread:.1%} across trials; "
                "the search did not distinguish these parameter sets"
            )
        if self.best.validation is not None:
            lines.append(self.best.validation.describe("validation "))
            if self.best.validation.n < 5:
                lines.append(
                    f"WARNING: validated on {self.best.validation.n} point(s); that "
                    "cannot detect overfitting"
                )
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

    # ---- Tier 0: is the field measuring the right thing at all? ----------
    base = copy.deepcopy(project)
    r0 = run(base, verbose=False)
    su0, sv0 = sample_field(r0, gt.x[calib], gt.y[calib], radius)
    verdict, why = diagnose_bias(obs_speed[calib], np.hypot(su0, sv0))
    res.diagnosis = f"{verdict}: {why}"
    res.log.append(f"Tier 0 bias diagnosis [{verdict}] {why}")

    if np.isfinite(r0.static_fraction) and r0.static_fraction > 0.5:
        res.log.append(
            f"Tier 0 gate: {r0.static_fraction:.0%} of cells report no motion. "
            "The field is not measuring the surface, so a parameter search would fit "
            "noise and report a good score for a wrong answer."
        )
        res.aborted = (
            f"static-motion check failed ({r0.static_fraction:.0%} of cells static) "
            f"and the bias diagnosis is '{verdict}'. Fix the imagery first: seed the "
            "flow, or restrict the analysis region to water whose surface is textured."
        )
        return res
    if verdict == "failure":
        res.log.append("Tier 0 gate: the bias pattern is a velocimetry failure, not a scale error.")
        res.aborted = why
        return res

    # ---- Tier 0.5: one scale factor, alone and first ---------------------
    if ccfg.fit_scale:
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
    unknown = [p for p in ccfg.params if p not in SEARCH_SPACE]
    if unknown:
        # silently dropping a requested parameter would make the search look
        # broader than it was
        res.log.append(
            f"WARNING: no search range defined for {unknown}; they were not varied. "
            f"Known parameters: {sorted(SEARCH_SPACE)}"
        )
    if not names:
        res.log.append("no Tier 1 parameters selected; stopping after the scale fit")
        return res

    # A search needs enough held-out points to detect its own overfitting. With a
    # handful of matched verticals the validation statistic is noise, and the
    # "best" parameter set is whichever one happened to fit four points.
    matched = int(np.isfinite(su0).sum())
    hold_matched = 0
    if len(hold):
        hu, hv = sample_field(r0, gt.x[hold], gt.y[hold], radius)
        hold_matched = int(np.isfinite(hu).sum())
    res.log.append(
        f"matched points: {matched} for calibration, {hold_matched} held out "
        f"(of {len(calib)} and {len(hold)} ground-truth points)"
    )
    if hold_matched < ccfg.min_validation_points:
        res.aborted = (
            f"only {hold_matched} held-out points have a computed vector, below "
            f"min_validation_points={ccfg.min_validation_points}. A parameter search "
            "cannot be validated on that, so it would overfit undetectably. The limit "
            "is coverage, not parameters: raise the measured area first."
        )
        res.log.append(f"Tier 1 skipped: {res.aborted}")
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

    if len(res.trials) >= 3:
        values = np.array([t.objective for t in res.trials if np.isfinite(t.objective)])
        if values.size >= 3:
            spread = float(values.max() - values.min())
            relative = spread / max(abs(float(np.median(values))), 1e-9)
            res.objective_spread = relative
            if relative < 0.05:
                res.log.append(
                    f"the objective varied by only {relative:.1%} across {values.size} "
                    "trials, so the search did not distinguish the parameter sets. "
                    "Treat the chosen values as arbitrary within that range."
                )
    return res


def _sample_space(space: dict, n: int, rng) -> list[dict]:
    """Latin-hypercube-style sampling of a discrete space, seeded with the reference point."""
    names = list(space)
    reference = {
        "piv.windows": [64, 64], "piv.overlap": 0.5, "piv.corr_threshold": 0.7,
        "piv.snr_threshold": 1.3, "preprocess.bg_stride": 10, "preprocess.clahe_clip": 2.0,
        "video.step": 1, "ptv.conv_size": 2.0, "ptv.filter_std": 1.5,
        "ptv.motion_neighbours": 10, "ptv.bright_threshold": 0.86,
        "ptv.min_area_px": 20, "ptv.max_displacement": 12.0, "grid.cell_px": 40,
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

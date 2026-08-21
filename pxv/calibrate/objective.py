"""Comparing a computed field against ground truth, and the calibration objective.

Parameters are tiered, and only Tier 1 reaches the optimizer:

Tier 0
    Geometry, frame interval, CRS, stabilisation quality. Never optimized. If the
    scale is wrong, an optimizer will happily compensate by shrinking the
    interrogation window until the numbers match, producing a meaningless field
    that validates perfectly.
Tier 0.5
    One global scale factor, fitted alone and first, then frozen. Needed only
    because no dedicated ground control points are available, so the absolute
    scale carries real uncertainty from the takeoff-to-water offset.
Tier 1
    Window size, overlap, correlation threshold, frame step, preprocessing
    strength. The actual search space.
Tier 2
    Profile conversion of the ground truth, handled in ``pxv.calibrate.profile``.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
from scipy.spatial import cKDTree


@dataclass
class Comparison:
    n: int
    rmse: float
    bias: float
    mae: float
    r2: float
    angular_error_deg: float
    coverage: float
    matched: np.ndarray = field(default_factory=lambda: np.zeros(0, bool))
    observed: np.ndarray = field(default_factory=lambda: np.zeros(0))
    modelled: np.ndarray = field(default_factory=lambda: np.zeros(0))

    def describe(self, label: str = "") -> str:
        tag = f"{label}: " if label else ""
        return (
            f"{tag}n={self.n}  RMSE {self.rmse:.4f}  bias {self.bias:+.4f}  "
            f"MAE {self.mae:.4f}  R2 {self.r2:.3f}  "
            f"angle {self.angular_error_deg:.1f} deg  matched {self.coverage:.0%}"
        )


def sample_field(result, wx: np.ndarray, wy: np.ndarray, radius: float):
    """Sample the computed field at world locations, within a search radius."""
    gx, gy = result.world_grid()
    u, v = result.velocity_world()
    valid = result.field.valid
    pts = np.column_stack([gx[valid].ravel(), gy[valid].ravel()])
    if len(pts) == 0:
        n = len(wx)
        return np.full(n, np.nan), np.full(n, np.nan)
    tree = cKDTree(pts)
    uu = u[valid].ravel()
    vv = v[valid].ravel()
    idx = tree.query_ball_point(np.column_stack([wx, wy]), r=radius)
    su = np.full(len(wx), np.nan)
    sv = np.full(len(wx), np.nan)
    for i, ids in enumerate(idx):
        if ids:
            su[i] = np.median(uu[ids])
            sv[i] = np.median(vv[ids])
    return su, sv


def compare(
    observed_speed: np.ndarray,
    modelled_u: np.ndarray,
    modelled_v: np.ndarray,
    observed_u: np.ndarray | None = None,
    observed_v: np.ndarray | None = None,
    sigma: np.ndarray | None = None,
) -> Comparison:
    """Weighted magnitude and angular comparison.

    Each residual is weighted by ``1 / sigma^2`` where a per-point uncertainty is
    available, so noisy verticals, and points that came through the noisier
    two-point extrapolation, stop dominating the fit.
    """
    modelled_speed = np.hypot(modelled_u, modelled_v)
    ok = np.isfinite(modelled_speed) & np.isfinite(observed_speed)
    n = int(ok.sum())
    coverage = float(ok.mean()) if len(ok) else 0.0
    if n == 0:
        return Comparison(0, np.inf, np.nan, np.inf, -np.inf, 180.0, 0.0)

    o = observed_speed[ok]
    m = modelled_speed[ok]
    if sigma is not None:
        s = np.asarray(sigma)[ok]
        w = 1.0 / np.maximum(s, 1e-6) ** 2
    else:
        w = np.ones_like(o)
    w = w / w.sum()

    resid = m - o
    rmse = float(np.sqrt(np.sum(w * resid**2)))
    bias = float(np.sum(w * resid))
    mae = float(np.sum(w * np.abs(resid)))
    denom = float(np.sum(w * (o - np.sum(w * o)) ** 2))
    r2 = float(1.0 - np.sum(w * resid**2) / denom) if denom > 1e-12 else -np.inf

    ang = 180.0
    if observed_u is not None and observed_v is not None:
        ou, ov = np.asarray(observed_u)[ok], np.asarray(observed_v)[ok]
        mu, mv = modelled_u[ok], modelled_v[ok]
        no = np.hypot(ou, ov)
        nm = np.hypot(mu, mv)
        good = (no > 1e-9) & (nm > 1e-9)
        if good.any():
            cos = np.clip((ou[good] * mu[good] + ov[good] * mv[good]) / (no[good] * nm[good]), -1, 1)
            ang = float(np.degrees(np.arccos(cos)).mean())

    return Comparison(n, rmse, bias, mae, r2, ang, coverage,
                      matched=ok, observed=o, modelled=m)


def objective_value(cmp: Comparison, w_mag: float, w_dir: float, w_yield: float,
                    vector_yield: float) -> float:
    """Scalar to minimise.

    The yield penalty is not optional. Without it the optimizer finds degenerate
    settings that return three excellent vectors and nothing else, and raising the
    correlation threshold is exactly such a lever.
    """
    if cmp.n == 0:
        return float("inf")
    ang_norm = cmp.angular_error_deg / 180.0
    penalty = w_yield * (1.0 - float(np.clip(vector_yield, 0.0, 1.0)))
    matched_penalty = w_yield * (1.0 - cmp.coverage)
    return w_mag * cmp.rmse + w_dir * ang_norm + penalty + matched_penalty


def diagnose_bias(observed: np.ndarray, modelled: np.ndarray) -> tuple[str, str]:
    """Tell a scale error apart from a velocimetry failure.

    A geometry or scale error multiplies every point by the same factor, so the
    modelled-to-measured ratios cluster tightly and a single scale factor fixes
    them. A velocimetry failure does not: when correlation locks onto something
    that is not the surface, the ratios scatter over orders of magnitude and are
    biased in one direction only.

    Running a parameter search on top of a velocimetry failure is the Tier 0 trap:
    the optimizer will find settings that fit a handful of points by chance and
    report an excellent score for a field that measures the wrong thing.
    """
    obs = np.asarray(observed, float)
    mod = np.asarray(modelled, float)
    ok = np.isfinite(obs) & np.isfinite(mod) & (obs > 1e-6)
    if ok.sum() < 3:
        return "unknown", "too few matched points to diagnose"
    ratio = mod[ok] / obs[ok]
    positive = ratio[ratio > 1e-9]
    if positive.size < 3:
        return "failure", "modelled velocities are essentially zero at the measured points"
    spread = float(positive.max() / positive.min())
    median = float(np.median(positive))

    if spread < 1.6:
        return "scale", (
            f"ratios cluster at {median:.2f} with only a {spread:.2f}x spread, which is a "
            f"scale error. Fit one global scale factor, or check the "
            f"takeoff-to-water-surface offset"
        )
    if spread > 5.0 and median < 0.6:
        return "failure", (
            f"ratios scatter over {spread:.0f}x and are biased low (median {median:.2f}). "
            "That is not a scale error, because a scale error multiplies every point by the "
            "same factor. It is the velocimetry measuring the wrong thing, typically "
            "correlation locking onto a stationary bed seen through clear water. Do not "
            "calibrate on top of this"
        )
    return "mixed", (
        f"ratios scatter {spread:.0f}x around a median of {median:.2f}: part scale, part "
        "scatter. Fix the geometry first, then look at coverage"
    )


def fit_scale(result, wx, wy, observed_speed, radius: float, sigma=None) -> tuple[float, float]:
    """Tier 0.5: one global scale factor, by weighted least squares, alone.

    Returns ``(scale, rmse_after)``. Fit this before any Tier 1 search and freeze
    it: fitting scale and interrogation window together lets them trade off
    against each other, which is the exact failure Tier 0 exists to prevent.
    """
    su, sv = sample_field(result, wx, wy, radius)
    m = np.hypot(su, sv)
    ok = np.isfinite(m) & np.isfinite(observed_speed) & (m > 1e-9)
    if ok.sum() < 2:
        return 1.0, float("inf")
    o = observed_speed[ok]
    mm = m[ok]
    w = np.ones_like(o) if sigma is None else 1.0 / np.maximum(np.asarray(sigma)[ok], 1e-6) ** 2
    scale = float(np.sum(w * o * mm) / np.sum(w * mm * mm))
    rmse = float(np.sqrt(np.sum(w * (scale * mm - o) ** 2) / np.sum(w)))
    return scale, rmse


def quarter_rule_window(expected_speed_ms: float, dt: float, scale_m_per_px: float) -> int:
    """Interrogation window from the quarter rule: displacement ~ window / 4."""
    if scale_m_per_px <= 0 or dt <= 0:
        return 64
    disp_px = expected_speed_ms * dt / scale_m_per_px
    w = 4.0 * disp_px
    for candidate in (16, 24, 32, 48, 64, 96, 128, 192, 256):
        if candidate >= w:
            return candidate
    return 256

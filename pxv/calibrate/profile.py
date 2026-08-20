"""Converting point-gauged velocity to surface velocity.

The ground truth is measured at fixed fractions of the flow depth *above the
riverbed* (0.6 h and 0.8 h). pXv measures velocity at the free surface. These are
different quantities and must be reconciled explicitly.

Note this is not the conventional "0.6 depth" gauging rule, which means 0.6 of
the depth measured downward *from the surface*, that is 0.4 h above the bed, and
which exists precisely because it approximates the depth-averaged velocity.
Measured upward from the bed, 0.6 h and 0.8 h sit high in the profile, so the
correction is small: roughly 6 to 15 % at 0.6 h and 3 to 7 % at 0.8 h, against 15
to 30 % for a depth-averaged value.

Conversion always runs on the ground truth, never on the computed field, so the
exported field stays a true surface field and every converted point carries its
own propagated uncertainty.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

#: u_surface = u1 + TWO_POINT_K * (u2 - u1) for z1 = 0.6h, z2 = 0.8h
TWO_POINT_K = float(np.log(1.0 / 0.6) / np.log(0.8 / 0.6))  # 1.7757


@dataclass
class Conversion:
    u_surface: np.ndarray
    sigma: np.ndarray
    method: np.ndarray        # per-point method label
    z0_estimate: float        # metres, NaN when none could be derived


def two_point_surface(u1: float, u2: float, z1_over_h: float = 0.6, z2_over_h: float = 0.8):
    """Extrapolate two log-profile points to the surface, roughness-free.

    Two points determine a logarithmic profile completely, so neither the
    roughness length nor a separate depth measurement is needed.

    The estimator is unbiased but noisy: ``u2 - u1`` is a small difference between
    two similar numbers, so an independent, equal standard deviation on each input
    is amplified by ``sqrt((1-K)^2 + K^2)``, about 1.94 for 0.6 h and 0.8 h.
    """
    k = np.log(1.0 / z1_over_h) / np.log(z2_over_h / z1_over_h)
    return u1 + k * (u2 - u1), k


def two_point_amplification(z1_over_h: float = 0.6, z2_over_h: float = 0.8) -> float:
    k = np.log(1.0 / z1_over_h) / np.log(z2_over_h / z1_over_h)
    return float(np.hypot(1.0 - k, k))


def z0_from_two_points(u1: float, u2: float, h: float,
                       z1_over_h: float = 0.6, z2_over_h: float = 0.8) -> float:
    """Roughness length implied by two points on the same vertical, in metres."""
    if not np.isfinite(u1) or not np.isfinite(u2) or h <= 0:
        return float("nan")
    denom = np.log(z2_over_h / z1_over_h)
    ustar_kappa = (u2 - u1) / denom
    if ustar_kappa <= 0:
        return float("nan")
    z1 = z1_over_h * h
    return float(z1 * np.exp(-u1 / ustar_kappa))


def log_ratio(z_over_h: float, h: float, z0: float) -> float:
    """``u(z) / u_surface`` for a logarithmic profile."""
    if h <= 0 or z0 <= 0 or z0 >= h:
        return float("nan")
    return float(1.0 + np.log(z_over_h) / np.log(h / z0))


def single_point_surface(u: float, z_over_h: float, h: float, z0: float) -> float:
    r = log_ratio(z_over_h, h, z0)
    if not np.isfinite(r) or r <= 0:
        return float("nan")
    return u / r


def roughness_sensitivity(z_over_h: float, h: float,
                          z0_lo: float, z0_hi: float) -> tuple[float, float]:
    """Conversion-ratio band over a plausible roughness range.

    A badly chosen roughness is a bounded error, not an open-ended one: across
    h/z0 from 30 to 3000 the ratio moves by about 4 % at 0.8 h and 9 % at 0.6 h.
    """
    return log_ratio(z_over_h, h, z0_hi), log_ratio(z_over_h, h, z0_lo)


def convert_to_surface(
    speed: np.ndarray,
    z_over_h: np.ndarray,
    depth: np.ndarray,
    sigma: np.ndarray | None = None,
    vertical_id: np.ndarray | None = None,
    method: str = "hybrid",
    z0_fallback: float = 0.005,
) -> Conversion:
    """Convert gauged speeds to surface speeds.

    ``hybrid`` is the operative case when vertical coverage varies: use the
    two-point verticals to *estimate* the site roughness, take a robust central
    value, then apply that single smoothed roughness to every point. This keeps
    the two-point method's freedom from roughness assumptions while avoiding its
    noise amplification, and it matters because the GeoPackage carries no grain
    size, so the two-point verticals are the only physical source of roughness
    information at the site.
    """
    speed = np.asarray(speed, np.float64)
    z_over_h = np.asarray(z_over_h, np.float64)
    depth = np.asarray(depth, np.float64)
    n = len(speed)
    sigma = np.zeros(n) if sigma is None else np.asarray(sigma, np.float64)

    out = np.full(n, np.nan)
    out_sigma = np.full(n, np.nan)
    labels = np.array(["none"] * n, dtype=object)

    if method == "none":
        return Conversion(speed.copy(), sigma.copy(), np.array(["none"] * n, dtype=object), float("nan"))

    # --- gather two-point verticals ---------------------------------------
    pairs: dict = {}
    if vertical_id is not None:
        vid = np.asarray(vertical_id)
        for key in np.unique(vid):
            idx = np.nonzero(vid == key)[0]
            if len(idx) < 2:
                continue
            order = idx[np.argsort(z_over_h[idx])]
            lo, hi = order[0], order[-1]
            if z_over_h[hi] - z_over_h[lo] > 1e-3:
                pairs[key] = (lo, hi)

    z0_estimates = [
        z0_from_two_points(speed[lo], speed[hi], depth[lo], z_over_h[lo], z_over_h[hi])
        for lo, hi in pairs.values()
    ]
    z0_estimates = [z for z in z0_estimates if np.isfinite(z) and z > 0]
    z0_site = float(np.median(z0_estimates)) if z0_estimates else float(z0_fallback)

    amp = None
    # --- per-point conversion ---------------------------------------------
    if method in ("two_point", "hybrid"):
        for key, (lo, hi) in pairs.items():
            us, k = two_point_surface(speed[lo], speed[hi], z_over_h[lo], z_over_h[hi])
            amp = float(np.hypot(1.0 - k, k))
            if method == "two_point":
                for i in (lo, hi):
                    out[i] = us
                    out_sigma[i] = amp * max(sigma[lo], sigma[hi])
                    labels[i] = "two_point"

    for i in range(n):
        if np.isfinite(out[i]):
            continue
        z0 = z0_site
        us = single_point_surface(speed[i], z_over_h[i], depth[i], z0)
        if np.isfinite(us):
            out[i] = us
            r = log_ratio(z_over_h[i], depth[i], z0)
            lo_r, hi_r = roughness_sensitivity(z_over_h[i], depth[i], z0 / 10.0, z0 * 10.0)
            band = abs(speed[i] / max(lo_r, 1e-6) - speed[i] / max(hi_r, 1e-6)) / 2.0
            out_sigma[i] = float(np.hypot(sigma[i] / max(r, 1e-6), band))
            labels[i] = "log_single" if method != "hybrid" else "hybrid"
        else:
            out[i] = speed[i]
            out_sigma[i] = sigma[i]
            labels[i] = "unconverted"

    return Conversion(out, out_sigma, labels, z0_site)

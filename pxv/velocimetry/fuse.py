"""Binning and PIV/PTV fusion onto a common output grid.

Fusion is not "run both and average". PIV supplies a robust dense field, PTV
supplies scattered vectors at higher spatial resolution, and each output cell
records which engine actually produced it.
"""

from __future__ import annotations

import warnings
from dataclasses import dataclass

import numpy as np

from pxv.velocimetry.piv import PIVField

SOURCE_NONE, SOURCE_PIV, SOURCE_PTV, SOURCE_INTERP, SOURCE_PIV_CORRECTED = 0, 1, 2, 3, 4


@dataclass
class VelocityField:
    """Time-averaged field on a regular pixel grid."""

    x: np.ndarray            # cell centre, image pixels
    y: np.ndarray
    u: np.ndarray            # pixels per frame interval
    v: np.ndarray
    n: np.ndarray            # samples per cell
    source: np.ndarray       # SOURCE_* per cell
    cell_px: float = 1.0

    def magnitude(self) -> np.ndarray:
        return np.hypot(self.u, self.v)

    def direction_deg(self) -> np.ndarray:
        """Compass-style bearing, 0 = +y up-image, increasing clockwise."""
        return (np.degrees(np.arctan2(self.u, -self.v)) + 360.0) % 360.0

    @property
    def valid(self) -> np.ndarray:
        return (self.n > 0) | (self.source == SOURCE_INTERP)

    def coverage(self) -> float:
        return float(self.valid.mean()) if self.valid.size else 0.0

    @property
    def measured(self) -> np.ndarray:
        """Cells backed by an actual measurement, excluding interpolated fill."""
        return (self.n > 0) & (self.source != SOURCE_INTERP)

    def summary(self) -> str:
        m = self.magnitude()[self.valid]
        if m.size == 0:
            return "field: empty"
        npiv = int((self.source == SOURCE_PIV).sum())
        nptv = int((self.source == SOURCE_PTV).sum())
        nint = int((self.source == SOURCE_INTERP).sum())
        ncor = int((self.source == SOURCE_PIV_CORRECTED).sum())
        extra = "".join([f", PIV corrected {ncor}" if ncor else "",
                         f", interpolated {nint}" if nint else ""])
        return (
            f"field: {self.u.shape[1]}x{self.u.shape[0]} cells, coverage {self.coverage():.0%} "
            f"(measured {self.measured.mean():.0%}), "
            f"|v| median {np.median(m):.3f} p95 {np.percentile(m, 95):.3f} px/interval "
            f"| cells from PIV {npiv}, PTV {nptv}{extra}"
        )


def _grid_axes(shape: tuple[int, int], cell: int) -> tuple[np.ndarray, np.ndarray]:
    h, w = shape
    nx = max(1, w // cell)
    ny = max(1, h // cell)
    x = (np.arange(nx) + 0.5) * cell
    y = (np.arange(ny) + 0.5) * cell
    return x, y


def bin_scatter(
    shape: tuple[int, int],
    cell: int,
    px, py, pu, pv,
) -> tuple[np.ndarray, ...]:
    """Median-bin scattered vectors onto the grid. Median resists stray mismatches."""
    x, y = _grid_axes(shape, cell)
    nx, ny = len(x), len(y)
    px = np.asarray(px); py = np.asarray(py)
    pu = np.asarray(pu); pv = np.asarray(pv)
    ix = np.clip((px // cell).astype(int), 0, nx - 1)
    iy = np.clip((py // cell).astype(int), 0, ny - 1)
    flat = iy * nx + ix

    u = np.full(nx * ny, np.nan, np.float32)
    v = np.full(nx * ny, np.nan, np.float32)
    n = np.zeros(nx * ny, np.int32)
    order = np.argsort(flat, kind="stable")
    fs, us, vs = flat[order], pu[order], pv[order]
    bounds = np.searchsorted(fs, np.arange(nx * ny + 1))
    for c in range(nx * ny):
        lo, hi = bounds[c], bounds[c + 1]
        if hi > lo:
            n[c] = hi - lo
            u[c] = np.median(us[lo:hi])
            v[c] = np.median(vs[lo:hi])
    return (u.reshape(ny, nx), v.reshape(ny, nx), n.reshape(ny, nx), x, y)


def average_piv(fields: list[PIVField]) -> tuple[np.ndarray, ...]:
    """Median over time of a sequence of PIV fields, honouring per-vector validity."""
    if not fields:
        raise ValueError("no PIV fields to average")
    us = np.stack([np.where(f.valid, f.u, np.nan) for f in fields])
    vs = np.stack([np.where(f.valid, f.v, np.nan) for f in fields])
    with np.errstate(invalid="ignore"), warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        u = np.nanmedian(us, axis=0)
        v = np.nanmedian(vs, axis=0)
    n = np.sum(np.isfinite(us), axis=0).astype(np.int32)
    return u.astype(np.float32), v.astype(np.float32), n, fields[0].x, fields[0].y


def fuse(
    shape: tuple[int, int],
    cell: int,
    piv_fields: list[PIVField] | None,
    tracks=None,
    min_samples: int = 3,
    piv_drop: np.ndarray | None = None,
    piv_classes: np.ndarray | None = None,
    piv_threshold_px: float = 0.5,
) -> VelocityField:
    """Combine PIV and PTV onto one grid, preferring PTV where it has support."""
    x, y = _grid_axes(shape, cell)
    nx, ny = len(x), len(y)
    xx, yy = np.meshgrid(x, y)

    u = np.full((ny, nx), np.nan, np.float32)
    v = np.full((ny, nx), np.nan, np.float32)
    n = np.zeros((ny, nx), np.int32)
    src = np.zeros((ny, nx), np.uint8)

    if piv_fields:
        if piv_classes is not None:
            pu, pv, pn, pgx, pgy = average_piv_moving(piv_fields, piv_classes,
                                                      piv_threshold_px)
        else:
            pu, pv, pn, pgx, pgy = average_piv(piv_fields)
        if piv_drop is not None and piv_drop.shape == pn.shape:
            pn = np.where(piv_drop, 0, pn)
        gu, gv, gn, _, _ = bin_scatter(
            shape, cell, pgx.ravel(), pgy.ravel(),
            np.where(pn > 0, pu, np.nan).ravel(), np.where(pn > 0, pv, np.nan).ravel(),
        )
        take = np.isfinite(gu) & np.isfinite(gv)
        u[take], v[take] = gu[take], gv[take]
        n[take] = np.maximum(gn[take], 1)
        src[take] = SOURCE_PIV

    if tracks is not None and tracks.n > 0:
        tx, ty, tu, tv, _ = tracks.stacked()
        bu, bv, bn, _, _ = bin_scatter(shape, cell, tx, ty, tu, tv)
        take = np.isfinite(bu) & (bn >= min_samples)
        u[take], v[take] = bu[take], bv[take]
        n[take] = bn[take]
        src[take] = SOURCE_PTV

    n[~np.isfinite(u)] = 0
    src[~np.isfinite(u)] = SOURCE_NONE
    return VelocityField(x=xx, y=yy, u=u, v=v, n=n, source=src, cell_px=float(cell))


CELL_CLEAN, CELL_MIXED, CELL_STATIC, CELL_EMPTY = 0, 1, 2, 3
CELL_NAMES = ("clean", "mixed", "static", "empty")


def classify_piv_cells(fields: list[PIVField], threshold_px: float = 0.5,
                       moving_min: float = 0.25, clean_min: float = 0.85):
    """Classify each PIV cell from the temporal structure of its own vectors.

    A displacement threshold alone cannot separate a stationary bed from genuinely
    slow water: both report small numbers. What separates them is *how* they
    report them over time.

    - **clean**: nearly every pair gives a moving vector. The surface won.
    - **static**: nearly every pair gives a stationary vector. Either a bed seen
      through clear water, or a genuinely still corner.
    - **mixed**: the cell alternates. Correlation is locking onto the bed in some
      pairs and onto the surface in others, because the two compete inside the
      same window.

    Mixed cells are the source of the low bias. Taking a median over all their
    samples averages true surface velocities together with bed zeros and lands
    between the two, so the cell reports a speed that was never observed. The
    remedy is not to drop them, which costs coverage, but to estimate them from
    their moving samples alone. See :func:`average_piv_moving`.

    Returns ``(classification, diagnostics)``.
    """
    if not fields:
        return np.zeros((0, 0), np.uint8), {}
    mags = np.stack([np.where(f.valid, np.hypot(f.u, f.v), np.nan) for f in fields])
    finite = np.isfinite(mags)
    n_valid = finite.sum(axis=0)
    with np.errstate(invalid="ignore"):
        n_moving = np.sum(np.nan_to_num(mags, nan=-1.0) >= threshold_px, axis=0)
    with np.errstate(divide="ignore", invalid="ignore"):
        moving_fraction = np.where(n_valid > 0, n_moving / np.maximum(n_valid, 1), 0.0)

    cls = np.full(n_valid.shape, CELL_EMPTY, np.uint8)
    has = n_valid > 0
    cls[has & (moving_fraction >= clean_min)] = CELL_CLEAN
    cls[has & (moving_fraction >= moving_min) & (moving_fraction < clean_min)] = CELL_MIXED
    cls[has & (moving_fraction < moving_min)] = CELL_STATIC

    diagnostics = {
        "moving_fraction": moving_fraction,
        "n_valid": n_valid,
        "counts": {name: int((cls == i).sum()) for i, name in enumerate(CELL_NAMES)},
    }
    # zero-lag correlation, where the engine reported it: the direct fingerprint
    # of a stationary pattern competing inside the window
    zl = [f for f in fields if f.zero_lag is not None]
    if zl:
        ratio = np.stack([
            np.where(f.valid & (f.corr > 1e-6), f.zero_lag / np.maximum(f.corr, 1e-6), np.nan)
            for f in zl
        ])
        with np.errstate(invalid="ignore"), warnings.catch_warnings():
            warnings.simplefilter("ignore", RuntimeWarning)
            diagnostics["zero_lag_ratio"] = np.nanmedian(ratio, axis=0)
    return cls, diagnostics


def average_piv_moving(fields: list[PIVField], classification: np.ndarray,
                       threshold_px: float = 0.5, min_moving: int = 3):
    """Time-median of the PIV series, using only the moving samples in mixed cells.

    In a clean cell every sample is a surface measurement, so the plain median is
    right. In a mixed cell the samples are drawn from two populations, the surface
    and the bed, and the median sits between them. Selecting the moving population
    recovers what the surface was doing, at the cost of assuming the near-zero
    samples are the bed rather than the flow briefly stopping.

    That assumption is exactly what the classifier established: a cell only
    reaches ``mixed`` because it *also* produces clearly moving vectors.
    """
    if not fields:
        raise ValueError("no PIV fields to average")
    us = np.stack([np.where(f.valid, f.u, np.nan) for f in fields])
    vs = np.stack([np.where(f.valid, f.v, np.nan) for f in fields])
    mags = np.hypot(us, vs)

    select = np.isfinite(us)
    mixed = classification == CELL_MIXED
    if mixed.any():
        moving = np.nan_to_num(mags, nan=-1.0) >= threshold_px
        select = np.where(mixed[None, :, :], select & moving, select)

    keep_u = np.where(select, us, np.nan)
    keep_v = np.where(select, vs, np.nan)
    with np.errstate(invalid="ignore"), warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        u = np.nanmedian(keep_u, axis=0)
        v = np.nanmedian(keep_v, axis=0)
    n = np.sum(select, axis=0).astype(np.int32)

    drop = (classification == CELL_STATIC) | (classification == CELL_EMPTY)
    drop |= (classification == CELL_MIXED) & (n < min_moving)
    n = np.where(drop, 0, n)
    return u.astype(np.float32), v.astype(np.float32), n, fields[0].x, fields[0].y


def piv_static_cells(fields: list[PIVField], threshold_px: float = 0.5,
                     min_fraction: float = 0.6) -> np.ndarray:
    """Per-cell test for correlation locked onto something stationary.

    Bed-locking is spatial, not global: over clear water the bed wins, over
    rippled water the surface does, and both happen in one frame. A single
    whole-field verdict therefore throws away the cells that work. This marks a
    cell static when it reports a displacement below ``threshold_px`` in at least
    ``min_fraction`` of the pairs in which it produced a vector at all.

    Persistence is what distinguishes the two cases. Genuinely slow water
    fluctuates; a stationary bed does not.
    """
    if not fields:
        return np.zeros((0, 0), bool)
    mags = np.stack([np.where(f.valid, np.hypot(f.u, f.v), np.nan) for f in fields])
    with np.errstate(invalid="ignore"):
        n_valid = np.sum(np.isfinite(mags), axis=0)
        n_still = np.sum(np.nan_to_num(mags, nan=np.inf) < threshold_px, axis=0)
    with np.errstate(divide="ignore", invalid="ignore"):
        fraction = np.where(n_valid > 0, n_still / np.maximum(n_valid, 1), 0.0)
    return (n_valid > 0) & (fraction >= min_fraction)


def correct_piv_with_ptv(field: "VelocityField", min_overlap: int = 4,
                         max_gain: float = 4.0) -> tuple["VelocityField", dict]:
    """Rescale the PIV cells using the PTV cells that overlap them.

    This is the correction step of "PIV first, then correct with PTV". PIV
    supplies coverage and PTV supplies accuracy, so where the two coincide their
    ratio measures how much PIV is losing, and that gain can be carried to the
    PIV-only cells.

    A single gain is fitted rather than a per-cell one: with a handful of
    overlapping cells anything richer would fit noise. Corrected cells are tagged
    so they are never confused with either a raw PIV or a PTV measurement.
    """
    report = {"overlap": 0, "gain": 1.0, "applied": 0, "reason": ""}
    piv = field.source == SOURCE_PIV
    ptv = field.source == SOURCE_PTV
    if not piv.any() or not ptv.any():
        report["reason"] = "no PIV and PTV cells to compare"
        return field, report

    # neighbouring cells count as overlapping: the two engines rarely fall in the
    # same cell, but they do fall next to each other
    from scipy.ndimage import binary_dilation

    near_ptv = binary_dilation(ptv, np.ones((3, 3), bool))
    both = piv & near_ptv
    report["overlap"] = int(both.sum())
    if both.sum() < min_overlap:
        report["reason"] = (
            f"only {int(both.sum())} PIV cells sit next to a PTV cell, "
            f"below min_overlap={min_overlap}"
        )
        return field, report

    # local PTV speed beside each overlapping PIV cell
    from scipy.spatial import cKDTree

    ptv_pts = np.column_stack([field.x[ptv], field.y[ptv]])
    ptv_speed = np.hypot(field.u[ptv], field.v[ptv])
    tree = cKDTree(ptv_pts)
    target = np.column_stack([field.x[both], field.y[both]])
    _, idx = tree.query(target, k=1)
    reference = ptv_speed[idx]
    modelled = np.hypot(field.u[both], field.v[both])

    denom = float(np.sum(modelled * modelled))
    if denom < 1e-12:
        report["reason"] = "PIV reports no motion where PTV does; nothing to rescale"
        return field, report
    gain = float(np.sum(modelled * reference) / denom)
    if not np.isfinite(gain) or not (1.0 / max_gain <= gain <= max_gain):
        report["gain"] = gain
        report["reason"] = (
            f"gain {gain:.2f} is outside 1/{max_gain:g} to {max_gain:g}; PIV and PTV "
            "are measuring different things, so no correction was applied"
        )
        return field, report

    u = field.u.copy()
    v = field.v.copy()
    source = field.source.copy()
    u[piv] *= gain
    v[piv] *= gain
    source[piv] = SOURCE_PIV_CORRECTED
    report["gain"] = gain
    report["applied"] = int(piv.sum())
    return (VelocityField(x=field.x, y=field.y, u=u, v=v, n=field.n, source=source,
                          cell_px=field.cell_px), report)


def interpolate_gaps(field: "VelocityField", radius_px: float, power: float = 2.0,
                    min_neighbours: int = 2) -> "VelocityField":
    """Fill cells that lie within ``radius_px`` of a measured cell.

    A sparse tracer field is a set of vectors along the paths the tracers took.
    Binning it leaves isolated cells, and a streamline that cannot step from one
    cell to the next stops immediately, which is why a real measurement can render
    as a handful of stubs.

    Filling the gaps between nearby measurements makes the streamlines continuous
    over the area that was actually observed. The radius bounds how far that goes,
    and the filled cells are tagged ``SOURCE_INTERP`` so they are never mistaken
    for measurements: they carry no sample count and are excluded from ``measured``.
    """
    if radius_px <= 0:
        return field
    src = field.measured
    if not src.any():
        return field

    from scipy.spatial import cKDTree

    known = np.column_stack([field.x[src], field.y[src]])
    tree = cKDTree(known)
    ku = field.u[src]
    kv = field.v[src]

    target = ~src
    if not target.any():
        return field
    tx = field.x[target]
    ty = field.y[target]
    neighbours = tree.query_ball_point(np.column_stack([tx, ty]), r=float(radius_px))

    u = field.u.copy()
    v = field.v.copy()
    source = field.source.copy()
    filled_u = np.full(len(tx), np.nan)
    filled_v = np.full(len(tx), np.nan)
    for i, ids in enumerate(neighbours):
        if len(ids) < min_neighbours:
            continue
        d = np.hypot(known[ids, 0] - tx[i], known[ids, 1] - ty[i])
        w = 1.0 / np.maximum(d, 1e-6) ** power
        w /= w.sum()
        filled_u[i] = float(np.sum(w * ku[ids]))
        filled_v[i] = float(np.sum(w * kv[ids]))

    ok = np.isfinite(filled_u)
    idx = np.nonzero(target)
    rows, cols = idx[0][ok], idx[1][ok]
    u[rows, cols] = filled_u[ok]
    v[rows, cols] = filled_v[ok]
    source[rows, cols] = SOURCE_INTERP
    n = field.n.copy()          # interpolated cells keep a zero sample count
    out = VelocityField(x=field.x, y=field.y, u=u, v=v, n=n, source=source,
                        cell_px=field.cell_px)
    return out


def seeding_density(detections_per_frame: float, image_shape: tuple[int, int], window: int) -> float:
    """Detectable objects per interrogation window.

    Above roughly 8, PIV correlation is well posed. Between 1 and 8, PTV or
    ensemble PIV does better. Below 1, only PTV works.
    """
    h, w = image_shape
    return float(detections_per_frame) * (window * window) / float(h * w)


def static_fraction(field: "VelocityField", threshold_px: float = 0.3) -> float:
    """Share of valid cells whose time-averaged displacement is indistinguishable
    from zero.

    The seeding-density diagnostic counts detectable objects but cannot tell a
    drifting tracer from a stationary stone. Over clear shallow water the bed is
    visible through the surface and is the strongest texture in the frame, so
    correlation locks onto it and reports zero. A high static fraction over what
    should be moving water is the signal that this is happening, and no amount of
    parameter tuning will fix it: the bed really is the dominant signal.
    """
    src = field.measured
    if not src.any():
        return 1.0
    mag = field.magnitude()[src]
    return float(np.mean(mag < threshold_px))


def assess_static(fraction: float) -> tuple[str, str]:
    if fraction < 0.15:
        return "ok", f"{fraction:.0%} of cells are static, which is normal for banks and structures"
    if fraction < 0.40:
        return "check", (
            f"{fraction:.0%} of cells report no motion. Check that the analysis region "
            "excludes banks and bars"
        )
    return "suspect", (
        f"{fraction:.0%} of cells report no motion. Over clear water this usually means "
        "correlation is locking onto the stationary bed seen through the surface rather "
        "than onto the surface itself. Seeding, a shorter background window, or a region "
        "restricted to rippled water are the ways out"
    )


def recommend_engine(density: float) -> tuple[str, str]:
    if density >= 8.0:
        return "piv", f"{density:.1f} objects per window: dense, PIV correlation is well posed"
    if density >= 1.0:
        return "combined", (
            f"{density:.1f} objects per window: sparse for PIV, PIV-guided PTV or ensemble PIV "
            "will do better"
        )
    return "ptv", f"{density:.2f} objects per window: too sparse for correlation, use PTV"

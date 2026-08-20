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

SOURCE_NONE, SOURCE_PIV, SOURCE_PTV = 0, 1, 2


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
        return self.n > 0

    def coverage(self) -> float:
        return float(self.valid.mean()) if self.valid.size else 0.0

    def summary(self) -> str:
        m = self.magnitude()[self.valid]
        if m.size == 0:
            return "field: empty"
        npiv = int((self.source == SOURCE_PIV).sum())
        nptv = int((self.source == SOURCE_PTV).sum())
        return (
            f"field: {self.u.shape[1]}x{self.u.shape[0]} cells, coverage {self.coverage():.0%}, "
            f"|v| median {np.median(m):.3f} p95 {np.percentile(m, 95):.3f} px/interval "
            f"| cells from PIV {npiv}, PTV {nptv}"
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
        pu, pv, pn, pgx, pgy = average_piv(piv_fields)
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


def seeding_density(detections_per_frame: float, image_shape: tuple[int, int], window: int) -> float:
    """Detectable objects per interrogation window.

    Above roughly 8, PIV correlation is well posed. Between 1 and 8, PTV or
    ensemble PIV does better. Below 1, only PTV works.
    """
    h, w = image_shape
    return float(detections_per_frame) * (window * window) / float(h * w)


def recommend_engine(density: float) -> tuple[str, str]:
    if density >= 8.0:
        return "piv", f"{density:.1f} objects per window: dense, PIV correlation is well posed"
    if density >= 1.0:
        return "combined", (
            f"{density:.1f} objects per window: sparse for PIV, PIV-guided PTV or ensemble PIV "
            "will do better"
        )
    return "ptv", f"{density:.2f} objects per window: too sparse for correlation, use PTV"

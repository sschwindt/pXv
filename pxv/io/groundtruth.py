"""Reading ground-truth velocity points from a GeoPackage.

The reader validates that the attributes required by the chosen profile
conversion are actually present and refuses to calibrate against points that lack
them, rather than silently substituting defaults.

Instrument note: a SonTek FlowTracker2 reports 2D or 3D components plus a
per-measurement standard error, so both the angular term of the objective and
per-point weighting are available. Its specification accuracy is 1 % of reading
with a 0.25 cm/s floor; where the file carries a measured standard error, that is
used in preference because turbulence normally dominates the instrument floor.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from pxv.project import GroundTruthCfg

#: FlowTracker2 specification: 1 % of reading, floor 0.25 cm/s
FT2_REL_ACCURACY = 0.01
FT2_FLOOR_MS = 0.0025


@dataclass
class GroundTruth:
    x: np.ndarray                       # world coordinates
    y: np.ndarray
    speed: np.ndarray                   # as measured, m/s
    u: np.ndarray | None = None
    v: np.ndarray | None = None
    depth: np.ndarray | None = None
    z_over_h: np.ndarray | None = None
    sigma: np.ndarray | None = None
    vertical_id: np.ndarray | None = None
    epsg: int = 0
    warnings: list[str] = field(default_factory=list)

    def __len__(self) -> int:
        return len(self.x)

    @property
    def has_direction(self) -> bool:
        return self.u is not None and self.v is not None

    def describe(self) -> str:
        lines = [f"ground truth: {len(self)} points, epsg {self.epsg or 'unknown'}"]
        lines.append(f"  speed {np.nanmin(self.speed):.3f} to {np.nanmax(self.speed):.3f} m/s")
        lines.append(f"  direction: {'yes' if self.has_direction else 'magnitude only'}")
        if self.z_over_h is not None:
            uniq = np.unique(np.round(self.z_over_h, 3))
            lines.append(f"  measurement heights z/h: {list(uniq)}")
        if self.vertical_id is not None:
            _, counts = np.unique(self.vertical_id, return_counts=True)
            lines.append(
                f"  verticals: {len(counts)} total, {(counts >= 2).sum()} with two or more heights"
            )
        if self.sigma is not None:
            lines.append(f"  sigma median {np.nanmedian(self.sigma):.4f} m/s")
        for w in self.warnings:
            lines.append(f"  warning: {w}")
        return "\n".join(lines)


def default_sigma(speed: np.ndarray) -> np.ndarray:
    """FlowTracker2 specification accuracy, used when the file carries no error column."""
    return np.maximum(np.abs(speed) * FT2_REL_ACCURACY, FT2_FLOOR_MS)


def read_groundtruth(cfg: GroundTruthCfg) -> GroundTruth:
    if not cfg.path:
        raise ValueError("groundtruth.path is empty")
    path = Path(cfg.path)
    if not path.exists():
        raise FileNotFoundError(f"ground-truth file not found: {path}")
    try:
        import geopandas as gpd
    except ImportError as exc:  # pragma: no cover
        raise ImportError("reading a GeoPackage needs geopandas; pip install 'pxv[geo]'") from exc

    gdf = gpd.read_file(path, layer=cfg.layer or None)
    if gdf.empty:
        raise ValueError(f"{path} contains no features")

    warnings: list[str] = []
    geom = gdf.geometry
    if not (geom.geom_type == "Point").all():
        warnings.append("non-point geometries present; using representative points")
        geom = geom.representative_point()

    x = geom.x.to_numpy(dtype=float)
    y = geom.y.to_numpy(dtype=float)
    epsg = 0
    if gdf.crs is not None:
        code = gdf.crs.to_epsg()
        epsg = int(code) if code else 0

    def col(name: str):
        return gdf[name].to_numpy(dtype=float) if name and name in gdf.columns else None

    u = col(cfg.col_u)
    v = col(cfg.col_v)
    speed = col(cfg.col_speed)
    if speed is None:
        if u is None or v is None:
            raise ValueError(
                f"{path} has neither a speed column ({cfg.col_speed!r}) nor "
                f"components ({cfg.col_u!r}, {cfg.col_v!r})"
            )
        speed = np.hypot(u, v)

    depth = col(cfg.col_depth)
    z_over_h = col(cfg.col_z_over_h)
    sigma = col(cfg.col_sigma)
    if sigma is None:
        sigma = default_sigma(speed)
        warnings.append(
            f"no {cfg.col_sigma!r} column; using the FlowTracker2 specification "
            "(1 % of reading, floor 0.25 cm/s)"
        )

    vertical_id = None
    if cfg.col_vertical and cfg.col_vertical in gdf.columns:
        vertical_id = gdf[cfg.col_vertical].to_numpy()

    if cfg.conversion != "none":
        if depth is None:
            warnings.append(
                f"no depth column {cfg.col_depth!r}; the single-point log conversion is unavailable"
            )
        if z_over_h is None:
            warnings.append(
                f"no {cfg.col_z_over_h!r} column; cannot tell 0.6 h from 0.8 h, conversion disabled"
            )
        if vertical_id is None:
            warnings.append(
                f"no {cfg.col_vertical!r} column; cannot pair heights on a vertical, "
                "so the roughness-free two-point method is unavailable"
            )

    return GroundTruth(
        x=x, y=y, speed=speed, u=u, v=v, depth=depth, z_over_h=z_over_h,
        sigma=sigma, vertical_id=vertical_id, epsg=epsg, warnings=warnings,
    )

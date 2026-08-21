"""Reading ground-truth velocity points from a GeoPackage.

The reader validates that the attributes required by the chosen profile
conversion are actually present and refuses to calibrate against points that lack
them, rather than silently substituting defaults.

Instrument note: a SonTek FlowTracker2 reports 2D or 3D components, so both the
angular term of the objective and per-point weighting are available. Its
specification accuracy is 1 % of reading with a 0.25 cm/s floor, but in a natural
stream turbulence dominates that by a wide margin, so the default working figure
is ``sigma_rel = 0.05``. A measured standard error in the file always wins.

Measurement height: standard hydrometric gauging reports depth measured *downward
from the water surface*, which is why 0.2, 0.6 and 0.8 recur; those are the
classic two-point and six-tenths positions. Height above the bed is then
``1 - meas/total``. The convention is configurable because getting it backwards
inverts the velocity profile.
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
        if self.depth is not None:
            lines.append(f"  depth {np.nanmin(self.depth):.3f} to {np.nanmax(self.depth):.3f} m")
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


def default_sigma(speed: np.ndarray, rel: float = FT2_REL_ACCURACY,
                  floor: float = FT2_FLOOR_MS) -> np.ndarray:
    """Per-point uncertainty when the file carries no measured standard error."""
    return np.maximum(np.abs(speed) * float(rel), float(floor))


def verticals_from_geometry(x: np.ndarray, y: np.ndarray, tol: float = 0.1) -> np.ndarray:
    """Label points that share a location, so paired heights can be found.

    Without this the roughness-free two-point method is unavailable, and the
    two-point verticals are usually the only physical source of roughness
    information at a site.
    """
    labels = np.full(len(x), -1, dtype=int)
    nxt = 0
    for i in range(len(x)):
        if labels[i] >= 0:
            continue
        near = (np.hypot(x - x[i], y - y[i]) <= tol) & (labels < 0)
        labels[near] = nxt
        nxt += 1
    return np.array([f"V{n:03d}" for n in labels])


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

    # derive the measurement height from a gauging-style depth pair
    if z_over_h is None:
        meas = col(cfg.col_meas_depth)
        total = col(cfg.col_total_depth)
        if meas is not None and total is not None:
            with np.errstate(divide="ignore", invalid="ignore"):
                frac = np.where(total > 0, meas / total, np.nan)
            if cfg.depth_convention == "below_surface":
                z_over_h = 1.0 - frac
                warnings.append(
                    f"measurement height from {cfg.col_meas_depth!r}/{cfg.col_total_depth!r} "
                    "read as depth below the water surface, so z/h above bed = 1 - ratio"
                )
            else:
                z_over_h = frac
                warnings.append(
                    f"measurement height from {cfg.col_meas_depth!r}/{cfg.col_total_depth!r} "
                    "read as height above the bed"
                )
            if depth is None:
                depth = total

    sigma = col(cfg.col_sigma)
    if sigma is None:
        sigma = default_sigma(speed, cfg.sigma_rel, cfg.sigma_floor_ms)
        warnings.append(
            f"no {cfg.col_sigma!r} column; using sigma_rel={cfg.sigma_rel:.0%} of reading "
            f"with a {cfg.sigma_floor_ms * 100:.2f} cm/s floor"
        )

    vertical_id = None
    if cfg.col_vertical and cfg.col_vertical in gdf.columns:
        vertical_id = gdf[cfg.col_vertical].to_numpy()
    elif cfg.vertical_from_geometry:
        vertical_id = verticals_from_geometry(x, y, cfg.vertical_tolerance_m)
        n_paired = sum(c >= 2 for c in np.unique(vertical_id, return_counts=True)[1])
        warnings.append(
            f"verticals grouped by position within {cfg.vertical_tolerance_m} m: "
            f"{len(np.unique(vertical_id))} verticals, {n_paired} with two or more heights"
        )

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
        if z_over_h is not None:
            uniq = np.unique(np.round(z_over_h[np.isfinite(z_over_h)], 2))
            if uniq.size and uniq.min() < 0.0:
                warnings.append(
                    "some measurement heights are negative; the depth convention is "
                    "probably inverted"
                )

    return GroundTruth(
        x=x, y=y, speed=speed, u=u, v=v, depth=depth, z_over_h=z_over_h,
        sigma=sigma, vertical_id=vertical_id, epsg=epsg, warnings=warnings,
    )

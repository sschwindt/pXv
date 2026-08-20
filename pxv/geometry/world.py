"""Pixel-to-metre geometry.

Four routes, in descending order of reliability:

``gcp`` / ``orthophoto``
    A projective homography fitted over all control points with RANSAC, with
    per-point residuals reported. Preferred whenever control is available.
``known_distance``
    Two image points spanning a known real length. Exact for a nadir view of a
    flat surface and by far the best option in a flume, where the channel width
    is known to the millimetre.
``height``
    Nadir scaling from flight height and field of view. Ground sampling distance
    is linear in height, so the takeoff-to-water-surface offset dominates the
    error budget.
``none``
    Image coordinates, pixels per frame.

Displacements are always converted by transforming both endpoints, never by
scaling the vector, so a projective transform stays correct away from the centre.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import cv2
import numpy as np

from pxv.project import GeometryCfg
from pxv.stabilize.homography import condition_number


@dataclass
class GeoTransform:
    """Maps image pixels to world units."""

    mode: str = "none"
    epsg: int = 0
    #: 3x3 image-to-world matrix; for uniform scaling this is a similarity
    matrix: np.ndarray = field(default_factory=lambda: np.eye(3))
    #: metres per pixel; NaN when the transform is not uniform
    scale_m_per_px: float = float("nan")
    #: per-control-point residuals in world units
    residuals: np.ndarray = field(default_factory=lambda: np.zeros(0))
    condition: float = float("nan")
    fallback_used: bool = False
    notes: list[str] = field(default_factory=list)

    @property
    def georeferenced(self) -> bool:
        return self.epsg > 0 and self.mode in ("gcp", "orthophoto")

    @property
    def units(self) -> str:
        return "px" if self.mode == "none" else "m"

    def to_world(self, x, y) -> tuple[np.ndarray, np.ndarray]:
        pts = np.column_stack([np.asarray(x, np.float64).ravel(),
                               np.asarray(y, np.float64).ravel()])
        out = cv2.perspectiveTransform(pts.reshape(-1, 1, 2), self.matrix).reshape(-1, 2)
        shape = np.asarray(x).shape
        return out[:, 0].reshape(shape), out[:, 1].reshape(shape)

    def displacement_to_world(self, x, y, du, dv) -> tuple[np.ndarray, np.ndarray]:
        """Convert a pixel displacement anchored at (x, y) into world units."""
        x0, y0 = self.to_world(x, y)
        x1, y1 = self.to_world(np.asarray(x) + np.asarray(du), np.asarray(y) + np.asarray(dv))
        return x1 - x0, y1 - y0

    def describe(self) -> str:
        lines = [f"geometry: mode={self.mode}, epsg={self.epsg or 'none'}, units={self.units}"]
        if np.isfinite(self.scale_m_per_px):
            lines.append(f"  scale {self.scale_m_per_px * 1000:.4f} mm/px")
        if len(self.residuals):
            r = self.residuals
            lines.append(
                f"  control residuals: median {np.median(r):.4f}, max {r.max():.4f} {self.units}, "
                f"n={len(r)}"
            )
        if np.isfinite(self.condition):
            lines.append(f"  control condition number {self.condition:.1f}")
        for note in self.notes:
            lines.append(f"  note: {note}")
        return "\n".join(lines)


def _uniform(scale: float, epsg: int, mode: str) -> GeoTransform:
    M = np.array([[scale, 0.0, 0.0], [0.0, scale, 0.0], [0.0, 0.0, 1.0]])
    return GeoTransform(mode=mode, epsg=epsg, matrix=M, scale_m_per_px=scale)


def scale_from_known_distance(p1, p2, length_m: float) -> float:
    d = float(np.hypot(p2[0] - p1[0], p2[1] - p1[1]))
    if d < 1e-9:
        raise ValueError("the two reference points coincide")
    if length_m <= 0:
        raise ValueError("known_length_m must be positive")
    return length_m / d


def scale_from_height(height_m: float, hfov_deg: float, image_width_px: int) -> float:
    """Nadir ground sampling distance.

    ``GSD = 2 * H * tan(HFOV / 2) / width``. Linear in height, which is why the
    takeoff-to-water offset matters so much.
    """
    if height_m <= 0:
        raise ValueError("height_m must be positive")
    if not (0 < hfov_deg < 180):
        raise ValueError("hfov_deg must be between 0 and 180")
    width_m = 2.0 * height_m * np.tan(np.radians(hfov_deg) / 2.0)
    return width_m / float(image_width_px)


def fit_control_points(image_pts, world_pts, ransac_thresh: float = 1.0):
    """Fit image-to-world control, degrading gracefully when spread is poor.

    Measurement verticals usually sit along a few cross-sections, which makes them
    near-collinear. A full projective fit on such a set is ill-conditioned, so the
    condition number decides whether to use a homography or a similarity.
    """
    src = np.asarray(image_pts, np.float64).reshape(-1, 2)
    dst = np.asarray(world_pts, np.float64).reshape(-1, 2)
    if len(src) != len(dst):
        raise ValueError("control point counts differ")
    if len(src) < 3:
        raise ValueError("need at least 3 control points (4 for a homography)")

    cond = condition_number(src)
    notes: list[str] = []
    fallback = False

    use_similarity = len(src) < 4 or not np.isfinite(cond) or cond > 20.0
    if use_similarity:
        fallback = True
        notes.append(
            f"control points are poorly spread (condition {cond:.1f}); "
            "fitted a similarity transform instead of a homography"
        )
        M, _ = cv2.estimateAffinePartial2D(
            src, dst, method=cv2.RANSAC, ransacReprojThreshold=ransac_thresh
        )
        if M is None:
            raise RuntimeError("similarity fit failed on the given control points")
        H = np.vstack([M, [0.0, 0.0, 1.0]])
    else:
        H, _ = cv2.findHomography(src, dst, method=cv2.RANSAC, ransacReprojThreshold=ransac_thresh)
        if H is None:
            raise RuntimeError("homography fit failed on the given control points")

    proj = cv2.perspectiveTransform(src.reshape(-1, 1, 2), H).reshape(-1, 2)
    residuals = np.linalg.norm(proj - dst, axis=1)
    return H, residuals, cond, fallback, notes


def build_transform(cfg: GeometryCfg, image_shape: tuple[int, int]) -> GeoTransform:
    """Resolve the configured geometry into a transform."""
    h, w = image_shape[:2]
    correction = float(cfg.scale_correction) if cfg.scale_correction else 1.0

    if cfg.mode == "none":
        t = _uniform(1.0, 0, "none")
        t.scale_m_per_px = float("nan")
        return t

    if cfg.mode == "known_distance":
        s = scale_from_known_distance(cfg.known_p1, cfg.known_p2, cfg.known_length_m) * correction
        t = _uniform(s, cfg.epsg, "known_distance")
        if correction != 1.0:
            t.notes.append(f"scale correction {correction:.4f} applied")
        return t

    if cfg.mode == "height":
        height = float(cfg.height_m) + float(cfg.water_offset_m)
        s = scale_from_height(height, cfg.hfov_deg, w) * correction
        t = _uniform(s, cfg.epsg, "height")
        t.notes.append(
            f"height above water taken as {height:.2f} m "
            f"(rel_alt {cfg.height_m:.2f} + offset {cfg.water_offset_m:+.2f}); "
            "GSD is linear in this value"
        )
        if correction != 1.0:
            t.notes.append(f"scale correction {correction:.4f} applied")
        return t

    if cfg.mode in ("gcp", "orthophoto"):
        H, res, cond, fallback, notes = fit_control_points(cfg.gcp_image, cfg.gcp_world)
        if correction != 1.0:
            H = np.diag([correction, correction, 1.0]) @ H
            notes = notes + [f"scale correction {correction:.4f} applied"]
        # a uniform scale is only meaningful when the transform has no perspective
        sx = float(np.hypot(H[0, 0], H[1, 0]))
        sy = float(np.hypot(H[0, 1], H[1, 1]))
        uniform = abs(H[2, 0]) < 1e-9 and abs(H[2, 1]) < 1e-9 and abs(sx - sy) < 0.02 * max(sx, sy)
        return GeoTransform(
            mode=cfg.mode,
            epsg=cfg.epsg,
            matrix=H,
            scale_m_per_px=(sx + sy) / 2.0 if uniform else float("nan"),
            residuals=res,
            condition=cond,
            fallback_used=fallback,
            notes=notes,
        )

    raise ValueError(f"unknown geometry mode: {cfg.mode!r}")

"""Polygon masks for static regions (banks, structures) and moving water."""

from __future__ import annotations

import cv2
import numpy as np


def polygon_mask(shape: tuple[int, int], polygons: list[list[list[float]]]) -> np.ndarray:
    """Rasterise polygons into a uint8 mask, 255 inside."""
    mask = np.zeros(shape[:2], dtype=np.uint8)
    for poly in polygons:
        if len(poly) < 3:
            continue
        pts = np.round(np.asarray(poly, dtype=np.float64)).astype(np.int32).reshape(-1, 1, 2)
        cv2.fillPoly(mask, [pts], 255)
    return mask


def static_feature_mask(
    shape: tuple[int, int],
    static_polys: list,
    water_polys: list,
) -> np.ndarray | None:
    """Where stabilisation may look for features.

    Static polygons win outright when given. Otherwise the whole frame minus the
    water polygons is used. Returning ``None`` means "no restriction", which is
    correct only when the scene is rigid.
    """
    statics = [p for p in (static_polys or []) if len(p) >= 3]
    waters = [p for p in (water_polys or []) if len(p) >= 3]
    if not statics and not waters:
        return None
    if statics:
        return polygon_mask(shape, statics)
    mask = np.full(shape[:2], 255, dtype=np.uint8)
    mask[polygon_mask(shape, waters) > 0] = 0
    return mask


def analysis_mask(shape: tuple[int, int], include: list, exclude: list) -> np.ndarray | None:
    """Boolean mask of the region velocimetry may use. ``None`` means everywhere."""
    if not include and not exclude:
        return None
    polys = [p for p in (include or []) if len(p) >= 3]
    if polys:
        mask = polygon_mask(shape, polys) > 0
    else:
        mask = np.ones(shape[:2], dtype=bool)
    for poly in exclude or []:
        if len(poly) >= 3:
            mask &= polygon_mask(shape, [poly]) == 0
    return mask


def brightness_water_suggestion(frame: np.ndarray, percentile: float = 55.0) -> np.ndarray:
    """A starting point for a water polygon, not a classifier.

    On a sunlit gravel-bed river, water and dry bar overlap in both brightness and
    colour, so this only offers a first guess to be corrected by hand.
    """
    import cv2

    gray = frame if frame.ndim == 2 else cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    smooth = cv2.GaussianBlur(gray.astype(np.float32), (0, 0), 9)
    thresh = np.percentile(smooth, percentile)
    mask = (smooth < thresh).astype(np.uint8) * 255
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((21, 21), np.uint8))
    return cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((51, 51), np.uint8)) > 0

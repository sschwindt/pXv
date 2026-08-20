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
    static_poly: list[list[float]],
    water_poly: list[list[float]],
) -> np.ndarray | None:
    """Where stabilisation may look for features.

    If a static polygon is given it wins outright. Otherwise the whole frame minus
    the water polygon is used. Returning ``None`` means "no restriction", which is
    correct only when the scene is rigid.
    """
    has_static = len(static_poly) >= 3
    has_water = len(water_poly) >= 3
    if not has_static and not has_water:
        return None
    if has_static:
        return polygon_mask(shape, [static_poly])
    mask = np.full(shape[:2], 255, dtype=np.uint8)
    water = polygon_mask(shape, [water_poly])
    mask[water > 0] = 0
    return mask

"""Analysis and stabilisation masks.

A rectangular ROI cannot exclude a gravel bar in the middle of a braided reach,
and correlation over dry ground reports a confident zero.
"""

import numpy as np

from pxv.preprocess.mask import analysis_mask, polygon_mask, static_feature_mask

SQUARE_A = [[10, 10], [50, 10], [50, 50], [10, 50]]
SQUARE_B = [[60, 60], [90, 60], [90, 90], [60, 90]]


def test_no_polygons_means_no_restriction():
    assert analysis_mask((100, 100), [], []) is None


def test_include_is_a_union():
    m = analysis_mask((100, 100), [SQUARE_A, SQUARE_B], [])
    assert m[30, 30] and m[75, 75]
    assert not m[5, 5]


def test_exclude_cuts_holes():
    hole = [[20, 20], [40, 20], [40, 40], [20, 40]]
    m = analysis_mask((100, 100), [SQUARE_A], [hole])
    assert m[15, 15]
    assert not m[30, 30]


def test_exclude_alone_masks_everything_else():
    m = analysis_mask((100, 100), [], [SQUARE_A])
    assert not m[30, 30]
    assert m[75, 75]


def test_degenerate_polygons_ignored():
    m = analysis_mask((100, 100), [SQUARE_A, [[1, 1], [2, 2]]], [])
    assert m[30, 30]


def test_static_mask_prefers_explicit_static_polygons():
    m = static_feature_mask((100, 100), [SQUARE_A], [SQUARE_B])
    assert m[30, 30] > 0
    assert m[75, 75] == 0


def test_static_mask_falls_back_to_everything_but_water():
    m = static_feature_mask((100, 100), [], [SQUARE_A, SQUARE_B])
    assert m[30, 30] == 0
    assert m[75, 75] == 0
    assert m[5, 5] > 0


def test_polygon_mask_rasterises():
    m = polygon_mask((100, 100), [SQUARE_A])
    assert m.dtype == np.uint8
    assert m[30, 30] == 255

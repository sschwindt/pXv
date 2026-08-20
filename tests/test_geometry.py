import numpy as np
import pytest

from pxv.geometry.world import (
    build_transform, fit_control_points, scale_from_height, scale_from_known_distance,
)
from pxv.project import GeometryCfg


def test_known_distance_scale():
    s = scale_from_known_distance([0, 0], [1000, 0], 0.5)
    assert s == pytest.approx(0.0005)


def test_height_scaling_is_linear_in_height():
    a = scale_from_height(40.0, 84.0, 3840)
    b = scale_from_height(42.0, 84.0, 3840)
    assert b / a == pytest.approx(42.0 / 40.0)
    # a 2 m height error at 40 m is a 5 % velocity bias
    assert (b / a - 1.0) == pytest.approx(0.05)


def test_transform_reproduces_the_known_length():
    cfg = GeometryCfg(mode="known_distance", known_p1=[10, 50], known_p2=[1030, 50],
                      known_length_m=0.5)
    t = build_transform(cfg, (900, 1080))
    x0, y0 = t.to_world(np.array([10.0]), np.array([50.0]))
    x1, y1 = t.to_world(np.array([1030.0]), np.array([50.0]))
    assert float(np.hypot(x1 - x0, y1 - y0)[0]) == pytest.approx(0.5, abs=1e-9)


def test_displacement_conversion_uses_both_endpoints():
    cfg = GeometryCfg(mode="known_distance", known_p1=[0, 0], known_p2=[1000, 0],
                      known_length_m=1.0)
    t = build_transform(cfg, (500, 1000))
    du, dv = t.displacement_to_world(np.array([100.0]), np.array([100.0]),
                                     np.array([10.0]), np.array([0.0]))
    assert float(du[0]) == pytest.approx(0.01)
    assert float(dv[0]) == pytest.approx(0.0)


def test_collinear_control_falls_back_to_similarity():
    img = [[10, 100], [200, 100], [400, 100], [600, 100.0]]
    world = [[0, 0], [19, 0], [39, 0], [59, 0.0]]
    _, _, cond, fallback, notes = fit_control_points(img, world)
    assert fallback
    assert not np.isfinite(cond)
    assert "poorly spread" in notes[0]


def test_well_spread_control_uses_homography():
    img = [[0, 0], [100, 0], [100, 100], [0, 100.0]]
    world = [[0, 0], [1, 0], [1, 1], [0, 1.0]]
    _, res, cond, fallback, _ = fit_control_points(img, world)
    assert not fallback
    assert cond < 5.0
    assert res.max() < 1e-6


def test_scale_correction_multiplies():
    cfg = GeometryCfg(mode="known_distance", known_p1=[0, 0], known_p2=[1000, 0],
                      known_length_m=1.0, scale_correction=1.1)
    t = build_transform(cfg, (500, 1000))
    assert t.scale_m_per_px == pytest.approx(0.0011)

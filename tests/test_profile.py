"""The ground truth sits at 0.6 h and 0.8 h *above the bed*, not below the surface."""

import numpy as np
import pytest

from pxv.calibrate.profile import (
    TWO_POINT_K, convert_to_surface, log_ratio, roughness_sensitivity,
    single_point_surface, two_point_amplification, two_point_surface, z0_from_two_points,
)

UK, Z0, H = 0.12, 0.005, 0.8


def _u(z):
    return UK * np.log(z / Z0)


def test_two_point_constant():
    assert TWO_POINT_K == pytest.approx(1.7757, abs=1e-4)


def test_uncertainty_amplification():
    assert two_point_amplification() == pytest.approx(1.938, abs=1e-3)


def test_two_point_is_exact_on_a_log_profile():
    est, _ = two_point_surface(_u(0.6 * H), _u(0.8 * H))
    assert est == pytest.approx(_u(H), abs=1e-9)


def test_two_point_recovers_roughness():
    z0 = z0_from_two_points(_u(0.6 * H), _u(0.8 * H), H)
    assert z0 == pytest.approx(Z0, rel=1e-6)


def test_single_point_is_exact_with_the_right_roughness():
    assert single_point_surface(_u(0.6 * H), 0.6, H, Z0) == pytest.approx(_u(H), rel=1e-9)
    assert single_point_surface(_u(0.8 * H), 0.8, H, Z0) == pytest.approx(_u(H), rel=1e-9)


def test_correction_is_smaller_than_a_depth_average_conversion():
    """0.6 h and 0.8 h above the bed sit high in the profile, so alpha does not apply."""
    r06 = log_ratio(0.6, H, Z0)
    r08 = log_ratio(0.8, H, Z0)
    depth_avg = 1.0 - 1.0 / np.log(H / Z0)   # the conventional alpha
    assert 0.85 < r06 < 0.94
    assert 0.93 < r08 < 0.98
    assert depth_avg < r06 < r08


def test_roughness_sensitivity_is_bounded():
    """A badly chosen roughness is a bounded error, not an open-ended one."""
    lo6, hi6 = roughness_sensitivity(0.6, H, H / 3000.0, H / 30.0)
    lo8, hi8 = roughness_sensitivity(0.8, H, H / 3000.0, H / 30.0)
    assert (hi6 - lo6) < 0.10
    assert (hi8 - lo8) < 0.05


def test_hybrid_handles_mixed_vertical_coverage():
    speed = np.array([_u(0.6 * H), _u(0.8 * H), _u(0.6 * H), _u(0.8 * H)])
    zoh = np.array([0.6, 0.8, 0.6, 0.8])
    depth = np.full(4, H)
    vid = np.array(["A", "A", "B", "C"])          # one paired vertical, two singles
    conv = convert_to_surface(speed, zoh, depth, np.full(4, 0.005), vid, method="hybrid")
    assert conv.z0_estimate == pytest.approx(Z0, rel=1e-5)
    assert np.allclose(conv.u_surface, _u(H), rtol=1e-6)
    # the 0.6 h point is further from the surface, so its converted value is less certain
    assert conv.sigma[0] > conv.sigma[1]


def test_conversion_none_passes_through():
    speed = np.array([1.0, 2.0])
    conv = convert_to_surface(speed, np.array([0.6, 0.8]), np.array([1.0, 1.0]),
                              method="none")
    assert np.allclose(conv.u_surface, speed)

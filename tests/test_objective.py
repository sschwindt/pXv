import numpy as np
import pytest

from pxv.calibrate.objective import Comparison, compare, objective_value, quarter_rule_window


def test_perfect_match():
    obs = np.array([1.0, 2.0, 3.0])
    c = compare(obs, obs.copy(), np.zeros(3))
    assert c.rmse == pytest.approx(0.0)
    assert c.r2 == pytest.approx(1.0)


def test_bias_is_signed():
    obs = np.array([1.0, 1.0, 1.0])
    c = compare(obs, np.array([1.1, 1.1, 1.1]), np.zeros(3))
    assert c.bias == pytest.approx(0.1)


def test_uncertainty_weighting_downweights_noisy_points():
    obs = np.array([1.0, 1.0])
    mod = np.array([1.0, 2.0])          # second point is badly wrong
    tight = compare(obs, mod, np.zeros(2), sigma=np.array([0.01, 0.01]))
    loose = compare(obs, mod, np.zeros(2), sigma=np.array([0.01, 10.0]))
    assert loose.rmse < tight.rmse


def test_angular_error_computed_from_components():
    c = compare(np.array([1.0]), np.array([0.0]), np.array([1.0]),
                observed_u=np.array([1.0]), observed_v=np.array([0.0]))
    assert c.angular_error_deg == pytest.approx(90.0, abs=1e-4)


def test_yield_penalty_punishes_degenerate_settings():
    """Without the penalty an optimizer picks settings returning three great vectors."""
    good = Comparison(n=50, rmse=0.10, bias=0.0, mae=0.1, r2=0.9,
                      angular_error_deg=10.0, coverage=0.9)
    degenerate = Comparison(n=3, rmse=0.01, bias=0.0, mae=0.01, r2=0.99,
                            angular_error_deg=5.0, coverage=0.06)
    j_good = objective_value(good, 1.0, 0.5, 0.25, vector_yield=0.85)
    j_bad = objective_value(degenerate, 1.0, 0.5, 0.25, vector_yield=0.02)
    assert j_bad > j_good


def test_quarter_rule():
    # 1 m/s, 0.1 s, 20 mm/px -> 5 px displacement -> 20 px window, rounds up to 24
    assert quarter_rule_window(1.0, 0.1, 0.020) == 24
    assert quarter_rule_window(0.0, 0.1, 0.020) == 16


def test_diagnose_scale_error():
    """A scale error multiplies every point by the same factor."""
    from pxv.calibrate.objective import diagnose_bias

    obs = np.array([0.5, 1.0, 1.5, 2.0])
    verdict, why = diagnose_bias(obs, obs * 0.72)
    assert verdict == "scale"
    assert "0.72" in why


def test_diagnose_velocimetry_failure():
    """Scattered, one-sided ratios are not a scale error.

    This is the pattern seen when correlation locks onto a stationary bed seen
    through clear water: every point too low, by wildly differing amounts.
    """
    from pxv.calibrate.objective import diagnose_bias

    obs = np.array([0.17, 0.88, 1.17, 1.24, 1.31, 1.99])
    mod = np.array([0.04, 0.17, 0.23, 0.47, 0.04, 0.01])
    verdict, why = diagnose_bias(obs, mod)
    assert verdict == "failure"
    assert "not a scale error" in why


def test_diagnose_needs_enough_points():
    from pxv.calibrate.objective import diagnose_bias

    assert diagnose_bias(np.array([1.0]), np.array([0.5]))[0] == "unknown"


def test_diagnose_all_zero_modelled():
    from pxv.calibrate.objective import diagnose_bias

    verdict, _ = diagnose_bias(np.array([1.0, 2.0, 3.0]), np.zeros(3))
    assert verdict == "failure"


def test_unknown_calibration_parameters_are_reported_not_dropped():
    """Silently dropping a requested parameter makes the search look broader."""
    from pxv.calibrate.optimize import SEARCH_SPACE

    assert "ptv.bright_threshold" in SEARCH_SPACE
    assert "grid.cell_px" in SEARCH_SPACE


def test_every_search_parameter_resolves_on_a_project():
    """A search range that does not map to a real field would fail mid-run."""
    from pxv.calibrate.optimize import SEARCH_SPACE
    from pxv.project import Project

    p = Project()
    for name, values in SEARCH_SPACE.items():
        current = p.get(name)          # raises if the path is wrong
        p.set(name, values[0])
        assert p.get(name) == values[0]
        p.set(name, current)

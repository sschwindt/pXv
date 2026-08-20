import numpy as np
import pytest

from pxv.project import PIVCfg
from pxv.velocimetry.piv import (
    correlation_planes, normalized_median_test, peak_find, piv_ensemble, piv_pair,
)


@pytest.mark.parametrize("du,dv", [(3.0, -2.0), (6.5, 4.25), (-8.0, 1.5), (0.0, 0.0)])
def test_recovers_known_displacement(particle_pair, du, dv):
    a, b = particle_pair(du, dv)
    f = piv_pair(a, b, PIVCfg(windows=[64, 32], overlap=0.5,
                              corr_threshold=0.4, snr_threshold=2.0))
    assert f.yield_fraction() > 0.5
    u = f.u[f.valid]
    v = f.v[f.valid]
    assert abs(np.median(u) - du) < 0.35
    assert abs(np.median(v) - dv) < 0.35


def test_correlation_matches_pearson_at_zero_lag(rng):
    a = rng.random((1, 1, 32, 32)).astype(np.float32)
    b = rng.random((1, 1, 32, 32)).astype(np.float32)
    plane = correlation_planes(a, b)[0, 0]
    expected = np.corrcoef(a.ravel(), b.ravel())[0, 1]
    assert plane[16, 16] == pytest.approx(expected, abs=1e-4)


def test_flat_window_is_not_reported_as_valid():
    """A textureless window correlates perfectly with itself at zero lag.

    Without a texture guard the static background outranks the moving water.
    """
    flat = np.full((256, 256), 0.5, np.float32)
    f = piv_pair(flat, flat, PIVCfg(windows=[64], overlap=0.5, min_texture=0.02))
    assert not f.valid.any()


def test_snr_peak2rms_beats_peak2peak_on_broad_peaks(rng):
    """A broad peak makes peak-to-second-peak collapse toward 1.0."""
    yy, xx = np.mgrid[0:64, 0:64]
    broad = np.exp(-((yy - 32) ** 2 + (xx - 32) ** 2) / (2 * 8.0 ** 2)).astype(np.float32)
    planes = broad[None, None]
    _, _, _, snr_pp = peak_find(planes, snr_method="peak2peak", exclusion_px=2)
    _, _, _, snr_rms = peak_find(planes, snr_method="peak2rms", exclusion_px=5)
    assert snr_pp[0, 0] < 1.1
    assert snr_rms[0, 0] > 2.0


def test_median_test_flags_a_planted_outlier():
    u = np.ones((5, 5), np.float32)
    v = np.zeros((5, 5), np.float32)
    u[2, 2] = 50.0
    bad = normalized_median_test(u, v, threshold=2.0)
    assert bad[2, 2]
    assert not bad[1, 1]


def test_ensemble_recovers_displacement(particle_pair):
    pairs = [particle_pair(4.0, -3.0) for _ in range(4)]
    f = piv_ensemble(pairs, PIVCfg(windows=[64], overlap=0.5,
                                   corr_threshold=0.4, snr_threshold=2.0))
    assert abs(np.median(f.u[f.valid]) - 4.0) < 0.4
    assert abs(np.median(f.v[f.valid]) + 3.0) < 0.4

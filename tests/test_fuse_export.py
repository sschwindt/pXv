import numpy as np
import pytest

from pxv.export.streamlines import check_cmap
from pxv.velocimetry.fuse import (
    SOURCE_PIV, SOURCE_PTV, bin_scatter, fuse, recommend_engine, seeding_density,
)
from pxv.velocimetry.piv import PIVField


def _piv_field(shape=(4, 4), u=2.0, v=-1.0):
    xs = np.arange(shape[1]) * 32.0 + 16
    ys = np.arange(shape[0]) * 32.0 + 16
    xx, yy = np.meshgrid(xs, ys)
    return PIVField(x=xx, y=yy, u=np.full(shape, u), v=np.full(shape, v),
                    corr=np.full(shape, 0.9), snr=np.full(shape, 5.0),
                    valid=np.ones(shape, bool))


def test_bin_scatter_uses_the_median():
    px = np.array([5.0, 6.0, 7.0])
    py = np.array([5.0, 5.0, 5.0])
    pu = np.array([1.0, 2.0, 100.0])      # one gross outlier
    pv = np.zeros(3)
    u, v, n, _, _ = bin_scatter((32, 32), 32, px, py, pu, pv)
    assert n[0, 0] == 3
    assert u[0, 0] == 2.0                 # median resists the outlier


def test_ptv_overrides_piv_where_it_has_support():
    class T:
        n = 40
        def stacked(self):
            x = np.full(40, 16.0)
            y = np.full(40, 16.0)
            return x, y, np.full(40, 9.0), np.full(40, 0.0), np.zeros(40)

    field = fuse((128, 128), 32, [_piv_field()], T(), min_samples=5)
    assert field.source[0, 0] == SOURCE_PTV
    assert field.u[0, 0] == pytest.approx(9.0)
    assert field.source[1, 1] == SOURCE_PIV
    assert field.u[1, 1] == pytest.approx(2.0)


def test_seeding_recommendation_thresholds():
    assert recommend_engine(20.0)[0] == "piv"
    assert recommend_engine(4.0)[0] == "combined"
    assert recommend_engine(0.2)[0] == "ptv"


def test_seeding_density_scales_with_window():
    d = seeding_density(1000, (1000, 1000), 100)
    assert d == pytest.approx(10.0)


def test_direction_convention():
    field = fuse((64, 64), 32, [_piv_field(shape=(2, 2), u=0.0, v=-1.0)], None)
    # v negative means motion toward the top of the image, which is bearing 0
    assert field.direction_deg()[0, 0] == pytest.approx(0.0, abs=1e-6)
    field = fuse((64, 64), 32, [_piv_field(shape=(2, 2), u=1.0, v=0.0)], None)
    assert field.direction_deg()[0, 0] == pytest.approx(90.0, abs=1e-6)


@pytest.mark.parametrize("name", ["jet", "rainbow", "turbo", "nipy_spectral"])
def test_rainbow_colormaps_refused(name):
    with pytest.raises(ValueError, match="colour-vision-deficiency safe"):
        check_cmap(name)


@pytest.mark.parametrize("name", ["cividis", "viridis", "magma"])
def test_safe_colormaps_accepted(name):
    assert check_cmap(name) == name


def test_static_fraction_flags_a_stationary_field():
    """The bed seen through clear water reports a confident zero."""
    from pxv.velocimetry.fuse import assess_static, static_fraction

    still = fuse((128, 128), 32, [_piv_field(shape=(4, 4), u=0.0, v=0.0)], None)
    moving = fuse((128, 128), 32, [_piv_field(shape=(4, 4), u=3.0, v=-2.0)], None)
    assert static_fraction(still) == 1.0
    assert static_fraction(moving) == 0.0
    assert assess_static(0.8)[0] == "suspect"
    assert assess_static(0.25)[0] == "check"
    assert assess_static(0.05)[0] == "ok"


def test_piv_health_gate_keeps_tracer_vectors_undiluted():
    """A bed-locked PIV field must not be fused in beside good tracer vectors.

    PIV that reports confident zeros would otherwise swamp a sparse but correct
    PTV field, dragging the median velocity toward nothing.
    """
    from pxv.velocimetry.fuse import static_fraction

    class T:
        n = 40
        def stacked(self):
            x = np.full(40, 16.0)
            y = np.full(40, 16.0)
            return x, y, np.full(40, 9.0), np.full(40, 0.0), np.zeros(40)

    dead_piv = [_piv_field(shape=(4, 4), u=0.0, v=0.0)]
    with_piv = fuse((128, 128), 32, dead_piv, T(), min_samples=5)
    ptv_only = fuse((128, 128), 32, None, T(), min_samples=5)

    assert static_fraction(with_piv) > 0.5      # the gate would fire on this
    assert static_fraction(ptv_only) == 0.0
    assert np.median(with_piv.magnitude()[with_piv.valid]) < 1.0
    assert np.median(ptv_only.magnitude()[ptv_only.valid]) == pytest.approx(9.0)

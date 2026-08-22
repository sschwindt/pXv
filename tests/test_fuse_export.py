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


class _ScatterTrack:
    """Vectors along a diagonal path, as a tracer pulse would leave."""

    def __init__(self, count: int = 60):
        t = np.linspace(0, 1, count)
        self.px = 20 + t * 200
        self.py = 20 + t * 100
        self.n = count

    def stacked(self):
        return (self.px, self.py,
                np.full(self.n, 5.0), np.full(self.n, 2.5), np.zeros(self.n))


def _scatter_track(count: int = 60) -> _ScatterTrack:
    return _ScatterTrack(count)


def test_gap_filling_connects_a_sparse_track():
    """Isolated cells make streamlines stop immediately; bounded filling connects them."""
    from pxv.velocimetry.fuse import SOURCE_INTERP, interpolate_gaps

    field = fuse((256, 320), 16, None, _scatter_track(), min_samples=1)
    before = int(field.valid.sum())
    filled = interpolate_gaps(field, radius_px=40.0, min_neighbours=2)
    after = int(filled.valid.sum())
    assert after > before
    assert int((filled.source == SOURCE_INTERP).sum()) == after - before


def test_interpolated_cells_are_not_counted_as_measurements():
    from pxv.velocimetry.fuse import interpolate_gaps

    field = fuse((256, 320), 16, None, _scatter_track(), min_samples=1)
    filled = interpolate_gaps(field, radius_px=40.0)
    assert filled.measured.sum() == field.measured.sum()
    assert filled.n[filled.source == 3].sum() == 0


def test_gap_filling_respects_its_radius():
    from pxv.velocimetry.fuse import interpolate_gaps

    field = fuse((256, 320), 16, None, _scatter_track(), min_samples=1)
    near = interpolate_gaps(field, radius_px=20.0)
    far = interpolate_gaps(field, radius_px=80.0)
    assert far.valid.sum() > near.valid.sum()
    assert interpolate_gaps(field, radius_px=0.0).valid.sum() == field.valid.sum()


def test_gap_filling_preserves_the_velocity_value():
    from pxv.velocimetry.fuse import interpolate_gaps

    field = fuse((256, 320), 16, None, _scatter_track(), min_samples=1)
    filled = interpolate_gaps(field, radius_px=40.0)
    interp = filled.source == 3
    assert np.allclose(filled.u[interp], 5.0, atol=1e-6)
    assert np.allclose(filled.v[interp], 2.5, atol=1e-6)


def test_static_fraction_ignores_interpolated_cells():
    """Filled cells must not dilute the health check that gates calibration."""
    from pxv.velocimetry.fuse import interpolate_gaps, static_fraction

    field = fuse((256, 320), 16, None, _scatter_track(), min_samples=1)
    filled = interpolate_gaps(field, radius_px=40.0)
    assert static_fraction(filled) == static_fraction(field)


def _piv_series(n=10, shape=(6, 6), moving_cols=3):
    """A PIV time series where some cells move and the rest never do.

    Bed-locking is spatial: over clear water the bed wins, over rippled water the
    surface does, and both happen in one frame.
    """
    rng = np.random.default_rng(7)
    out = []
    for _ in range(n):
        u = np.zeros(shape, np.float32)
        v = np.zeros(shape, np.float32)
        u[:, :moving_cols] = 4.0 + rng.normal(0, 0.2, (shape[0], moving_cols))
        v[:, :moving_cols] = -1.0
        # the static half jitters at the noise floor, never beyond it
        u[:, moving_cols:] = rng.normal(0, 0.05, (shape[0], shape[1] - moving_cols))
        xs = np.arange(shape[1]) * 32.0 + 16
        ys = np.arange(shape[0]) * 32.0 + 16
        xx, yy = np.meshgrid(xs, ys)
        out.append(PIVField(x=xx, y=yy, u=u, v=v, corr=np.full(shape, 0.9),
                            snr=np.full(shape, 5.0), valid=np.ones(shape, bool)))
    return out


def test_per_cell_static_test_keeps_the_moving_half():
    """A single whole-field verdict throws away the cells that work."""
    from pxv.velocimetry.fuse import piv_static_cells

    drop = piv_static_cells(_piv_series(), threshold_px=0.5, min_fraction=0.6)
    assert not drop[:, :3].any()      # moving cells kept
    assert drop[:, 3:].all()          # persistently static cells dropped


def test_per_cell_static_test_keeps_fluctuating_slow_water():
    """Genuinely slow water fluctuates; a stationary bed does not."""
    from pxv.velocimetry.fuse import piv_static_cells

    rng = np.random.default_rng(2)
    fields = []
    for _ in range(10):
        u = np.abs(rng.normal(0.6, 0.5, (4, 4))).astype(np.float32)
        xs = np.arange(4) * 32.0 + 16
        xx, yy = np.meshgrid(xs, xs)
        fields.append(PIVField(x=xx, y=yy, u=u, v=np.zeros((4, 4), np.float32),
                               corr=np.full((4, 4), 0.9), snr=np.full((4, 4), 5.0),
                               valid=np.ones((4, 4), bool)))
    drop = piv_static_cells(fields, threshold_px=0.5, min_fraction=0.6)
    assert drop.mean() < 0.5


def test_ptv_correction_rescales_piv():
    """Where the two engines coincide, their ratio carries to the PIV-only cells."""
    from pxv.velocimetry.fuse import SOURCE_PIV_CORRECTED, correct_piv_with_ptv

    class T:
        n = 30
        def stacked(self):
            x = np.full(30, 48.0)
            y = np.full(30, 48.0)
            return x, y, np.full(30, 8.0), np.full(30, 0.0), np.zeros(30)

    field = fuse((192, 192), 32, [_piv_field(shape=(6, 6), u=4.0, v=0.0)], T(),
                 min_samples=5)
    out, report = correct_piv_with_ptv(field, min_overlap=1)
    assert report["applied"] > 0
    assert report["gain"] == pytest.approx(2.0, rel=0.05)
    assert (out.source == SOURCE_PIV_CORRECTED).any()
    corrected = out.source == SOURCE_PIV_CORRECTED
    assert np.allclose(np.hypot(out.u[corrected], out.v[corrected]), 8.0, rtol=0.05)


def test_ptv_correction_refuses_an_implausible_gain():
    """A gain far from one means the engines are measuring different things."""
    from pxv.velocimetry.fuse import correct_piv_with_ptv

    class T:
        n = 30
        def stacked(self):
            x = np.full(30, 48.0)
            y = np.full(30, 48.0)
            return x, y, np.full(30, 400.0), np.full(30, 0.0), np.zeros(30)

    field = fuse((192, 192), 32, [_piv_field(shape=(6, 6), u=1.0, v=0.0)], T(),
                 min_samples=5)
    out, report = correct_piv_with_ptv(field, min_overlap=1, max_gain=4.0)
    assert report["applied"] == 0
    assert "outside" in report["reason"]


def test_ptv_correction_needs_enough_overlap():
    from pxv.velocimetry.fuse import correct_piv_with_ptv

    field = fuse((192, 192), 32, [_piv_field(shape=(6, 6), u=4.0, v=0.0)], None)
    out, report = correct_piv_with_ptv(field, min_overlap=4)
    assert report["applied"] == 0
    assert "no PIV and PTV cells" in report["reason"]

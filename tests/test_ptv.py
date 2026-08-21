import cv2
import numpy as np

from pxv.project import PTVCfg
from tests.conftest import render_particles
from pxv.velocimetry.ptv import MotionModel, detect, run_ptv


def test_detects_particles(rng):
    xs = rng.uniform(20, 236, 200)
    ys = rng.uniform(20, 236, 200)
    img = render_particles((256, 256), xs, ys, sigma=1.8)
    pts = detect(img, PTVCfg(conv_size=2.0))
    assert 100 < len(pts) <= 220


def test_tracks_uniform_motion(rng):
    n = 300
    xs = rng.uniform(20, 236, n)
    ys = rng.uniform(20, 236, n)
    du, dv = 4.0, -2.5
    frames = np.stack([render_particles((256, 256), xs + k * du, ys + k * dv, 1.8)
                       for k in range(10)])
    tracks = run_ptv(frames, PTVCfg(conv_size=2.0, motion_neighbours=8, filter_std=2.0))
    assert tracks.n > 500
    _, _, u, v, _ = tracks.stacked()
    assert abs(np.median(u) - du) < 0.3
    assert abs(np.median(v) - dv) < 0.3


def test_motion_model_predicts_from_piv_seed():
    model = MotionModel(neighbours=4)
    x, y = np.meshgrid(np.arange(0, 100, 10.0), np.arange(0, 100, 10.0))
    model.seed_from_piv(x, y, np.full(x.shape, 3.0), np.full(x.shape, -1.0))
    pred = model.predict(np.array([[50.0, 50.0], [10.0, 10.0]]))
    assert np.allclose(pred[:, 0], 3.0, atol=1e-3)
    assert np.allclose(pred[:, 1], -1.0, atol=1e-3)


def _tracer_frame(shape, points, radius=4, background=0.35, brightness=0.95):
    """A dim, textured water surface with a few bright compact tracers on it."""
    rng = np.random.default_rng(3)
    img = np.full(shape, background, np.float32)
    img += rng.normal(0, 0.05, shape).astype(np.float32)     # water texture
    for x, y in points:
        cv2.circle(img, (int(x), int(y)), radius, brightness, -1)
    return np.clip(img, 0, 1)


def test_bright_detector_finds_tracers_and_ignores_texture():
    """Injected tracers are far brighter than the water and have a known size."""
    from pxv.velocimetry.ptv import detect_bright

    pts = [(60, 60), (140, 90), (200, 180)]
    img = _tracer_frame((256, 256), pts)
    cfg = PTVCfg(detector="bright", bright_threshold=0.8, min_area_px=20, max_area_px=400)
    found = detect_bright(img, cfg)
    assert len(found) == 3
    for x, y in pts:
        assert np.min(np.hypot(found[:, 0] - x, found[:, 1] - y)) < 2.0


def test_bright_detector_rejects_blobs_outside_the_area_window():
    from pxv.velocimetry.ptv import detect_bright

    img = _tracer_frame((256, 256), [(128, 128)], radius=40)   # far too large
    cfg = PTVCfg(detector="bright", bright_threshold=0.8, min_area_px=20, max_area_px=400)
    assert len(detect_bright(img, cfg)) == 0


def test_bright_detector_honours_the_mask():
    from pxv.velocimetry.ptv import detect_bright

    img = _tracer_frame((256, 256), [(60, 60), (200, 200)])
    mask = np.zeros((256, 256), bool)
    mask[:128, :128] = True
    cfg = PTVCfg(detector="bright", bright_threshold=0.8, min_area_px=20, max_area_px=400)
    found = detect_bright(img, cfg, mask)
    assert len(found) == 1
    assert found[0][0] < 128


def test_bright_detector_tracks_tracers_through_a_sequence():
    du, dv = 4.0, -2.0
    pts = [(50 + 30 * i, 150 + 10 * i) for i in range(6)]
    frames = np.stack([
        _tracer_frame((256, 320), [(x + k * du, y + k * dv) for x, y in pts])
        for k in range(8)
    ])
    cfg = PTVCfg(detector="bright", bright_threshold=0.8, min_area_px=20,
                 max_area_px=400, motion_neighbours=4, filter_std=3.0,
                 max_displacement=15.0)
    tracks = run_ptv(frames, cfg)
    assert tracks.n > 20
    _, _, u, v, _ = tracks.stacked()
    assert abs(np.median(u) - du) < 0.5
    assert abs(np.median(v) - dv) < 0.5

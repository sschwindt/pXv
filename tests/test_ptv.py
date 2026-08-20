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

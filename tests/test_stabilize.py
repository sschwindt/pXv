import cv2
import numpy as np

from pxv.project import StabilizeCfg
from pxv.stabilize.homography import condition_number, stabilize, warp_to_reference


def _shifted_stack(rng, shifts):
    base = cv2.GaussianBlur(rng.random((240, 320)).astype(np.float32), (0, 0), 1.2)
    out = []
    for dx, dy in shifts:
        M = np.float32([[1, 0, dx], [0, 1, dy]])
        out.append(cv2.warpAffine(base, M, (320, 240)))
    return np.stack(out)


def test_recovers_known_translation(rng):
    shifts = [(0, 0), (3, -2), (6, -4), (9, -5)]
    frames = _shifted_stack(rng, shifts)
    res = stabilize(frames, StabilizeCfg(model="similarity", keyframe_interval=100))
    recovered = res.apparent_motion_px()
    for (dx, dy), (rx, ry) in zip(shifts, recovered):
        assert abs(rx + dx) < 0.3
        assert abs(ry + dy) < 0.3
    assert np.nanmedian(res.residuals) < 0.5
    assert not res.failures


def test_warping_undoes_the_shift(rng):
    frames = _shifted_stack(rng, [(0, 0), (7, -4)])
    res = stabilize(frames, StabilizeCfg(model="similarity", keyframe_interval=100))
    warped = warp_to_reference(frames[1], res.transforms[1])
    inner = (slice(20, 220), slice(20, 300))
    assert np.mean(np.abs(warped[inner] - frames[0][inner])) < 0.02


def test_disabled_returns_identity(rng):
    frames = _shifted_stack(rng, [(0, 0), (5, 5)])
    res = stabilize(frames, StabilizeCfg(enabled=False))
    assert all(np.allclose(H, np.eye(3)) for H in res.transforms)


def test_condition_number_flags_collinear_points():
    collinear = np.array([[0, 0], [1, 1], [2, 2], [3, 3.0]])
    spread = np.array([[0, 0], [10, 0], [0, 10], [10, 10.0]])
    assert not np.isfinite(condition_number(collinear))
    assert condition_number(spread) < 2.0

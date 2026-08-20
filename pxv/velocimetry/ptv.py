"""Particle tracking velocimetry, following the TracTrac algorithm (Heyman 2019).

Detection is a difference-of-Gaussian convolution followed by non-maximum
suppression and a second-order sub-pixel fit. Matching uses a predictor: a
KdTree-averaged motion model built from previously accepted vectors, which can
additionally be seeded from a PIV field (the combined mode of section 4.5 in the
development plan). Matches whose error exceeds ``filter_std`` standard deviations
of the model error are rejected.

Upstream TracTrac is MIT licensed. This is a reimplementation of the published
algorithm against the same parameter names, so existing ``*_par.txt`` settings
carry over.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import cv2
import numpy as np
from scipy.spatial import cKDTree

from pxv.project import PTVCfg


@dataclass
class Tracks:
    """Scattered displacement vectors accumulated over the clip."""

    x: list[np.ndarray] = field(default_factory=list)
    y: list[np.ndarray] = field(default_factory=list)
    u: list[np.ndarray] = field(default_factory=list)
    v: list[np.ndarray] = field(default_factory=list)
    err: list[np.ndarray] = field(default_factory=list)

    def add(self, x, y, u, v, err) -> None:
        self.x.append(np.asarray(x, np.float32))
        self.y.append(np.asarray(y, np.float32))
        self.u.append(np.asarray(u, np.float32))
        self.v.append(np.asarray(v, np.float32))
        self.err.append(np.asarray(err, np.float32))

    def stacked(self) -> tuple[np.ndarray, ...]:
        if not self.x:
            empty = np.zeros(0, np.float32)
            return empty, empty, empty, empty, empty
        return (
            np.concatenate(self.x),
            np.concatenate(self.y),
            np.concatenate(self.u),
            np.concatenate(self.v),
            np.concatenate(self.err),
        )

    @property
    def n(self) -> int:
        return sum(len(a) for a in self.x)


def difference_of_gaussian(img: np.ndarray, size: float) -> np.ndarray:
    """DoG bandpass tuned to objects of radius ``size`` pixels."""
    s1 = max(0.5, float(size))
    s2 = s1 * 1.6
    a = cv2.GaussianBlur(img, (0, 0), s1)
    b = cv2.GaussianBlur(img, (0, 0), s2)
    return a - b


def auto_threshold(conv: np.ndarray) -> float:
    """Threshold at the upper tail of the convolved image.

    TracTrac's automatic mode adapts to image contrast rather than fixing an
    absolute value, which matters when illumination varies between clips.
    """
    positive = conv[conv > 0]
    if positive.size == 0:
        return 0.0
    return float(np.percentile(positive, 99.0) * 0.5)


def _subpixel_2nd(conv: np.ndarray, ys: np.ndarray, xs: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Second-order fit on the 3x3 neighbourhood of each integer maximum."""
    h, w = conv.shape
    dx = np.zeros(len(xs), np.float32)
    dy = np.zeros(len(ys), np.float32)
    for k, (j, i) in enumerate(zip(ys, xs)):
        if not (0 < j < h - 1 and 0 < i < w - 1):
            continue
        c = conv[j, i]
        denx = 2.0 * (conv[j, i - 1] - 2.0 * c + conv[j, i + 1])
        deny = 2.0 * (conv[j - 1, i] - 2.0 * c + conv[j + 1, i])
        if abs(denx) > 1e-9:
            dx[k] = np.clip((conv[j, i - 1] - conv[j, i + 1]) / denx, -1.0, 1.0)
        if abs(deny) > 1e-9:
            dy[k] = np.clip((conv[j - 1, i] - conv[j + 1, i]) / deny, -1.0, 1.0)
    return dx, dy


def detect(img: np.ndarray, cfg: PTVCfg) -> np.ndarray:
    """Detect objects. Returns an ``(n, 2)`` array of sub-pixel ``(x, y)``."""
    conv = difference_of_gaussian(img, cfg.conv_size)
    thresh = auto_threshold(conv) if cfg.auto_threshold else float(cfg.peak_threshold)
    k = 2 * int(max(1, cfg.peak_neighbourhood)) + 1
    dilated = cv2.dilate(conv, np.ones((k, k), np.uint8))
    peaks = (conv >= dilated) & (conv > thresh)
    ys, xs = np.nonzero(peaks)
    if len(xs) == 0:
        return np.zeros((0, 2), np.float32)
    dx, dy = _subpixel_2nd(conv, ys, xs)
    return np.column_stack([xs + dx, ys + dy]).astype(np.float32)


class MotionModel:
    """KdTree-averaged displacement predictor.

    Rebuilt from the vectors accepted on the previous frame pair, and optionally
    pre-seeded from a PIV field so the very first frames are already guided.
    """

    def __init__(self, neighbours: int = 10):
        self.neighbours = int(max(1, neighbours))
        self._tree: cKDTree | None = None
        self._uv: np.ndarray | None = None
        self._err_std: float = 0.0

    def seed_from_piv(self, x: np.ndarray, y: np.ndarray, u: np.ndarray, v: np.ndarray) -> None:
        pts = np.column_stack([np.asarray(x).ravel(), np.asarray(y).ravel()])
        uv = np.column_stack([np.asarray(u).ravel(), np.asarray(v).ravel()])
        good = np.isfinite(uv).all(axis=1)
        if good.sum() == 0:
            return
        self._tree = cKDTree(pts[good])
        self._uv = uv[good]

    def update(self, pts: np.ndarray, uv: np.ndarray, err: np.ndarray) -> None:
        if len(pts) == 0:
            return
        self._tree = cKDTree(pts)
        self._uv = uv
        self._err_std = float(np.std(err)) if len(err) > 1 else 0.0

    def predict(self, pts: np.ndarray) -> np.ndarray:
        if self._tree is None or self._uv is None or len(pts) == 0:
            return np.zeros((len(pts), 2), np.float32)
        k = min(self.neighbours, len(self._uv))
        dist, idx = self._tree.query(pts, k=k)
        if k == 1:
            return self._uv[idx].astype(np.float32)
        weights = 1.0 / np.maximum(dist, 1e-3)
        weights /= weights.sum(axis=1, keepdims=True)
        return np.einsum("nk,nkd->nd", weights, self._uv[idx]).astype(np.float32)

    @property
    def error_std(self) -> float:
        return self._err_std


def track_pair(
    prev_pts: np.ndarray,
    next_pts: np.ndarray,
    model: MotionModel,
    cfg: PTVCfg,
    max_disp: float,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Match two detection sets through the motion model.

    Returns ``(matched_prev_points, displacements, prediction_errors)``.
    """
    if len(prev_pts) == 0 or len(next_pts) == 0:
        empty = np.zeros((0, 2), np.float32)
        return empty, empty, np.zeros(0, np.float32)

    pred = model.predict(prev_pts)
    target = prev_pts + pred
    tree = cKDTree(next_pts)
    dist, idx = tree.query(target, k=1, distance_upper_bound=max_disp)
    ok = np.isfinite(dist) & (idx < len(next_pts))
    if not ok.any():
        empty = np.zeros((0, 2), np.float32)
        return empty, empty, np.zeros(0, np.float32)

    src = prev_pts[ok]
    dst = next_pts[idx[ok]]
    disp = (dst - src).astype(np.float32)
    err = np.linalg.norm(disp - pred[ok], axis=1).astype(np.float32)

    # reject matches far from what the model expected
    if len(err) > 3:
        med = np.median(err)
        mad = np.median(np.abs(err - med)) + 1e-6
        keep = err <= med + cfg.filter_std * 1.4826 * mad
        src, disp, err = src[keep], disp[keep], err[keep]
    return src, disp, err


def run_ptv(
    frames: np.ndarray,
    cfg: PTVCfg,
    piv_seed=None,
    progress=None,
) -> Tracks:
    """Track across a preprocessed frame stack. Displacements are px per interval."""
    tracks = Tracks()
    model = MotionModel(cfg.motion_neighbours)
    if piv_seed is not None and cfg.piv_guided:
        model.seed_from_piv(piv_seed.x, piv_seed.y, piv_seed.u, piv_seed.v)

    max_disp = float(cfg.max_displacement)
    if max_disp <= 0:
        max_disp = max(8.0, 0.05 * min(frames.shape[1], frames.shape[2]))

    prev_pts = detect(frames[0], cfg)
    for i in range(1, len(frames)):
        next_pts = detect(frames[i], cfg)
        src, disp, err = track_pair(prev_pts, next_pts, model, cfg, max_disp)
        if len(src):
            tracks.add(src[:, 0], src[:, 1], disp[:, 0], disp[:, 1], err)
            model.update(src + disp * 0.5, disp, err)
        prev_pts = next_pts
        if progress is not None:
            progress(i + 1, len(frames))
    return tracks

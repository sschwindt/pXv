"""Frame preprocessing: the reference workflow's recipe, plus alternatives.

The reference chain (Bro/Dorthe/Schalko 2026) is: grayscale, subtract a static
background built as the mean of every Nth frame of the whole clip, normalize,
enhance contrast. TracTrac's adaptive running background is kept as an
alternative because it suits scenes whose illumination drifts.
"""

from __future__ import annotations

import cv2
import numpy as np

from pxv.project import PreprocessCfg


def to_gray(frame: np.ndarray) -> np.ndarray:
    if frame.ndim == 3:
        return cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    return frame


def crop(frame: np.ndarray, roi: list[int] | None) -> np.ndarray:
    if not roi:
        return frame
    x0, y0, x1, y1 = roi
    h, w = frame.shape[:2]
    x0, y0 = max(0, x0), max(0, y0)
    x1, y1 = min(w, x1), min(h, y1)
    if x1 <= x0 or y1 <= y0:
        raise ValueError(f"empty ROI after clipping: {roi}")
    return frame[y0:y1, x0:x1]


def static_background(frames: np.ndarray, stride: int = 10) -> np.ndarray:
    """Mean of every ``stride``-th frame, as float32.

    This is the reference workflow's background. Because it is static it removes
    stationary sun glint and fixed bright spots cleanly, which is its main
    advantage over an adaptive background on drone footage.
    """
    if stride < 1:
        raise ValueError("stride must be >= 1")
    sel = frames[::stride]
    if len(sel) == 0:
        raise ValueError("no frames selected for the background")
    acc = np.zeros(frames.shape[1:], dtype=np.float64)
    for f in sel:
        acc += f
    return (acc / len(sel)).astype(np.float32)


def normalize(img: np.ndarray) -> np.ndarray:
    """Scale to 0..1 using robust percentiles, so a few hot pixels do not dominate."""
    lo, hi = np.percentile(img, (1.0, 99.0))
    if hi - lo < 1e-9:
        return np.zeros_like(img, dtype=np.float32)
    return np.clip((img - lo) / (hi - lo), 0.0, 1.0).astype(np.float32)


def apply_clahe(img01: np.ndarray, clip: float, grid: int) -> np.ndarray:
    u8 = np.clip(img01 * 255.0, 0, 255).astype(np.uint8)
    clahe = cv2.createCLAHE(clipLimit=float(clip), tileGridSize=(int(grid), int(grid)))
    return clahe.apply(u8).astype(np.float32) / 255.0


def preprocess_frame(
    gray: np.ndarray,
    cfg: PreprocessCfg,
    background: np.ndarray | None = None,
) -> np.ndarray:
    """Run the configured chain on one grayscale frame. Returns float32 in 0..1."""
    img = gray.astype(np.float32)
    if background is not None and cfg.background != "none":
        img = img - background
    if cfg.median and cfg.median >= 3:
        k = int(cfg.median) | 1
        img = cv2.medianBlur(img, k)
    if cfg.normalize:
        img = normalize(img)
    else:
        img = np.clip(img / 255.0, 0.0, 1.0).astype(np.float32)
    if cfg.clahe:
        img = apply_clahe(img, cfg.clahe_clip, cfg.clahe_grid)
    if cfg.invert:
        img = 1.0 - img
    return img


class AdaptiveBackground:
    """TracTrac-style running background, updated at ``speed`` per frame."""

    def __init__(self, speed: float = 0.02):
        self.speed = float(speed)
        self._bg: np.ndarray | None = None

    def update(self, gray: np.ndarray) -> np.ndarray:
        f = gray.astype(np.float32)
        if self._bg is None:
            self._bg = f.copy()
        else:
            self._bg = (1.0 - self.speed) * self._bg + self.speed * f
        return self._bg

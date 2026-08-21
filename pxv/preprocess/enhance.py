"""Frame preprocessing.

The reference chain (Bro/Dorthe/Schalko 2026) runs as four ordered stages, and
this module keeps them separate and separately reportable so each can be judged
on its own:

1. **background subtraction**: remove what does not move
2. **averaging and normalization**: the background is the mean of every Nth frame
   of the whole clip; the difference is then rescaled to a common range
3. **image enhancement**: local contrast, so faint structure survives correlation
4. **correlation filter**: applied later, in the velocimetry stage

TracTrac's adaptive running background and a rolling temporal high-pass are kept
as alternatives to stage 1, because a static background suits a steady hover and
neither of the others does.
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


def rolling_background(frames: np.ndarray, window: int) -> np.ndarray:
    """Centred rolling temporal mean, one background per frame.

    A whole-clip mean removes what is constant. A short rolling mean is a temporal
    high-pass: it also removes anything that changes slowly, which over clear water
    is the bed pattern and the illumination, leaving the fast-moving surface. The
    window has to be long compared with the surface pattern's lifetime and short
    compared with the bed's, so a few tenths of a second usually works.
    """
    if window < 2:
        raise ValueError("rolling window must be at least 2 frames")
    n = len(frames)
    cumulative = np.cumsum(np.vstack([np.zeros((1,) + frames.shape[1:], np.float64),
                                      frames.astype(np.float64)]), axis=0)
    half = window // 2
    out = np.empty(frames.shape, np.float32)
    for i in range(n):
        lo = max(0, i - half)
        hi = min(n, lo + window)
        lo = max(0, hi - window)
        out[i] = ((cumulative[hi] - cumulative[lo]) / (hi - lo)).astype(np.float32)
    return out


def normalize(img: np.ndarray, low: float = 1.0, high: float = 99.0) -> np.ndarray:
    """Scale to 0..1 using robust percentiles, so a few hot pixels do not dominate.

    Widening the percentile window keeps more of the dynamic range and stretches
    noise less, which matters when the surface signal is faint.
    """
    lo, hi = np.percentile(img, (low, high))
    if hi - lo < 1e-9:
        return np.zeros_like(img, dtype=np.float32)
    return np.clip((img - lo) / (hi - lo), 0.0, 1.0).astype(np.float32)


def apply_clahe(img01: np.ndarray, clip: float, grid: int) -> np.ndarray:
    u8 = np.clip(img01 * 255.0, 0, 255).astype(np.uint8)
    clahe = cv2.createCLAHE(clipLimit=float(clip), tileGridSize=(int(grid), int(grid)))
    return clahe.apply(u8).astype(np.float32) / 255.0


def unsharp(img01: np.ndarray, sigma: float, amount: float) -> np.ndarray:
    """Local-contrast boost that keeps fine texture the correlator can lock onto."""
    blur = cv2.GaussianBlur(img01, (0, 0), float(sigma))
    return np.clip(img01 + float(amount) * (img01 - blur), 0.0, 1.0).astype(np.float32)


def highpass(img: np.ndarray, sigma: float) -> np.ndarray:
    """Remove structure larger than ``sigma``, such as a broad illumination gradient."""
    return img - cv2.GaussianBlur(img, (0, 0), float(sigma))


def stage_metrics(img: np.ndarray) -> dict:
    """What a correlator cares about: how much usable texture there is.

    ``contrast`` is the robust dynamic range, ``texture`` the standard deviation of
    a high-pass residual, which is the part correlation actually locks onto, and
    ``saturated`` the fraction pinned at the extremes, which carries no
    information at all.
    """
    f = img.astype(np.float32)
    lo, hi = np.percentile(f, (1.0, 99.0))
    fine = f - cv2.GaussianBlur(f, (0, 0), 3.0)
    scale = max(float(f.max() - f.min()), 1e-9)
    return {
        "contrast": float(hi - lo),
        "texture": float(fine.std()),
        "saturated": float(np.mean((f <= f.min() + 1e-6) | (f >= f.max() - 1e-6))),
        "range": scale,
    }


def preprocess_frame(
    gray: np.ndarray,
    cfg: PreprocessCfg,
    background: np.ndarray | None = None,
    report: list | None = None,
) -> np.ndarray:
    """Run the four-stage chain on one grayscale frame. Returns float32 in 0..1.

    The stages run strictly in order, and each can be switched off on its own so
    its contribution is visible rather than entangled with the others.
    """
    img = gray.astype(np.float32)

    def note(label: str) -> None:
        if report is not None:
            report.append((label, stage_metrics(img)))

    note("0 input")

    # --- stage 1: background subtraction ---------------------------------
    if background is not None and cfg.background != "none":
        img = img - background
        note(f"1 background ({cfg.background})")
    if cfg.highpass_sigma > 0:
        img = highpass(img, cfg.highpass_sigma)
        note("1b high-pass")
    if cfg.median and cfg.median >= 3:
        k = int(cfg.median) | 1
        img = cv2.medianBlur(img, k)
        note("1c median")

    # --- stage 2: normalization ------------------------------------------
    if cfg.normalize:
        img = normalize(img, cfg.norm_low, cfg.norm_high)
        note(f"2 normalize ({cfg.norm_low:g}-{cfg.norm_high:g} pct)")
    else:
        img = np.clip(img / 255.0 + 0.5, 0.0, 1.0).astype(np.float32)

    # --- stage 3: image enhancement --------------------------------------
    if cfg.clahe:
        img = apply_clahe(img, cfg.clahe_clip, cfg.clahe_grid)
        note(f"3 CLAHE (clip {cfg.clahe_clip:g})")
    if cfg.unsharp_amount > 0:
        img = unsharp(img, cfg.unsharp_sigma, cfg.unsharp_amount)
        note(f"3b unsharp ({cfg.unsharp_amount:g})")

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

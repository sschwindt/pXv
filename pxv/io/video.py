"""Video reading with a frame cache.

OpenCV's frame counter is unreliable on some containers, so the reader always
decodes sequentially and exposes an explicit frame index. Preprocessed frames are
cached to a memory-mapped uint8 array, which keeps repeated calibration passes
cheap without holding a 4K clip in RAM.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

import cv2
import numpy as np


@dataclass
class VideoInfo:
    path: str
    width: int
    height: int
    fps: float
    n_frames: int
    fourcc: str

    def describe(self) -> str:
        return (
            f"{Path(self.path).name}: {self.width}x{self.height} "
            f"{self.fps:.2f} fps, {self.n_frames} frames, "
            f"{self.n_frames / self.fps:.1f} s, codec {self.fourcc}"
        )


def probe(path: str | Path) -> VideoInfo:
    cap = cv2.VideoCapture(str(path))
    if not cap.isOpened():
        raise IOError(f"cannot open video: {path}")
    try:
        raw = int(cap.get(cv2.CAP_PROP_FOURCC))
        fourcc = "".join(chr((raw >> (8 * i)) & 0xFF) for i in range(4)).strip() or "?"
        return VideoInfo(
            path=str(path),
            width=int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)),
            height=int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT)),
            fps=float(cap.get(cv2.CAP_PROP_FPS)) or 0.0,
            n_frames=int(cap.get(cv2.CAP_PROP_FRAME_COUNT)),
            fourcc=fourcc,
        )
    finally:
        cap.release()


def iter_frames(
    path: str | Path, start: int = 0, stop: int = -1, step: int = 1
) -> Iterator[tuple[int, np.ndarray]]:
    """Yield ``(frame_index, bgr_frame)`` decoding sequentially from frame 0."""
    if step < 1:
        raise ValueError("step must be >= 1")
    cap = cv2.VideoCapture(str(path))
    if not cap.isOpened():
        raise IOError(f"cannot open video: {path}")
    try:
        idx = 0
        while True:
            ok, frame = cap.read()
            if not ok:
                break
            if idx >= start and (stop < 0 or idx < stop) and (idx - start) % step == 0:
                yield idx, frame
            idx += 1
            if stop >= 0 and idx >= stop:
                break
    finally:
        cap.release()


def selected_indices(info: VideoInfo, start: int, stop: int, step: int) -> list[int]:
    last = info.n_frames if stop < 0 else min(stop, info.n_frames)
    return list(range(start, last, step))


class FrameCache:
    """Memory-mapped cache of preprocessed single-channel frames.

    The cache key covers everything that influences the pixels, so changing a
    preprocessing parameter invalidates it automatically.
    """

    def __init__(self, root: str | Path, key: dict, shape: tuple[int, int, int]):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        digest = hashlib.sha1(json.dumps(key, sort_keys=True).encode()).hexdigest()[:16]
        self.data_path = self.root / f"frames-{digest}.npy"
        self.meta_path = self.root / f"frames-{digest}.json"
        self.shape = shape
        self._key = key

    @property
    def valid(self) -> bool:
        if not (self.data_path.exists() and self.meta_path.exists()):
            return False
        meta = json.loads(self.meta_path.read_text())
        return meta.get("complete") is True and tuple(meta.get("shape", ())) == self.shape

    def open(self, mode: str = "r") -> np.ndarray:
        return np.lib.format.open_memmap(
            self.data_path, mode=mode, dtype=np.uint8, shape=self.shape
        )

    def create(self) -> np.ndarray:
        self.meta_path.write_text(json.dumps({"complete": False, "shape": list(self.shape)}))
        return np.lib.format.open_memmap(
            self.data_path, mode="w+", dtype=np.uint8, shape=self.shape
        )

    def finalize(self) -> None:
        self.meta_path.write_text(
            json.dumps({"complete": True, "shape": list(self.shape), "key": self._key})
        )

"""Co-registering a second camera stream onto the primary one.

A multispectral or thermal payload sits beside the RGB camera and sees the same
scene through different optics, so its frames must be brought into the primary
image frame before the two can be combined.

The rig is rigid and the gimbal is locked, so one homography serves the whole
clip. Estimating it from a single frame pair is fragile when the two renderings
look nothing alike, so correspondences are pooled across several frames and fitted
once.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import cv2
import numpy as np


@dataclass
class Registration:
    matrix: np.ndarray
    n_matches: int
    n_inliers: int
    residual_median: float
    residual_p95: float
    frames_used: list[int] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.n_inliers >= 12 and self.residual_median < 3.0

    def describe(self) -> str:
        scale = float(np.hypot(self.matrix[0, 0], self.matrix[1, 0]))
        return (
            f"co-registration: {self.n_inliers}/{self.n_matches} inliers over "
            f"{len(self.frames_used)} frames, residual median "
            f"{self.residual_median:.2f} px, p95 {self.residual_p95:.2f} px, "
            f"scale {scale:.4f}"
            + ("" if self.ok else "  <- weak, check the two streams show the same scene")
        )

    def warp(self, frame: np.ndarray, shape: tuple[int, int]) -> np.ndarray:
        h, w = shape[:2]
        return cv2.warpPerspective(frame, self.matrix, (w, h))


def register_streams(primary_path, secondary_path, frames: list[int],
                     ratio: float = 0.78, ransac_thresh: float = 2.0,
                     progress=None) -> Registration:
    """Fit one homography mapping the secondary stream into the primary frame."""
    sift = cv2.SIFT_create(nfeatures=6000)
    matcher = cv2.BFMatcher()
    clahe = cv2.createCLAHE(3.0, (8, 8))

    cap_p = cv2.VideoCapture(str(primary_path))
    cap_s = cv2.VideoCapture(str(secondary_path))
    if not (cap_p.isOpened() and cap_s.isOpened()):
        cap_p.release(), cap_s.release()
        raise IOError("cannot open both streams for co-registration")

    src: list = []
    dst: list = []
    used: list[int] = []
    try:
        for k, index in enumerate(frames):
            cap_p.set(cv2.CAP_PROP_POS_FRAMES, int(index))
            ok_p, prim = cap_p.read()
            cap_s.set(cv2.CAP_PROP_POS_FRAMES, int(index))
            ok_s, sec = cap_s.read()
            if not (ok_p and ok_s):
                continue
            a = clahe.apply(cv2.cvtColor(sec, cv2.COLOR_BGR2GRAY))
            b = clahe.apply(cv2.cvtColor(prim, cv2.COLOR_BGR2GRAY))
            ka, da = sift.detectAndCompute(a, None)
            kb, db = sift.detectAndCompute(b, None)
            if da is None or db is None:
                continue
            good = [m for m, n in matcher.knnMatch(da, db, k=2) if m.distance < ratio * n.distance]
            src += [ka[m.queryIdx].pt for m in good]
            dst += [kb[m.trainIdx].pt for m in good]
            used.append(int(index))
            if progress is not None:
                progress(k + 1, len(frames))
    finally:
        cap_p.release()
        cap_s.release()

    if len(src) < 8:
        raise RuntimeError(
            f"only {len(src)} correspondences between the two streams; they may not "
            "show the same scene, or the renderings are too dissimilar to match"
        )
    s = np.float32(src).reshape(-1, 1, 2)
    d = np.float32(dst).reshape(-1, 1, 2)
    matrix, inl = cv2.findHomography(s, d, cv2.RANSAC, ransac_thresh)
    if matrix is None:
        raise RuntimeError("co-registration homography fit failed")
    proj = cv2.perspectiveTransform(s, matrix).reshape(-1, 2)
    err = np.linalg.norm(proj - d.reshape(-1, 2), axis=1)
    keep = inl.ravel().astype(bool)
    return Registration(
        matrix=matrix, n_matches=len(src), n_inliers=int(keep.sum()),
        residual_median=float(np.median(err[keep])),
        residual_p95=float(np.percentile(err[keep], 95)),
        frames_used=used,
    )

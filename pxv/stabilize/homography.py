"""Wind-gust stabilisation by feature-based frame registration.

Water is non-rigid, so the transform must be estimated from *static* pixels only:
banks, structures, rocks, logs. Following Ioli et al. (2020), the moving water is
masked out of feature detection.

The reference study chains each frame to its immediate predecessor. That
accumulates drift over a long clip, so this implementation tracks frame to frame
for speed but re-anchors against a global reference every
``keyframe_interval`` frames, and publishes the residual so the difference is
measurable rather than assumed.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import cv2
import numpy as np

from pxv.project import StabilizeCfg


@dataclass
class StabilizationResult:
    #: 3x3 transform mapping each frame onto the reference frame
    transforms: list[np.ndarray] = field(default_factory=list)
    #: RMS reprojection residual of the inliers, in pixels, per frame
    residuals: list[float] = field(default_factory=list)
    #: surviving inlier count per frame
    inliers: list[int] = field(default_factory=list)
    #: frames where estimation failed and identity was substituted
    failures: list[int] = field(default_factory=list)

    def summary(self) -> str:
        if not self.residuals:
            return "stabilisation: no frames"
        r = np.asarray(self.residuals)
        n = np.asarray(self.inliers)
        return (
            f"stabilisation: residual median {np.median(r):.3f} px, "
            f"p95 {np.percentile(r, 95):.3f} px, max {r.max():.3f} px | "
            f"inliers median {int(np.median(n))} | failures {len(self.failures)}"
        )

    def apparent_motion_px(self) -> np.ndarray:
        """Translation of each frame relative to the reference, in pixels.

        With no measured wind data this is the best available proxy for gust
        activity: it is what the aircraft actually did.
        """
        return np.array([[float(H[0, 2]), float(H[1, 2])] for H in self.transforms])


def _detect(gray01: np.ndarray, mask: np.ndarray | None, cfg: StabilizeCfg) -> np.ndarray | None:
    u8 = np.clip(gray01 * 255.0, 0, 255).astype(np.uint8)
    pts = cv2.goodFeaturesToTrack(
        u8,
        maxCorners=int(cfg.max_features),
        qualityLevel=float(cfg.quality),
        minDistance=int(cfg.min_distance),
        mask=mask,
    )
    return pts


def _estimate(src: np.ndarray, dst: np.ndarray, cfg: StabilizeCfg):
    if len(src) < 4:
        return None, None
    if cfg.model == "similarity":
        M, inl = cv2.estimateAffinePartial2D(
            src, dst, method=cv2.RANSAC, ransacReprojThreshold=float(cfg.ransac_thresh)
        )
        if M is None:
            return None, None
        H = np.vstack([M, [0.0, 0.0, 1.0]])
        return H, inl
    H, inl = cv2.findHomography(
        src, dst, method=cv2.RANSAC, ransacReprojThreshold=float(cfg.ransac_thresh)
    )
    return H, inl


def _residual(src: np.ndarray, dst: np.ndarray, H: np.ndarray, inl: np.ndarray | None) -> float:
    if H is None or len(src) == 0:
        return float("nan")
    proj = cv2.perspectiveTransform(src.reshape(-1, 1, 2), H).reshape(-1, 2)
    err = np.linalg.norm(proj - dst.reshape(-1, 2), axis=1)
    if inl is not None:
        keep = inl.ravel().astype(bool)
        if keep.any():
            err = err[keep]
    return float(np.sqrt(np.mean(err**2)))


def condition_number(points: np.ndarray) -> float:
    """Spread diagnostic for a control-point set.

    Near-collinear or clustered points give a badly conditioned projective fit.
    A large value here is the signal to fall back to a similarity transform.
    """
    p = np.asarray(points, dtype=np.float64).reshape(-1, 2)
    if len(p) < 3:
        return float("inf")
    centred = p - p.mean(axis=0)
    scale = np.abs(centred).max()
    if scale < 1e-12:
        return float("inf")
    centred /= scale
    sv = np.linalg.svd(centred, compute_uv=False)
    if sv[-1] < 1e-12:
        return float("inf")
    return float(sv[0] / sv[-1])


def stabilize(
    frames: np.ndarray,
    cfg: StabilizeCfg,
    mask: np.ndarray | None = None,
    progress=None,
) -> StabilizationResult:
    """Register every frame onto frame 0.

    ``frames`` is a stack of float32 images in 0..1.
    """
    result = StabilizationResult()
    n = len(frames)
    if n == 0:
        return result
    identity = np.eye(3, dtype=np.float64)
    if not cfg.enabled or cfg.model == "none":
        result.transforms = [identity.copy() for _ in range(n)]
        result.residuals = [0.0] * n
        result.inliers = [0] * n
        return result

    ref = frames[0]
    ref_pts = _detect(ref, mask, cfg)
    if ref_pts is None or len(ref_pts) < 4:
        raise RuntimeError(
            "stabilisation found too few features in the reference frame; "
            "check the static mask, or disable stabilisation for a water-filled scene"
        )

    result.transforms.append(identity.copy())
    result.residuals.append(0.0)
    result.inliers.append(len(ref_pts))

    prev = ref
    prev_pts = ref_pts
    prev_H = identity.copy()

    lk = dict(winSize=(21, 21), maxLevel=3,
              criteria=(cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_COUNT, 30, 0.01))

    for i in range(1, n):
        cur = frames[i]
        anchor_to_ref = (i % max(1, int(cfg.keyframe_interval))) == 0
        src_img, src_pts, base_H = (
            (ref, ref_pts, identity) if anchor_to_ref else (prev, prev_pts, prev_H)
        )

        a = np.clip(src_img * 255.0, 0, 255).astype(np.uint8)
        b = np.clip(cur * 255.0, 0, 255).astype(np.uint8)
        nxt, status, _ = cv2.calcOpticalFlowPyrLK(a, b, src_pts, None, **lk)
        H = None
        inl = None
        if nxt is not None and status is not None:
            keep = status.ravel().astype(bool)
            p0 = src_pts[keep].reshape(-1, 2)
            p1 = nxt[keep].reshape(-1, 2)
            # map current frame onto the source frame, then onto the reference
            H_step, inl = _estimate(p1, p0, cfg)
            if H_step is not None:
                H = base_H @ H_step
                result.residuals.append(_residual(p1, p0, H_step, inl))
                result.inliers.append(int(inl.sum()) if inl is not None else len(p0))

        if H is None:
            H = prev_H.copy()
            result.failures.append(i)
            result.residuals.append(float("nan"))
            result.inliers.append(0)

        result.transforms.append(H)
        prev_H = H
        prev = cur
        # refresh the tracked set periodically so features are not lost to drift
        if anchor_to_ref or (nxt is not None and status is not None and status.sum() < 0.5 * len(src_pts)):
            fresh = _detect(cur, mask, cfg)
            prev_pts = fresh if fresh is not None and len(fresh) >= 4 else src_pts
            prev = cur
        else:
            prev_pts = nxt[status.ravel().astype(bool)].reshape(-1, 1, 2)

        if progress is not None:
            progress(i + 1, n)

    return result


def warp_to_reference(frame: np.ndarray, H: np.ndarray) -> np.ndarray:
    h, w = frame.shape[:2]
    return cv2.warpPerspective(frame, H, (w, h), flags=cv2.INTER_LINEAR,
                               borderMode=cv2.BORDER_CONSTANT, borderValue=0)

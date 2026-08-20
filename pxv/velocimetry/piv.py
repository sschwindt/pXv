"""Multi-pass FFT cross-correlation PIV.

Owned rather than delegated, for three reasons: the numerics are small enough to
verify directly, the calibration optimizer needs parameters a library will not
expose, and the correlation-coefficient threshold has to mean exactly what the
reference workflow means by it (0.7 on a normalised peak).

Normalisation follows the usual PIV convention: each interrogation window is
zero-meaned, correlated by FFT, then divided by ``n_pixels * std_a * std_b``. At
the peak this equals the Pearson coefficient between the overlapping windows,
which is the quantity the 0.7 threshold refers to.
"""

from __future__ import annotations

import warnings
from dataclasses import dataclass

import numpy as np
from numpy.lib.stride_tricks import sliding_window_view

from pxv.project import PIVCfg


@dataclass
class PIVField:
    x: np.ndarray          # window centre column, pixels
    y: np.ndarray          # window centre row, pixels
    u: np.ndarray          # displacement along +x, pixels per frame interval
    v: np.ndarray          # displacement along +y, pixels per frame interval
    corr: np.ndarray       # normalised correlation peak
    snr: np.ndarray        # primary-to-secondary peak ratio
    valid: np.ndarray      # bool mask after validation

    @property
    def shape(self) -> tuple[int, int]:
        return self.u.shape

    def magnitude(self) -> np.ndarray:
        return np.hypot(self.u, self.v)

    def yield_fraction(self) -> float:
        return float(self.valid.mean()) if self.valid.size else 0.0


def grid_positions(shape: tuple[int, int], window: int, overlap: float) -> tuple[np.ndarray, np.ndarray]:
    h, w = shape
    step = max(1, int(round(window * (1.0 - overlap))))
    ys = np.arange(0, h - window + 1, step)
    xs = np.arange(0, w - window + 1, step)
    if len(ys) == 0 or len(xs) == 0:
        raise ValueError(f"window {window} px does not fit in a {w}x{h} image")
    return xs, ys


def _extract(img: np.ndarray, xs: np.ndarray, ys: np.ndarray, window: int) -> np.ndarray:
    """Stack of (ny, nx, window, window) interrogation windows."""
    view = sliding_window_view(img, (window, window))
    return view[np.ix_(ys, xs)]


def _extract_shifted(
    img: np.ndarray, xs: np.ndarray, ys: np.ndarray, window: int, dx: np.ndarray, dy: np.ndarray
) -> np.ndarray:
    """Windows taken at integer offsets, clamped to the image, for window shifting."""
    h, w = img.shape
    y0 = np.clip(ys[:, None] + dy, 0, h - window)
    x0 = np.clip(xs[None, :] + dx, 0, w - window)
    off = np.arange(window)
    rows = y0[..., None, None] + off[None, None, :, None]
    cols = x0[..., None, None] + off[None, None, None, :]
    return img[rows, cols].astype(np.float32)


def window_texture(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    """Smaller of the two window standard deviations, as a texture measure."""
    return np.minimum(a.std(axis=(-2, -1)), b.std(axis=(-2, -1)))


def correlation_planes(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    """Normalised cross-correlation of matched window stacks, FFT-shifted to centre."""
    a = a.astype(np.float32)
    b = b.astype(np.float32)
    a = a - a.mean(axis=(-2, -1), keepdims=True)
    b = b - b.mean(axis=(-2, -1), keepdims=True)
    n = a.shape[-1] * a.shape[-2]
    sa = a.std(axis=(-2, -1), keepdims=True)
    sb = b.std(axis=(-2, -1), keepdims=True)
    denom = np.maximum(n * sa * sb, 1e-12)
    fa = np.fft.rfft2(a, axes=(-2, -1))
    fb = np.fft.rfft2(b, axes=(-2, -1))
    corr = np.fft.irfft2(np.conj(fa) * fb, s=a.shape[-2:], axes=(-2, -1))
    corr = np.fft.fftshift(corr, axes=(-2, -1)) / denom
    return corr.astype(np.float32)


def peak_find(
    planes: np.ndarray,
    subpixel: str = "gaussian",
    snr_method: str = "peak2rms",
    exclusion_px: int = 5,
) -> tuple[np.ndarray, ...]:
    """Locate the displacement peak in each correlation plane.

    Fully vectorised over the window grid: a 1400-frame clip means millions of
    peak searches, and a per-window Python loop makes the tool unusable.

    Returns ``(du, dv, corr, snr)``, displacements relative to the plane centre in
    pixels.
    """
    ny, nx, wy, wx = planes.shape
    flat = planes.reshape(ny, nx, wy * wx)
    k = np.argmax(flat, axis=-1)
    pi, pj = np.divmod(k, wx)
    peak = np.take_along_axis(flat, k[..., None], axis=-1)[..., 0]

    # Detection quality. The exclusion radius must clear the primary peak's own
    # shoulder: these correlation peaks are several pixels wide, so a tight
    # exclusion makes every peak-to-second-peak ratio collapse to about 1.0 and
    # silently rejects every real vector.
    r = int(max(1, exclusion_px))
    yy = np.arange(wy)[None, None, :, None]
    xx = np.arange(wx)[None, None, None, :]
    near = (np.abs(yy - pi[..., None, None]) <= r) & (np.abs(xx - pj[..., None, None]) <= r)
    with np.errstate(divide="ignore", invalid="ignore"):
        if snr_method == "peak2peak":
            second = np.where(near, -np.inf, planes).reshape(ny, nx, -1).max(axis=-1)
            snr = np.where(second > 1e-6, peak / second, np.where(peak > 0, np.inf, 0.0))
        else:
            # peak-to-RMS of the background plane: well behaved for broad peaks
            bg = np.where(near, 0.0, planes)
            cnt = np.maximum((~near).sum(axis=(-2, -1)), 1)
            rms = np.sqrt((bg ** 2).sum(axis=(-2, -1)) / cnt)
            snr = np.where(rms > 1e-9, peak / rms, np.where(peak > 0, np.inf, 0.0))
    snr = np.nan_to_num(snr, nan=0.0, posinf=1e6)

    def gather(di: int, dj: int) -> np.ndarray:
        ii = np.clip(pi + di, 0, wy - 1)
        jj = np.clip(pj + dj, 0, wx - 1)
        return np.take_along_axis(flat, (ii * wx + jj)[..., None], axis=-1)[..., 0]

    up, dn = gather(-1, 0), gather(1, 0)
    lf, rt = gather(0, -1), gather(0, 1)

    eps = 1e-12
    if subpixel == "gaussian":
        lg = lambda v: np.log(np.maximum(v, eps))
        m, u_, d_, l_, r_ = lg(peak), lg(up), lg(dn), lg(lf), lg(rt)
    else:
        m, u_, d_, l_, r_ = peak, up, dn, lf, rt

    def refine(centre, lo, hi):
        den = 2.0 * (lo - 2.0 * centre + hi)
        with np.errstate(divide="ignore", invalid="ignore"):
            d = np.where(np.abs(den) < 1e-9, 0.0, (lo - hi) / den)
        return np.clip(np.nan_to_num(d), -1.0, 1.0)

    dy = refine(m, u_, d_)
    dx = refine(m, l_, r_)
    # a peak on the plane border has no neighbourhood to fit
    dy = np.where((pi > 0) & (pi < wy - 1), dy, 0.0)
    dx = np.where((pj > 0) & (pj < wx - 1), dx, 0.0)

    du = (pj - wx // 2) + dx
    dv = (pi - wy // 2) + dy
    bad = peak <= 0
    du = np.where(bad, np.nan, du).astype(np.float32)
    dv = np.where(bad, np.nan, dv).astype(np.float32)
    return du, dv, peak.astype(np.float32), snr.astype(np.float32)


def _neighbourhood(field: np.ndarray) -> np.ndarray:
    """(ny, nx, 8) stack of each cell's neighbours, with the centre removed."""
    padded = np.pad(field, 1, mode="constant", constant_values=np.nan)
    block = sliding_window_view(padded, (3, 3)).reshape(*field.shape, 9)
    return np.delete(block, 4, axis=-1)


def normalized_median_test(u: np.ndarray, v: np.ndarray, threshold: float, eps: float = 0.1) -> np.ndarray:
    """Westerweel and Scarano normalised median test. True marks an outlier."""
    bad = np.zeros(u.shape, dtype=bool)
    for comp in (u, v):
        neigh = _neighbourhood(comp)
        with np.errstate(invalid="ignore"), warnings.catch_warnings():
            warnings.simplefilter("ignore", RuntimeWarning)
            med = np.nanmedian(neigh, axis=-1)
            resid = np.nanmedian(np.abs(neigh - med[..., None]), axis=-1)
            score = np.abs(comp - med) / (resid + eps)
        enough = np.sum(np.isfinite(neigh), axis=-1) >= 2
        bad |= ~np.isfinite(comp) | ~enough | (np.nan_to_num(score, nan=np.inf) > threshold)
    return bad


def _fill(u: np.ndarray, valid: np.ndarray) -> np.ndarray:
    """Replace invalid vectors with the median of their valid neighbours."""
    masked = np.where(valid, u, np.nan)
    neigh = _neighbourhood(masked)
    with np.errstate(invalid="ignore"), warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        med = np.nanmedian(neigh, axis=-1)
    out = np.where(valid, u, med)
    return np.nan_to_num(out, nan=0.0).astype(np.float32)


def piv_pair(a: np.ndarray, b: np.ndarray, cfg: PIVCfg) -> PIVField:
    """Multi-pass PIV on one image pair. Displacements are pixels per interval."""
    if a.shape != b.shape:
        raise ValueError("image pair must have identical shape")
    windows = list(cfg.windows) or [64]
    u_prev = v_prev = None
    xs = ys = None
    du = dv = cc = snr = None

    for p, window in enumerate(windows):
        xs, ys = grid_positions(a.shape, window, cfg.overlap)
        ny, nx = len(ys), len(xs)

        if u_prev is None:
            pred_u = np.zeros((ny, nx), dtype=np.float32)
            pred_v = np.zeros((ny, nx), dtype=np.float32)
        else:
            pred_u = _resample(u_prev, (ny, nx))
            pred_v = _resample(v_prev, (ny, nx))

        # symmetric integer window shifting: split the predicted displacement
        off_a_x = np.round(-pred_u / 2.0).astype(int)
        off_a_y = np.round(-pred_v / 2.0).astype(int)
        off_b_x = np.round(pred_u / 2.0).astype(int)
        off_b_y = np.round(pred_v / 2.0).astype(int)
        applied = (off_b_x - off_a_x).astype(np.float32), (off_b_y - off_a_y).astype(np.float32)

        wa = _extract_shifted(a, xs, ys, window, off_a_x, off_a_y)
        wb = _extract_shifted(b, xs, ys, window, off_b_x, off_b_y)
        texture = window_texture(wa, wb)
        tex_ok = texture > max(float(cfg.min_texture), 1e-6)
        planes = correlation_planes(wa, wb)
        rdu, rdv, cc, snr = peak_find(planes, cfg.subpixel, cfg.snr_method, cfg.snr_exclusion_px)

        du = rdu + applied[0]
        dv = rdv + applied[1]

        valid = _validate(du, dv, cc, snr, cfg, tex_ok)
        if p < len(windows) - 1 and cfg.fill:
            u_prev = _fill(du, valid)
            v_prev = _fill(dv, valid)
        else:
            u_prev, v_prev = du, dv

    valid = _validate(du, dv, cc, snr, cfg, tex_ok)
    x = (xs + windows[-1] / 2.0).astype(np.float32)
    y = (ys + windows[-1] / 2.0).astype(np.float32)
    xx, yy = np.meshgrid(x, y)
    return PIVField(x=xx, y=yy, u=du, v=dv, corr=cc, snr=snr, valid=valid)


def _validate(du, dv, cc, snr, cfg: PIVCfg, texture_ok: np.ndarray | None = None) -> np.ndarray:
    valid = np.isfinite(du) & np.isfinite(dv)
    if texture_ok is not None:
        # A window with no texture correlates perfectly with itself at zero lag,
        # which otherwise lets flat background outrank the moving water.
        valid &= texture_ok
    valid &= cc >= cfg.corr_threshold
    valid &= snr >= cfg.snr_threshold
    if cfg.median_threshold > 0:
        u = np.where(valid, du, np.nan)
        v = np.where(valid, dv, np.nan)
        valid &= ~normalized_median_test(u, v, cfg.median_threshold)
    return valid


def _resample(field: np.ndarray, shape: tuple[int, int]) -> np.ndarray:
    """Nearest-neighbour resample of a coarse field onto a finer grid."""
    ny, nx = shape
    sy, sx = field.shape
    jj = np.clip((np.arange(ny) * sy / ny).astype(int), 0, sy - 1)
    ii = np.clip((np.arange(nx) * sx / nx).astype(int), 0, sx - 1)
    return field[np.ix_(jj, ii)].astype(np.float32)


def piv_ensemble(pairs, cfg: PIVCfg, progress=None) -> PIVField:
    """Ensemble correlation: average the correlation planes, then find one peak.

    This is the right tool for sparse seeding, where any single pair gives a noisy
    correlation but the ensemble does not. Single pass, at the finest window.
    """
    window = list(cfg.windows)[-1]
    acc = None
    texture_acc = None
    xs = ys = None
    count = 0
    for k, (a, b) in enumerate(pairs):
        xs, ys = grid_positions(a.shape, window, cfg.overlap)
        zeros = np.zeros((len(ys), len(xs)), dtype=int)
        wa = _extract_shifted(a, xs, ys, window, zeros, zeros)
        wb = _extract_shifted(b, xs, ys, window, zeros, zeros)
        tex_ok = window_texture(wa, wb) > max(float(cfg.min_texture), 1e-6)
        texture_acc = tex_ok.astype(np.float32) if texture_acc is None else texture_acc + tex_ok
        planes = correlation_planes(wa, wb)
        acc = planes if acc is None else acc + planes
        count += 1
        if progress is not None:
            progress(k + 1, None)
    if acc is None:
        raise ValueError("no image pairs supplied")
    acc /= count
    du, dv, cc, snr = peak_find(acc, cfg.subpixel, cfg.snr_method, cfg.snr_exclusion_px)
    valid = _validate(du, dv, cc, snr, cfg, texture_acc > 0.5 * count)
    x = (xs + window / 2.0).astype(np.float32)
    y = (ys + window / 2.0).astype(np.float32)
    xx, yy = np.meshgrid(x, y)
    return PIVField(x=xx, y=yy, u=du, v=dv, corr=cc, snr=snr, valid=valid)

"""Streamline images over a faded backdrop of the footage.

Colour maps are restricted to perceptually uniform, colour-vision-deficiency-safe
sequential maps. ``cividis`` is the default because it was designed for CVD.
Rainbow maps are refused outright: they invent structure that is not in the data.
"""

from __future__ import annotations

from pathlib import Path

import matplotlib
import numpy as np

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.colors import Normalize  # noqa: E402

#: sequential, perceptually uniform, safe under the common CVD types
SAFE_CMAPS = {"cividis", "viridis", "magma", "inferno", "plasma", "batlow", "mako", "rocket"}
BANNED_CMAPS = {"jet", "rainbow", "hsv", "gist_rainbow", "nipy_spectral", "turbo"}


def check_cmap(name: str) -> str:
    if name in BANNED_CMAPS:
        raise ValueError(
            f"{name!r} is not colour-vision-deficiency safe and is not perceptually uniform; "
            f"use one of {sorted(SAFE_CMAPS)}"
        )
    if name not in SAFE_CMAPS:
        try:
            matplotlib.colormaps[name]
        except KeyError as exc:
            raise ValueError(f"unknown colormap {name!r}") from exc
    return name


def _scale_bar(ax, scale_m_per_px: float, img_w: int, colour: str) -> None:
    if not np.isfinite(scale_m_per_px) or scale_m_per_px <= 0:
        return
    target_px = img_w * 0.22
    raw = target_px * scale_m_per_px
    exp = np.floor(np.log10(raw))
    nice = min([1, 2, 5, 10], key=lambda m: abs(m * 10**exp - raw)) * 10**exp
    bar_px = nice / scale_m_per_px
    x0 = img_w * 0.04
    y0 = ax.get_ylim()[0] * 0.955 if ax.get_ylim()[0] > ax.get_ylim()[1] else ax.get_ylim()[1] * 0.955
    ax.plot([x0, x0 + bar_px], [y0, y0], color=colour, lw=3, solid_capstyle="butt", zorder=6)
    label = f"{nice:g} m" if nice >= 1 else f"{nice * 100:g} cm"
    ax.text(x0 + bar_px / 2, y0, label, color=colour, ha="center", va="bottom",
            fontsize=8, zorder=6)


def _north_arrow(ax, img_w: int, img_h: int, colour: str, yaw_deg: float = 0.0) -> None:
    cx, cy = img_w * 0.94, img_h * 0.10
    L = img_h * 0.05
    a = np.radians(yaw_deg)
    dx, dy = L * np.sin(a), -L * np.cos(a)
    ax.annotate("", xy=(cx + dx, cy + dy), xytext=(cx, cy),
                arrowprops=dict(arrowstyle="-|>", color=colour, lw=1.6), zorder=6)
    ax.text(cx + dx * 1.5, cy + dy * 1.5, "N", color=colour, ha="center", va="center",
            fontsize=9, fontweight="bold", zorder=6)


def write_world_file(path: Path, scale_m_per_px: float, origin_xy: tuple[float, float],
                     epsg: int) -> None:
    """Six-line world file plus a .prj, so the JPG drops into QGIS georeferenced."""
    jgw = path.with_suffix(".jgw")
    x0, y0 = origin_xy
    jgw.write_text(
        f"{scale_m_per_px:.10f}\n0.0\n0.0\n{-scale_m_per_px:.10f}\n"
        f"{x0:.6f}\n{y0:.6f}\n"
    )
    if epsg:
        try:
            from pyproj import CRS
            path.with_suffix(".prj").write_text(CRS.from_epsg(epsg).to_wkt())
        except Exception:  # pragma: no cover
            pass


def plot_streamlines(
    result,
    outpath: str | Path,
    backdrop: np.ndarray | None = None,
    cmap: str = "cividis",
    backdrop_alpha: float = 0.5,
    dpi: int = 200,
    density: float = 1.6,
    title: str | None = None,
) -> Path:
    """Streamlines coloured by speed over a 50 % opacity frame of the footage."""
    cmap = check_cmap(cmap)
    outpath = Path(outpath)
    outpath.parent.mkdir(parents=True, exist_ok=True)

    fld = result.field
    u, v = result.velocity_world()
    speed = np.hypot(u, v)
    valid = fld.valid

    # streamplot needs a complete grid; fill gaps with the local median
    uu = np.where(valid, u, np.nan)
    vv = np.where(valid, v, np.nan)
    fillu = np.nanmedian(uu) if np.isfinite(uu).any() else 0.0
    fillv = np.nanmedian(vv) if np.isfinite(vv).any() else 0.0
    uu = np.nan_to_num(uu, nan=fillu)
    vv = np.nan_to_num(vv, nan=fillv)
    sp = np.where(valid, speed, np.nan)

    gx = fld.x[0, :]
    gy = fld.y[:, 0]

    h, w = (backdrop.shape[:2] if backdrop is not None
            else (int(gy[-1] + fld.cell_px), int(gx[-1] + fld.cell_px)))

    fig_w = 9.0
    fig, ax = plt.subplots(figsize=(fig_w, fig_w * h / w), dpi=dpi)
    fig.patch.set_facecolor("white")
    ax.set_facecolor("white")

    if backdrop is not None:
        img = backdrop
        if img.ndim == 3:
            img = img[..., ::-1]  # BGR to RGB
        ax.imshow(img, cmap="gray" if img.ndim == 2 else None,
                  alpha=backdrop_alpha, extent=(0, w, h, 0), zorder=1)

    vmax = float(np.nanpercentile(sp, 98)) if np.isfinite(sp).any() else 1.0
    norm = Normalize(vmin=0.0, vmax=max(vmax, 1e-9))
    strm = ax.streamplot(
        gx, gy, uu, vv, color=np.nan_to_num(sp, nan=0.0), cmap=cmap, norm=norm,
        density=density, linewidth=1.1, arrowsize=0.9, zorder=4,
    )
    strm.lines.set_alpha(1.0)

    ax.set_xlim(0, w)
    ax.set_ylim(h, 0)
    ax.set_xticks([])
    ax.set_yticks([])
    for spine in ax.spines.values():
        spine.set_visible(False)

    unit = f"{result.transform.units}/s"
    cbar = fig.colorbar(strm.lines, ax=ax, fraction=0.035, pad=0.02)
    cbar.set_label(f"surface velocity magnitude [{unit}]", fontsize=9)
    cbar.ax.tick_params(labelsize=8)

    ink = "#111111"
    _scale_bar(ax, result.transform.scale_m_per_px, w, ink)
    yaw = result.telemetry.median("gb_yaw") or 0.0
    if result.transform.georeferenced:
        _north_arrow(ax, w, h, ink, yaw)
    if title:
        ax.set_title(title, fontsize=10, loc="left")

    fig.tight_layout()
    fig.savefig(outpath, format="jpeg", dpi=dpi, bbox_inches="tight",
                facecolor="white", pil_kwargs={"quality": 92})
    plt.close(fig)

    t = result.transform
    if np.isfinite(t.scale_m_per_px) and t.epsg:
        ox, oy = t.to_world(np.array([0.0]), np.array([0.0]))
        write_world_file(outpath, t.scale_m_per_px, (float(ox[0]), float(oy[0])), t.epsg)
    return outpath


def plot_quicklook(result, outpath: str | Path, backdrop: np.ndarray | None = None,
                   cmap: str = "cividis", dpi: int = 150) -> Path:
    """Diagnostic panel: speed map, vector overlay, sample count, stabilisation residual."""
    cmap = check_cmap(cmap)
    outpath = Path(outpath)
    outpath.parent.mkdir(parents=True, exist_ok=True)
    fld = result.field
    u, v = result.velocity_world()
    speed = np.where(fld.valid, np.hypot(u, v), np.nan)

    fig, axes = plt.subplots(2, 2, figsize=(11, 9), dpi=dpi)
    fig.patch.set_facecolor("white")
    unit = f"{result.transform.units}/s"

    im = axes[0, 0].imshow(speed, cmap=cmap, origin="upper")
    axes[0, 0].set_title(f"speed [{unit}]", fontsize=10)
    fig.colorbar(im, ax=axes[0, 0], fraction=0.04)

    if backdrop is not None:
        bg = backdrop[..., ::-1] if backdrop.ndim == 3 else backdrop
        axes[0, 1].imshow(bg, cmap="gray" if backdrop.ndim == 2 else None, alpha=0.5)
    step = max(1, fld.u.shape[0] // 30)
    axes[0, 1].quiver(fld.x[::step, ::step], fld.y[::step, ::step],
                      np.where(fld.valid, u, 0)[::step, ::step],
                      -np.where(fld.valid, v, 0)[::step, ::step],
                      np.nan_to_num(speed)[::step, ::step], cmap=cmap, scale_units="xy")
    axes[0, 1].set_title("vectors over frame", fontsize=10)

    im = axes[1, 0].imshow(fld.n, cmap="magma", origin="upper")
    axes[1, 0].set_title("samples per cell", fontsize=10)
    fig.colorbar(im, ax=axes[1, 0], fraction=0.04)

    res = np.asarray(result.stabilization.residuals, dtype=float)
    axes[1, 1].plot(res, lw=0.9, color="#1C4A75")
    axes[1, 1].set_title("stabilisation residual [px]", fontsize=10)
    axes[1, 1].set_xlabel("frame")
    axes[1, 1].grid(alpha=0.3)

    for ax in (axes[0, 0], axes[0, 1], axes[1, 0]):
        ax.set_xticks([]); ax.set_yticks([])
    fig.tight_layout()
    fig.savefig(outpath, dpi=dpi, facecolor="white")
    plt.close(fig)
    return outpath

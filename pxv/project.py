"""Project schema: every pXv run is fully described by one YAML file.

The GUI only edits this structure. ``pxv run project.yaml`` reproduces a run
headlessly, so nothing the GUI can do is unreachable from the command line.
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

import yaml


@dataclass
class VideoCfg:
    path: str = ""
    #: optional second camera stream showing the same scene, e.g. multispectral.
    #: Co-registered onto the primary frame before use.
    secondary_path: str = ""
    #: first frame index to process
    start: int = 0
    #: last frame index, exclusive; ``-1`` means end of file
    stop: int = -1
    #: process every ``step``-th frame; also sets the velocimetry time base
    step: int = 1
    #: frames per second; ``0`` means read it from the container
    fps: float = 0.0
    #: region of interest in pixels as (xmin, ymin, xmax, ymax); empty means full frame
    roi: list[int] = field(default_factory=list)


@dataclass
class PreprocessCfg:
    """The four ordered stages of the reference chain.

    1. background subtraction, 2. normalization, 3. enhancement, and then the
    correlation filter in the velocimetry stage. Each is separately switchable so
    its contribution can be judged on its own.
    """

    grayscale: bool = True
    #: static background = mean of every ``bg_stride``-th frame over the whole clip
    background: Literal["static", "rolling", "adaptive", "none"] = "static"
    bg_stride: int = 10
    #: window in frames for the "rolling" background, a temporal high-pass that
    #: suppresses a stationary bed seen through clear water
    bg_window: int = 15
    #: adaptation speed when ``background == "adaptive"``
    bg_speed: float = 0.02
    #: stage 1, optional: remove structure larger than this, e.g. an illumination
    #: gradient. 0 disables.
    highpass_sigma: float = 0.0
    median: int = 0  # kernel size, 0 disables
    #: stage 2
    normalize: bool = True
    #: percentile window for the normalization. A wider window stretches noise
    #: less, which matters when the surface signal is faint.
    norm_low: float = 1.0
    norm_high: float = 99.0
    #: stage 3
    clahe: bool = True
    clahe_clip: float = 2.0
    clahe_grid: int = 8
    #: unsharp mask, applied after CLAHE. 0 disables.
    unsharp_sigma: float = 2.0
    unsharp_amount: float = 0.0
    invert: bool = False  # track dark objects on a bright surface


@dataclass
class StabilizeCfg:
    enabled: bool = True
    #: "homography" (8 dof) or "similarity" (4 dof, needs far less feature spread)
    model: Literal["homography", "similarity", "none"] = "similarity"
    max_features: int = 2000
    quality: float = 0.01
    min_distance: int = 12
    ransac_thresh: float = 3.0
    #: re-match against the global reference frame every N frames to stop drift
    keyframe_interval: int = 30
    #: polygons of static pixels (banks, bars, structures), taken as a union.
    #: Empty means "whole frame minus water_mask".
    static_mask: list[list[list[float]]] = field(default_factory=list)
    #: polygons of moving water, excluded from feature detection
    water_mask: list[list[list[float]]] = field(default_factory=list)


@dataclass
class GeometryCfg:
    """Pixel-to-metre scaling. Exactly one mode is active."""

    mode: Literal["none", "known_distance", "height", "camera", "gcp", "orthophoto"] = "none"
    #: target CRS as an EPSG code; 25832 is UTM zone 32N. 0 means image coordinates.
    epsg: int = 0

    # --- known_distance: two image points spanning a known real length
    known_p1: list[float] = field(default_factory=list)
    known_p2: list[float] = field(default_factory=list)
    known_length_m: float = 0.0

    # --- height / camera: flight geometry
    #: height above the takeoff point; 0 means "take rel_alt from the DJI SRT"
    height_m: float = 0.0
    #: takeoff-to-water-surface offset, added to rel_alt. Positive when the takeoff
    #: point is above the water. Ground sampling distance is linear in the result.
    water_offset_m: float = 0.0
    hfov_deg: float = 0.0
    # --- camera: gimbal attitude and optics; 0 means "take it from the SRT"
    yaw_deg: float = 0.0
    #: -90 is nadir. A locked gimbal is not necessarily a nadir gimbal.
    pitch_deg: float = 0.0
    roll_deg: float = 0.0
    #: 35 mm-equivalent focal length in mm, as DJI reports it
    focal_len_35mm: float = 0.0
    #: world coordinates of the point below the aircraft; empty means "from the SRT"
    origin_xy: list[float] = field(default_factory=list)

    # --- gcp / orthophoto: image points paired with world coordinates
    gcp_image: list[list[float]] = field(default_factory=list)
    gcp_world: list[list[float]] = field(default_factory=list)
    orthophoto: str = ""

    #: fitted afterwards by the Tier 0.5 scale fit; 1.0 means untouched
    scale_correction: float = 1.0


@dataclass
class PIVCfg:
    enabled: bool = True
    #: interrogation window size per pass, coarse to fine
    windows: list[int] = field(default_factory=lambda: [64, 64])
    overlap: float = 0.5
    #: reject vectors whose correlation peak falls below this
    corr_threshold: float = 0.7
    #: detection-quality measure. "peak2rms" compares the peak against the RMS of
    #: the rest of the plane and stays meaningful for broad peaks; "peak2peak" is
    #: the classic ratio and collapses toward 1.0 when the peak is wide.
    snr_method: Literal["peak2rms", "peak2peak"] = "peak2rms"
    #: half-width of the region excluded around the primary peak, in pixels; must
    #: clear the peak's own shoulder
    snr_exclusion_px: int = 5
    #: reject vectors whose detection quality falls below this
    snr_threshold: float = 3.0
    subpixel: Literal["gaussian", "parabolic"] = "gaussian"
    #: reject windows flatter than this standard deviation (image scaled 0..1);
    #: a textureless window correlates perfectly with itself and would otherwise win
    min_texture: float = 0.02
    #: normalised median test over a 3x3 neighbourhood
    median_threshold: float = 2.0
    #: fill rejected vectors by local interpolation between passes
    fill: bool = True


@dataclass
class PTVCfg:
    enabled: bool = False
    #: "dog" is a band-pass suited to natural texture; "bright" thresholds on
    #: absolute brightness and area, which is what injected tracers need
    detector: Literal["dog", "bright"] = "dog"
    #: work on the raw radiometry rather than the PIV-preprocessed image. PIV wants
    #: the background removed and the contrast stretched; a bright-tracer detector
    #: wants neither, because the stretch pushes noise to saturation.
    use_raw: bool = True
    #: "bright" detector: threshold on the image, 0 to 1
    bright_threshold: float = 0.86
    min_area_px: int = 8
    max_area_px: int = 600
    #: difference-of-Gaussian kernel scale; roughly the particle radius in pixels
    conv_size: float = 2.0
    #: 0 to 1; automatic threshold when ``auto_threshold`` is set
    peak_threshold: float = 0.02
    auto_threshold: bool = True
    #: minimum separation between detected objects in pixels
    peak_neighbourhood: int = 1
    #: neighbours averaged by the KdTree motion model
    motion_neighbours: int = 10
    #: reject a match beyond this many standard deviations of the model error
    filter_std: float = 1.5
    #: hard cap on displacement between frames, in pixels; 0 means automatic
    max_displacement: float = 0.0
    #: seed the motion model from the PIV field (combined mode)
    piv_guided: bool = True


@dataclass
class AnalysisCfg:
    """Where velocimetry is allowed to look.

    A rectangular ROI cannot exclude a gravel bar in the middle of a braided
    reach, and correlation over dry ground reports a confident zero. Automatic
    water detection is unreliable on a sunlit gravel-bed river, where water and
    bar overlap in both brightness and colour, so the mask is drawn by hand in
    the GUI and stored here.
    """

    #: polygons of the regions to analyse, image pixels, taken as a union.
    #: Empty means the whole ROI. A braided reach needs several.
    include: list[list[list[float]]] = field(default_factory=list)
    #: polygons to exclude, e.g. bars or structures inside the include polygon
    exclude: list[list[list[float]]] = field(default_factory=list)


@dataclass
class GridCfg:
    """Output grid for the time-averaged field."""

    #: cell size in pixels of the (stabilised) image
    cell_px: int = 32
    #: drop cells with fewer than this many samples
    min_samples: int = 3
    #: fill grid cells within this many pixels of a measured cell, by inverse
    #: distance weighting. A sparse tracer field is a set of vectors along the
    #: paths the tracers took; without this a streamline cannot step from one cell
    #: to the next and a real measurement renders as a handful of stubs. Filled
    #: cells are tagged as interpolated and never counted as measurements.
    interpolate_radius_px: float = 0.0
    interpolate_min_neighbours: int = 2
    #: exclude the PIV field from the output when more than this fraction of its
    #: cells report no motion. PIV is still computed and still guides PTV where it
    #: is healthy, but a bed-locked field must not be fused in as if it were a
    #: measurement: it would dilute good tracer vectors with confident zeros.
    piv_health_gate: float = 0.5


@dataclass
class GroundTruthCfg:
    path: str = ""
    layer: str = ""
    #: attribute names in the GeoPackage
    col_u: str = "u"
    col_v: str = "v"
    col_speed: str = "speed"
    col_depth: str = "h"
    col_z_over_h: str = "z_over_h"
    col_sigma: str = "sigma"
    col_vertical: str = "vertical_id"
    #: alternative to col_z_over_h: a measurement depth and a total depth
    col_meas_depth: str = "Meas_Depth_m"
    col_total_depth: str = "Total_Depth_m"
    #: how col_meas_depth is referenced. Standard hydrometric gauging measures
    #: downward from the water surface, so 0.2 / 0.6 / 0.8 are the classic
    #: two-point and six-tenths positions. Height above the bed is then
    #: 1 - meas/total.
    depth_convention: Literal["below_surface", "above_bed"] = "below_surface"
    #: relative velocity uncertainty when the file carries no error column.
    #: 0.05 is a reasonable working figure for an ADV in a natural stream, where
    #: turbulence dominates the instrument specification.
    sigma_rel: float = 0.05
    sigma_floor_ms: float = 0.0025
    #: group points into verticals by position when there is no vertical column
    vertical_from_geometry: bool = True
    vertical_tolerance_m: float = 0.10
    #: search radius in metres when sampling the computed field at a point
    search_radius_m: float = 1.0
    #: log-law conversion of the measurement to surface velocity
    conversion: Literal["two_point", "log_single", "hybrid", "none"] = "hybrid"
    #: fallback roughness length in metres when no two-point vertical is available
    z0_fallback_m: float = 0.005
    #: drop points whose relative submergence h/z0 falls below this, where the
    #: logarithmic profile stops being a reasonable model
    min_submergence: float = 5.0


@dataclass
class CalibrateCfg:
    enabled: bool = False
    #: Tier 0.5: fit one global scale factor alone and first, then freeze it
    fit_scale: bool = True
    #: Tier 1 search
    n_trials: int = 40
    holdout_fraction: float = 0.3
    #: refuse the Tier 1 search unless at least this many held-out points have a
    #: computed vector. Below it the validation statistic cannot detect the
    #: overfitting the search would produce.
    min_validation_points: int = 5
    seed: int = 0
    w_magnitude: float = 1.0
    w_direction: float = 0.5
    w_yield: float = 0.25
    params: list[str] = field(
        default_factory=lambda: ["piv.windows", "piv.overlap", "piv.corr_threshold", "video.step"]
    )


@dataclass
class ExportCfg:
    outdir: str = "out"
    geotiff: bool = True
    gpkg: bool = True
    ugrid: bool = True
    streamlines: bool = True
    cmap: str = "cividis"
    backdrop_alpha: float = 0.5
    dpi: int = 200
    #: known discharge for the mass-conservation check, m3/s; 0 disables
    discharge_m3s: float = 0.0
    #: mean flow depth for the discharge check, m
    check_depth_m: float = 0.0
    #: surface-to-depth-average coefficient used only by the discharge check
    alpha: float = 0.85


@dataclass
class Project:
    name: str = "pxv-project"
    video: VideoCfg = field(default_factory=VideoCfg)
    preprocess: PreprocessCfg = field(default_factory=PreprocessCfg)
    stabilize: StabilizeCfg = field(default_factory=StabilizeCfg)
    geometry: GeometryCfg = field(default_factory=GeometryCfg)
    piv: PIVCfg = field(default_factory=PIVCfg)
    ptv: PTVCfg = field(default_factory=PTVCfg)
    analysis: AnalysisCfg = field(default_factory=AnalysisCfg)
    grid: GridCfg = field(default_factory=GridCfg)
    groundtruth: GroundTruthCfg = field(default_factory=GroundTruthCfg)
    calibrate: CalibrateCfg = field(default_factory=CalibrateCfg)
    export: ExportCfg = field(default_factory=ExportCfg)

    # ------------------------------------------------------------------ helpers

    def to_dict(self) -> dict[str, Any]:
        return dataclasses.asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Project":
        sections = {f.name: f.type for f in dataclasses.fields(cls)}
        kwargs: dict[str, Any] = {}
        for key, value in d.items():
            if key not in sections:
                raise ValueError(f"unknown project section: {key!r}")
            if key == "name":
                kwargs[key] = value
                continue
            sub = _SECTION_TYPES[key]
            known = {f.name for f in dataclasses.fields(sub)}
            unknown = set(value) - known
            if unknown:
                raise ValueError(f"unknown keys in section {key!r}: {sorted(unknown)}")
            kwargs[key] = sub(**value)
        return cls(**kwargs)

    def get(self, dotted: str) -> Any:
        obj: Any = self
        for part in dotted.split("."):
            obj = getattr(obj, part)
        return obj

    def set(self, dotted: str, value: Any) -> None:
        parts = dotted.split(".")
        obj: Any = self
        for part in parts[:-1]:
            obj = getattr(obj, part)
        setattr(obj, parts[-1], value)


_SECTION_TYPES = {
    "video": VideoCfg,
    "preprocess": PreprocessCfg,
    "stabilize": StabilizeCfg,
    "geometry": GeometryCfg,
    "piv": PIVCfg,
    "ptv": PTVCfg,
    "analysis": AnalysisCfg,
    "grid": GridCfg,
    "groundtruth": GroundTruthCfg,
    "calibrate": CalibrateCfg,
    "export": ExportCfg,
}


def load_project(path: str | Path) -> Project:
    with open(path, "r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}
    return Project.from_dict(data)


def save_project(project: Project, path: str | Path) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        yaml.safe_dump(project.to_dict(), fh, sort_keys=False, default_flow_style=False)

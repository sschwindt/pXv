# pXv

Mixed PIV and PTV surface velocimetry for drone and lab top-view video, with a
PySide6 GUI, ground-truth-driven calibration, and QGIS-ready exports.

pXv turns a nadir-looking video of a water surface into a georeferenced surface
velocity field. It carries two velocimetry engines behind one shared geometry,
stabilisation, validation and calibration frame, so the same clip can be
processed either way and the two results compared directly:

- **PIV**, multi-pass FFT cross-correlation, for dense particle swarms and
  natural surface texture
- **PTV**, a reimplementation of TracTrac (Heyman 2019), for discrete drifting
  objects
- **Combined**, PIV-guided PTV: the PIV field seeds the PTV motion model, so
  search windows shrink and mismatches collapse

See [`docs/DEVELOPMENT_PLAN.md`](docs/DEVELOPMENT_PLAN.md) for the design
rationale, the reference workflow it implements, and the open questions.

## Install

```bash
mamba env create -f environment.yml
mamba activate pxv-env
pip install -e .
```

Or into an existing environment:

```bash
pip install -e ".[geo,gui,opt]"
```

`opencv-python-headless` is deliberate: the normal OpenCV wheel bundles its own
Qt and will clash with PySide6.

## Quick start

```bash
# what is in the video, and does it carry DJI telemetry?
pxv info flight.mp4

# how should the frame step and interrogation window be set?
pxv advise --speed 1.0 --fps 30 --height 40 --hfov 84 --width 3840

# write a starter project, edit it, run it
pxv init flight.mp4 -o flight.yaml
pxv run flight.yaml

# fit the scale, then search the Tier 1 parameters against ground truth
pxv calibrate flight.yaml --write

# or do all of it in the GUI
pxv gui flight.yaml
```

A worked example on the lab clip in this repository:

```bash
pxv run examples/mq-slots.yaml
```

## The pipeline

Order follows the reference workflow (Broß, Dorthe & Schalko 2026):

Each preprocessing stage is separately switchable and separately reported, so its
contribution to contrast, fine texture and saturation is visible rather than
entangled with the others.

1. **Stabilise** against wind-gust drift, using a homography estimated from
   *static* pixels only, with the moving water masked out (Ioli et al. 2020).
   pXv re-anchors to a global reference frame periodically rather than chaining
   frame to frame, and reports the residual so drift is measurable.
2. **Background**: a static background built as the mean of every Nth frame of
   the whole clip, subtracted from each frame.
3. **Normalise**, then **enhance** with CLAHE.
4. **Velocimetry**: PIV, PTV, or both. A seeding-density diagnostic recommends
   which, per clip.
5. **Correlation filter** plus SNR, normalised median test and a texture guard.
6. **Geometry**: control points, a known distance, or flight height.
7. **Ground truth**: convert gauged velocities to surface velocity, compare,
   calibrate.
8. **Export**.

## Geometry

| Mode | When | Accuracy |
|---|---|---|
| `gcp` / `orthophoto` | control points with known world coordinates | best; RANSAC over all points, per-point residuals |
| `camera` | a DJI `.SRT` with height, gimbal attitude and focal length | full projective rectification with **no ground control at all** |
| `known_distance` | a known real length visible in frame | exact for a nadir view of a flat surface; ideal in a flume |
| `height` | flight height and field of view only | uniform scale; valid only within a degree or two of nadir |
| `none` | no scaling | pixels per frame |

**A locked gimbal is not a nadir gimbal.** DJI reports `gb_pitch`, and a hover
held steady at, say, -78.4 degrees sits 11.6 degrees off nadir. That makes the
ground sampling distance vary systematically across the frame, by around 17 % in
that example, so a single scale factor is wrong no matter how carefully it is
chosen. `camera` mode builds the image-to-ground homography from the telemetry
and reports the ground sampling distance at the frame centre, top and bottom so
the spread is visible. pXv warns whenever the gimbal is more than two degrees off
nadir.

Ground sampling distance scales linearly with height, so a 2 m error in the
takeoff-to-water-surface offset at 40 m altitude is a flat 5 % velocity bias
across the whole field. pXv asks for that offset explicitly rather than assuming
`rel_alt` is height above water. Where control points are near-collinear, which
is what happens when they sit along cross-sections, pXv reports the condition
number and falls back to a similarity transform rather than fitting a degenerate
homography.

### A second camera stream

`video.secondary_path` accepts a second stream, such as a multispectral or
thermal payload, showing the same scene through different optics.
`pxv.geometry.coregister` brings it into the primary image frame. The rig is
rigid and the gimbal locked, so one homography serves the whole clip;
correspondences are pooled across several frames because the two renderings can
look nothing alike.

### DJI telemetry

With Video Captions enabled, a Mavic 3 writes a `.SRT` sidecar next to the `.MP4`
carrying position, `rel_alt`, `abs_alt`, gimbal yaw/pitch/roll, focal length and
zoom, once per frame. Note that a single bracket can hold several fields, as in
`[rel_alt: 35.217 abs_alt: 900.696]` and
`[gb_yaw: -2.0 gb_pitch: -78.4 gb_roll: 0.0]`; a parser that treats one bracket
as one field silently drops `abs_alt` and both gimbal angles. `abs_alt` is
barometric and routinely tens of metres out, so pXv never uses it for scale.

## Ground truth and the profile conversion

Point gauging measures velocity at a fixed fraction of the depth, which is
neither depth-averaged nor surface velocity, so a single surface coefficient
alpha is the wrong model.

**Watch the convention.** Standard hydrometric gauging reports depth measured
*downward from the water surface*, which is why 0.2, 0.6 and 0.8 recur: they are
the classic two-point and six-tenths positions. Height above the bed is then
`1 - meas/total`. Set `depth_convention` to say which your file uses; getting it
backwards inverts the velocity profile. If a file carries `Meas_Depth_m` and
`Total_Depth_m`, pXv derives the height for you.

Where a vertical carries **two** heights, they determine a logarithmic profile
completely, with no assumed roughness:

```
u_surface = u_lower + K * (u_upper - u_lower),   K = ln(1/z1) / ln(z2/z1)
```

with `z1 < z2` as fractions of depth above the bed. For the standard 0.2 / 0.8
below-surface pair, `K = 1.161` and the measurement uncertainty is amplified by
only **1.17**, which makes the extrapolation genuinely usable. Widely separated
points condition the fit well; closely spaced ones do not.

The default `hybrid` mode uses the paired verticals to estimate the site
roughness `z0`, takes a robust central value, and applies that to every point,
including the single-height ones. Conversion always runs on the ground truth,
never on the computed field, so the exported field stays a true surface field and
each point carries its own propagated uncertainty into the objective's weighting.

Required GeoPackage attributes: velocity (components or speed), a measurement
height (`z_over_h`, or `Meas_Depth_m` plus `Total_Depth_m`), and depth. Verticals
are grouped by position when there is no `vertical_id` column. Without a measured
standard error, pXv applies `sigma_rel`, which defaults to 5 % of reading, a
reasonable working figure for an ADV in a natural stream where turbulence
dominates the instrument specification.

## Calibration tiers

Only Tier 1 reaches the optimizer. If the scale is wrong, an optimizer will
happily compensate by shrinking the interrogation window until the numbers match,
producing a meaningless field that validates perfectly.

| Tier | Contents | Treatment |
|---|---|---|
| 0 | geometry, frame interval, CRS, stabilisation quality | fixed first, never optimized; **gates the whole search** |
| 0.5 | one global scale factor | fitted **alone and first**, then frozen; only needed when no dedicated control points exist |
| 1 | window, overlap, correlation threshold, frame step, preprocessing strength | the actual search space |
| 2 | profile conversion of the ground truth | handled separately, see above |

Calibration and validation error are always reported separately, over a held-out
subset of the ground-truth points. With a handful of gauged verticals it is
trivially easy to overfit, and the report makes that visible.

**The Tier 0 gate.** Before any search runs, pXv checks whether the field is
measuring the right thing at all, and refuses to continue if it is not:

- the **static-motion check**, the share of cells whose displacement is
  indistinguishable from zero. The seeding-density diagnostic counts detectable
  objects but cannot tell a drifting tracer from a stationary stone.
- the **bias diagnosis**, which separates a scale error from a velocimetry
  failure. A scale error multiplies every point by the same factor, so the
  modelled-to-measured ratios cluster tightly and one scale factor fixes them. A
  velocimetry failure scatters the ratios over orders of magnitude, biased in one
  direction only.

Optimizing on top of a velocimetry failure is the failure mode the tiers exist to
prevent: the search will find settings that fit a handful of points by chance and
report an excellent score for a field that measures the wrong thing.

### Two engines, two health checks

PIV and PTV can disagree by construction. On a seeded clip, PIV runs on the
natural texture while PTV runs on the injected tracers, and if the natural
texture is a riverbed seen through clear water the two are measuring different
things. So PIV's contribution is gated on its own static-motion check:

- it is always computed, and it still guides the PTV motion model **when it is
  healthy**;
- it is withheld from that guidance when it is not, because a field predicting
  zero displacement biases tracer tracking toward standing still;
- it is excluded from the fused output above `grid.piv_health_gate`, because
  fusing confident zeros beside sparse but correct tracer vectors drags the
  median toward nothing.

Every exclusion is logged, never silent.

### Sparse fields and streamlines

A tracer pulse leaves vectors along the paths the tracers took, so binning gives
isolated cells, and a streamline that cannot step from one cell to the next stops
immediately: a real measurement renders as a handful of stubs.
`grid.interpolate_radius_px` fills cells within a bounded distance of a measured
one. Filled cells are tagged as interpolated, carry a zero sample count, are
excluded from every statistic and from the health checks, and are exported with
`source = "interpolated"`. The figure caption separates measured from rendered
coverage.

### Seeded tracers

Injected tracers are far brighter than anything else on the water and have a
known physical size, so `ptv.detector: bright` thresholds on brightness and area
rather than using a band-pass, which responds to sun glint and to the bed just as
strongly. `ptv.use_raw` keeps that detector on the raw radiometry: the contrast
stretch that PIV needs pushes noise to saturation and yields thousands of false
detections per frame.

Find the seeded window before analysing: tracer density varies enormously over a
clip, and a window chosen before injection measures only the riverbed.

Two things that look like they should help and do not, both measured rather than
assumed:

- **Running the preprocessing chain before tracer detection.** It exists to
  amplify faint texture so correlation can lock onto it. Tracers are the opposite
  case, the brightest thing in the frame, so amplifying faint texture destroys the
  contrast that identifies them. On the Isar clip the chain turns a 33x
  tracer-to-background contrast into 1.3x, and adding CLAHE or an unsharp mask
  inverts it entirely.
- **Ensemble correlation.** Averaging correlation planes over many pairs is the
  standard remedy for sparse seeding, but it reinforces whatever is consistent
  across pairs. Where a stationary bed competes with the tracers, that is the bed.

### Clear water over a visible bed

This is the hard case, and it is common on gravel-bed rivers. When the bed is
visible through the surface it is the strongest texture in the frame, so
correlation locks onto it and reports near-zero velocity with high confidence. No
parameter choice fixes this, because the bed really is the dominant signal.

Suppressing the zero-lag correlation peak looks like a fix and is not: it pins
the reported displacement at whatever lies just outside the exclusion radius, so
the answer stops changing with the frame interval. That is a tell, and pXv does
not offer it.

What does work is seeding the flow, or restricting the analysis region to water
whose surface is actually textured. The static-motion check and the bias
diagnosis are there to tell you which situation you are in.

## Analysis region

A rectangular ROI cannot exclude a gravel bar in the middle of a braided reach,
and correlation over dry ground reports a confident zero. `analysis.include`
takes a union of polygons and `analysis.exclude` cuts holes in them; both are
drawn in the GUI viewport.

Automatic water detection is offered only as a starting point. On a sunlit
gravel-bed river, water and dry bar overlap in both brightness and colour, and
temporal variance does not separate them either because residual camera motion
makes every edge flicker. Draw the polygon.

## Exports

- **UGRID NetCDF** mesh, read natively by QGIS through MDAL, with arrow,
  streamline and particle-trace rendering
- **GeoTIFF**, three bands: `u`, `v`, `magnitude`
- **GeoPackage** points with `u`, `v`, `magnitude`, `direction`, `n_samples`,
  `source`
- **Streamline JPG** coloured by speed over a 50 % opacity frame of the footage,
  with a colourbar, scale bar and north arrow, plus a `.jgw` world file and
  `.prj` so it drops into QGIS georeferenced. Cells with no data are masked
  rather than filled, so streamlines stop where the measurement stopped, and the
  figure carries its own coverage and static-motion verdict
- **Quicklook PNG**: speed map, vector overlay, sample count, stabilisation
  residual

Colour maps are restricted to perceptually uniform, colour-vision-deficiency-safe
sequential maps; `cividis` is the default and rainbow maps are refused.

## Reproducibility

Every run is fully described by one YAML project file. The GUI only edits that
file, and `pxv run project.yaml` reproduces the run headlessly, so nothing the
GUI can do is unreachable from the command line.

## Tests

```bash
python -m pytest tests -q
```

The suite checks the numerics against analytic answers: PIV and PTV recover known
synthetic displacements, stabilisation recovers known shifts, the correlation
plane matches a manual Pearson coefficient at zero lag, and the profile
conversion is exact against an analytic logarithmic profile.

## References

- Broß, F., Dorthe, C., Schalko, I., 2026. Hydrodynamic and ecological effects of
  engineered logjams: Insights from field observations. *Earth Surface Processes
  and Landforms* 51, e70322. https://doi.org/10.1002/esp.70322
- Ioli, F., Pinto, L., Passoni, D., Nova, V., Detert, M., 2020. Evaluation of
  airborne image velocimetry approaches using low-cost UAVs in riverine
  environments. *ISPRS Archives* XLIII-B2-2020, 597-604.
  https://doi.org/10.5194/isprs-archives-XLIII-B2-2020-597-2020
- Heyman, J., 2019. TracTrac: A fast multi-object tracking algorithm for motion
  estimation. *Computers & Geosciences* 128, 11-18.
  https://doi.org/10.1016/j.cageo.2019.03.007
- Westerweel, J., Scarano, F., 2005. Universal outlier detection for PIV data.
  *Experiments in Fluids* 39, 1096-1100.

## License

MIT. The vendored TracTrac reference in `tractracSources/` is also MIT; see
`tractracSources/VENDORED.md`.

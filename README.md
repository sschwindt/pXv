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
| `known_distance` | a known real length visible in frame | exact for a nadir view of a flat surface; ideal in a flume |
| `height` | flight height and field of view only | ground sampling distance is **linear in height** |
| `none` | no scaling | pixels per frame |

Ground sampling distance scales linearly with height, so a 2 m error in the
takeoff-to-water-surface offset at 40 m altitude is a flat 5 % velocity bias
across the whole field. pXv asks for that offset explicitly rather than assuming
`rel_alt` is height above water. Where control points are near-collinear, which
is what happens when they sit along cross-sections, pXv reports the condition
number and falls back to a similarity transform rather than fitting a degenerate
homography.

## Ground truth and the profile conversion

Point gauging measures velocity at a fixed fraction of the depth. When those
points sit at **0.6 h and 0.8 h above the bed** they are neither depth-averaged
nor surface velocity, so a single surface coefficient alpha is the wrong model.

Note this is not the conventional "0.6 depth" rule, which means 0.6 of the depth
*below the surface* (0.4 h above the bed) and exists precisely because it
approximates the depth average. Measured upward from the bed, the points sit high
in the profile and the correction is small: about 6 to 15 % at 0.6 h and 3 to 7 %
at 0.8 h, against 15 to 30 % for a depth-averaged value.

Where a vertical carries **both** heights, two points determine a logarithmic
profile completely, with no assumed roughness:

```
u_surface = u(0.6h) + 1.7757 * (u(0.8h) - u(0.6h))
```

That is exact but noisy: it amplifies the measurement standard deviation by
**1.94**. So the default `hybrid` mode uses the paired verticals to estimate the
site roughness `z0`, takes a robust central value, and applies that to every
point. Conversion always runs on the ground truth, never on the computed field,
so the exported field stays a true surface field and each point carries its own
propagated uncertainty into the objective's weighting.

Required GeoPackage attributes: velocity (components or speed), `z_over_h`, `h`,
and ideally `vertical_id` and `sigma`. Without a measured standard error, pXv
falls back to the FlowTracker2 specification (1 % of reading, floor 0.25 cm/s).

## Calibration tiers

Only Tier 1 reaches the optimizer. If the scale is wrong, an optimizer will
happily compensate by shrinking the interrogation window until the numbers match,
producing a meaningless field that validates perfectly.

| Tier | Contents | Treatment |
|---|---|---|
| 0 | geometry, frame interval, CRS, stabilisation quality | fixed first, never optimized |
| 0.5 | one global scale factor | fitted **alone and first**, then frozen; only needed when no dedicated control points exist |
| 1 | window, overlap, correlation threshold, frame step, preprocessing strength | the actual search space |
| 2 | profile conversion of the ground truth | handled separately, see above |

Calibration and validation error are always reported separately, over a held-out
subset of the ground-truth points. With a handful of gauged verticals it is
trivially easy to overfit, and the report makes that visible.

## Exports

- **UGRID NetCDF** mesh, read natively by QGIS through MDAL, with arrow,
  streamline and particle-trace rendering
- **GeoTIFF**, three bands: `u`, `v`, `magnitude`
- **GeoPackage** points with `u`, `v`, `magnitude`, `direction`, `n_samples`,
  `source`
- **Streamline JPG** coloured by speed over a 50 % opacity frame of the footage,
  with a colourbar, scale bar and north arrow, plus a `.jgw` world file and
  `.prj` so it drops into QGIS georeferenced
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

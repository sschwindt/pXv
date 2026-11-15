# Isar River showcase

This example uses drone footage and a field survey from the Isar River
on 4 August 2026. Floating tracers added to the river make it possible
to follow the surface flow even where the gravel bed is visible.

## Run the bundled example

1. Open pXv using the [Installation guide](installation.md).
2. Select **Load Isar example**. The preset loads the video, camera
   information, survey, and analysis settings.
3. When the river appears in **Surface view**, select **Run analysis** in
   the **Analysis setup** panel. Keep the preset **PTV** method so the app
   follows the floating tracers.

## Read and export the result

```{figure} _static/isar-results.webp
:alt: Isar River video frame with yellow and green velocity arrows, bold pink survey points, and a tracer median of 1.55 metres per second.
:width: 100%

The bundled Isar clip after analysis in pXv. The arrows follow the areas
with visible tracer motion, and the pink diamonds mark survey positions.
The **Tracks** overlay is switched off here so the arrows are easier to see.
```

- In **Surface view**, the arrows point in the flow direction. Use the
  colour scale below the image to read their speeds in m/s.
- Under **Surface speed**, **Tracer median** is the midpoint of the sorted
  cell speeds. The bundled clip gave **1.55 m/s**
  across 22 cells.
- **Survey median in frame** was **1.24 m/s** at nine usable positions.
  **Local video vs. survey** uses positions with a nearby video estimate.
  In the short clip it shows a dash because no measured video cell
  was within 1.5 m of a survey position.

Select **Export result graph** to save the image with a legend, north
arrow, and scale bar. Select **Export XLSX** or **Vectors CSV** to open
the measured speeds in a spreadsheet.

## How the survey is used

The FlowTracker2 field survey measured velocity below the surface. pXv
uses the depth of each reading and the total water depth to estimate a
reference surface speed with a logarithmic velocity profile. That
estimate depends on how well the assumed profile fits the local flow.
See [Compare with a survey](browser.md#compare-with-a-survey) to change
the match radius and review comparisons.

## Data and full recording

A fresh download includes a 2.63 MB video and matching DJI caption file
under `examples/isar-ci/`. The clip contains original frames 1290–1319
at 1920 × 1080 and about 30 frames per second. The captions supply camera
information that pXv uses to place the image on a map. The field survey
is in `examples/isar-20260804/IsarAug2026.gpkg`.

If the original video is present alongside its DJI captions in
`examples/isar-20260804/`, pXv selects a longer interval. To use the
bundled clip in that situation, set `PXV_ISAR_FIXTURE` to `1` before
starting pXv.

The 400-frame browser PTV run covered source frames 1250–1649. The median
was 1.48 m/s across 43 cells along tracer paths. The survey median was
1.24 m/s across nine usable positions in the video footprint. One
survey position matched a tracer cell within 1.5 m, with a speed
difference of 0.35 m/s. The comparison supports a local check.

The video scale uses the DJI camera tilt and assumes takeoff was level
with the water, giving a camera height of about 35.21 m.

# Isar River example

The repository includes a short seeded-tracer clip and synchronized DJI
captions under `examples/isar-ci/`. The example also uses the FlowTracker2
survey at `examples/isar-20260804/IsarAug2026.gpkg`.

Start the [browser app](browser.md), select **Load Isar example**, then
select **Run analysis**. On a fresh clone the preset uses the short clip.
If you have placed the original footage beside its DJI captions in
`examples/isar-20260804/`, the preset uses the longer seeded interval.
Set the `PXV_ISAR_FIXTURE` environment variable to `1` before starting
the server to select the short clip explicitly.

The 2.63 MB clip contains original frames 1290–1319, renumbered 0–29.
It keeps the 1920 × 1080 image size and 30000/1001 frames per second
for a duration of 1.001 seconds. The browser uses the matching
captions to project the reference frame and the tracers to estimate
surface speed. The field survey contains subsurface measurements.
pXv uses the measurement depths and a logarithmic profile to estimate
the reference speed at the surface.

The short clip tests the workflow with real footage and limited tracer
coverage. Use the [survey comparison controls](browser.md#compare-with-a-survey)
to inspect which locations have nearby measured vectors.

## Full footage comparison

The 400-frame browser PTV run covered source frames 1250–1649. The median
was 1.48 m/s across 43 cells along tracer paths. The survey median was
1.24 m/s across nine usable positions in the video footprint. One
survey position matched a tracer cell within 1.5 m, with a speed
difference of 0.35 m/s. The comparison supports a local check.

The projective video scale uses the DJI gimbal tilt and assumes takeoff
was level with the water, giving a camera height of about 35.21 m. The
visible gravel bed can influence PIV correlation, so this example uses
seeded tracer tracking.

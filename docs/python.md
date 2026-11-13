# Python workflow

After [installing the package](installation.md), inspect the video and
create a YAML project.

```sh
pxv info flight.mp4
pxv init flight.mp4 -o flight.yaml
```

For an initial frame-step and window estimate, use `pxv advise` with
the expected speed, frame rate, camera height, field of view, and image width.

Set the frame range, geometry, and analysis method in `flight.yaml`. If
using DJI captions, keep the SRT beside the video. Use the configured geometry
to convert measured displacements to ground distances.

```sh
pxv run flight.yaml
pxv gui flight.yaml
```

With a suitable ground-truth survey, `pxv calibrate flight.yaml --write`
can fit the scale and search processing parameters. Confirm the survey depth
convention and review the holdout errors before using the calibrated project.
Supported exports include GeoPackage, GeoTIFF, and UGRID NetCDF.

See the [Python API](python-api.rst) for the camera model and
velocity-profile conversion.

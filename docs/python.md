# Python workflow

Install the package from the repository checkout with Python 3.10 or later.

```sh
python -m pip install -e ".[geo,gui,opt]"
```

The extras provide GIS exports, the Qt interface, and parameter search. The
base install runs the core processing pipeline.

Inspect the video and create a YAML project.

```sh
pxv info flight.mp4
pxv init flight.mp4 -o flight.yaml
```

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

See the [development notes](DEVELOPMENT_PLAN.md) for the camera model,
velocity-profile conversion, and method evaluation.

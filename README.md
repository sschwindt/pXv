# pXv

[View the documentation on Read the Docs](https://pxv.readthedocs.io/en/latest/)

## Purpose

pXv measures water-surface velocity from downward-looking drone and laboratory
video using particle tracking velocimetry (PTV) and particle image velocimetry
(PIV). The browser app handles georeferencing, survey comparison, and figure
and table export. The Python package also provides a CLI, a desktop GUI,
and GIS exports.

## Installation

Clone the repository and start the browser app with Node.js 24 or later.

```sh
git clone https://github.com/sschwindt/pXv.git
cd pXv
node web/server.mjs
```

Open [the local app](http://127.0.0.1:8040/) in Chrome or Edge.
See [Installation](https://pxv.readthedocs.io/en/latest/installation.html)
for Python setup.

## Usage

Select **Load Isar example**, then **Run analysis**. A fresh checkout includes
a 30-frame, 2.63 MB seeded clip, matching DJI captions, and the survey. The
app uses the full clip when it is present. Set `PXV_ISAR_FIXTURE=1` to
select the short clip explicitly.

Use **Import** to analyze your own footage. **Save project** and **Load
project** preserve settings in a `.pxv` file. **Export result graph**
downloads a WebP or JPEG with a speed legend, scale bar, and a north arrow
when the footage is georeferenced. CSV and XLSX contain the numeric results.

The [Usage guide](https://pxv.readthedocs.io/en/latest/usage.html) covers the
browser app, survey comparison, Isar example, and Python workflow.

## Code Documentation

The [Code Documentation](https://pxv.readthedocs.io/en/latest/code-documentation.html)
includes a UML sequence diagram and API references generated from JavaScript
JSDoc and Python docstrings. Run the JavaScript unit tests with
`node --test "web/*.test.mjs"`. GitHub Actions also processes the bundled Isar
example in Chrome and builds the documentation.

## License and Disclaimer

pXv and the vendored TracTrac reference are distributed under their respective
MIT licenses. The software is provided as is, without warranty.
See the [pXv license](LICENSE), [TracTrac notice](tractracSources/LICENSE),
and [License and Disclaimer](https://pxv.readthedocs.io/en/latest/license.html).

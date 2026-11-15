# pXv

[View the documentation on Read the Docs](https://pxv.readthedocs.io/en/latest/)

## Purpose

pXv estimates water-surface velocity from video filmed above a river or
laboratory flume. Particle tracking velocimetry (PTV) follows individual
tracers, and particle image velocimetry (PIV) follows patterns across the
image. The browser app maps the results, compares them with field
measurements where available, and exports figures and tables.

## Installation

Install [Node.js 24 or later](https://nodejs.org/en/download). On GitHub,
select **Code** → **Download ZIP** and extract it. Open a terminal in the
extracted folder that contains `web` and run the following command.

```sh
node web/server.mjs
```

Keep the terminal open and open [the local app](http://127.0.0.1:8040/) in
Chrome or Edge. See the [Installation guide](https://pxv.readthedocs.io/en/latest/installation.html)
for help opening a terminal on Windows, macOS, or Linux.

## Usage

Select **Load Isar example**, then **Run analysis**. The download includes
a short drone clip, its DJI captions, and the field survey. The
[Isar River showcase](https://pxv.readthedocs.io/en/latest/isar.html)
shows what to expect and explains the results.

Use **Import** to analyze your own footage. **Save project** and **Load
project** preserve settings in a `.pxv` file. **Export result graph**
downloads a WebP or JPEG with a speed legend, scale bar, and a north arrow
when the footage is georeferenced. CSV and XLSX contain the numeric results.

The [Usage guide](https://pxv.readthedocs.io/en/latest/usage.html) covers
import, survey comparison, filters, and exports.

## Code Documentation

The [Code Documentation](https://pxv.readthedocs.io/en/latest/code-documentation.html)
includes a UML sequence diagram and a JavaScript API reference generated from
JSDoc comments. Run the JavaScript unit tests with
`node --test "web/*.test.mjs"`. GitHub Actions also processes the bundled Isar
example in Chrome and builds the documentation.

## License and Disclaimer

pXv and the vendored TracTrac reference are distributed under their respective
MIT licenses. The software is provided as is, without warranty.
See the [pXv license](LICENSE), [TracTrac notice](tractracSources/LICENSE),
and [License and Disclaimer](https://pxv.readthedocs.io/en/latest/license.html).

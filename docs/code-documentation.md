# Code Documentation

## Browser analysis sequence

The browser coordinates analysis in a Web Worker. It projects and filters
the returned vectors before comparing them with survey observations and
exporting the results.

```{mermaid}
sequenceDiagram
    participant UI as Browser app
    participant Worker as Analysis worker
    participant Decode as MP4 decoder
    participant Engine as Velocimetry
    participant Geo as Geometry and survey
    participant Output as Graph and tables
    UI->>Worker: Analyze selected frame window
    Worker->>Decode: decodeWindow
    loop Each selected frame
        Decode-->>Worker: Decoded video frame
        Worker->>Engine: push grayscale frame and time
    end
    Worker->>Engine: finish
    Engine-->>Worker: Vectors, tracks, diagnostics
    Worker-->>UI: Result and reference image
    UI->>Geo: Project vectors and assess survey matches
    Geo-->>UI: Metric vectors and comparison
    UI->>Output: Export result
```

## API reference

The JavaScript reference reads JSDoc comments and the Python reference
reads Python docstrings during each documentation build.

```{toctree}
:maxdepth: 1

javascript-api
python-api
```

## Build the documentation

Run these commands from the repository checkout with Python 3.12 and
Node.js 24 available. A strict build fails on documentation warnings.

```sh
python -m pip install -r docs/requirements.txt
npm install --no-save --package-lock=false jsdoc@4.0.5
python -m sphinx -W -n -b html docs docs/_build/html
```

On Windows, sphinx-js invokes Node through a `node.cmd` launcher. If
your Node installation only provides `node.exe`, place a `node.cmd`
file containing `@node.exe %*` in a directory on your `PATH`.

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

The documentation build reads JSDoc comments from the browser app source
to generate the JavaScript API reference.

```{toctree}
:maxdepth: 1

javascript-api
```

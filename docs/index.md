# pXv

## Purpose

pXv estimates how fast water moves at the surface from video filmed above
a river or laboratory flume. It measures how far visible features move
between frames and uses the time between frames and the image scale to
report velocity in metres per second (m/s).

- **Particle tracking velocimetry (PTV)** follows individual tracers, such
  as floating particles added to the water.
- **Particle image velocimetry (PIV)** compares small patches in successive
  frames to estimate how the visible surface pattern moves.

The app displays the speed and direction of the flow as arrows on a video
frame. A field survey can help check the result where measurements overlap.
The video estimates surface velocity. Calculating river discharge also
requires information about the channel cross section and the velocity
through the water depth.

Start with [Installation](installation.md), then run the
[Isar River showcase](isar.md) using the supplied drone video and survey.

```{raw} html
<p><a class="btn btn-neutral" href="https://github.com/sschwindt/pXv">View on GitHub</a></p>
```

```{toctree}
:maxdepth: 2
:hidden:

installation
usage
isar
code-documentation
license
```

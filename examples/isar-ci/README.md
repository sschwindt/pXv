# Compact Isar example

This clip uses the original Isar RGB recording, `DJI_20260804150234_0002_D.MP4`.
It contains the 30 frames at zero-based indices 1290–1319, corresponding to
43.043–44.044 s at 30000/1001 fps. The interval contains seeded tracer motion.
The 1920 × 1080 H.264 clip is about 2.63 MB. Use the original project’s water
and bank polygons with frames 0–29 in the compact clip.

`isar-ci.srt` contains the original camera readings for those frames. Subtitle
cue times start at zero, cue and frame indices start at one, and the recorded
camera timestamps and telemetry values retain their original time and values.
The survey is `../isar-20260804/IsarAug2026.gpkg`, layer `ft_tke_points`.

The clip was encoded with FFmpeg and libx264:

```sh
ffmpeg -ss 43.043 -i ../isar-20260804/DJI_20260804150234_0002_D.MP4 \
  -map 0:v:0 -map_metadata -1 -frames:v 30 -c:v libx264 -crf 24 \
  -preset fast -threads 4 -pix_fmt yuv420p -bf 0 -g 30 \
  -video_track_timescale 30000 -movflags +faststart isar-ci.mp4
```

An independent PTV check on FFmpeg-decoded grayscale frames yielded 22 cells
with a median speed of 1.55 m/s. The same unmodified source frames yielded 22
cells at 1.57 m/s. The abbreviated run supports a smoke test of seeded motion.
Use the full seeded window for the field comparison described in the project
documentation.

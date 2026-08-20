"""DJI flight telemetry.

A Mavic 3 writes position and height to a sidecar ``.SRT`` next to the ``.MP4``
when Video Captions are enabled, not into the container. This reader is
deliberately tolerant: firmware revisions differ in which fields are present, so
it reports what it found and what it could not find rather than failing.

``rel_alt`` is height above the *takeoff point*, not above the water surface.
:func:`height_above_water` makes that correction explicit.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

_FIELD_RE = re.compile(r"\[([a-z_]+)\s*:\s*([^\]]+?)\s*\]", re.IGNORECASE)
_TIME_RE = re.compile(r"(\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2}(?:[.,]\d+)?)")
_NUM_RE = re.compile(r"-?\d+(?:\.\d+)?")


@dataclass
class TelemetrySample:
    index: int
    timestamp: datetime | None = None
    latitude: float | None = None
    longitude: float | None = None
    rel_alt: float | None = None
    abs_alt: float | None = None
    gb_yaw: float | None = None
    gb_pitch: float | None = None
    gb_roll: float | None = None
    focal_len: float | None = None
    extra: dict[str, str] = field(default_factory=dict)


@dataclass
class Telemetry:
    samples: list[TelemetrySample] = field(default_factory=list)
    source: str = "none"
    #: field name -> where the value came from
    provenance: dict[str, str] = field(default_factory=dict)

    def __len__(self) -> int:
        return len(self.samples)

    @property
    def present(self) -> list[str]:
        keys = []
        for name in ("latitude", "longitude", "rel_alt", "abs_alt", "gb_yaw", "gb_pitch", "focal_len"):
            if any(getattr(s, name) is not None for s in self.samples):
                keys.append(name)
        return keys

    @property
    def missing(self) -> list[str]:
        all_keys = ["latitude", "longitude", "rel_alt", "abs_alt", "gb_yaw", "gb_pitch", "focal_len"]
        return [k for k in all_keys if k not in self.present]

    def median(self, name: str) -> float | None:
        vals = sorted(v for s in self.samples if (v := getattr(s, name)) is not None)
        if not vals:
            return None
        return vals[len(vals) // 2]

    def describe(self) -> str:
        if not self.samples:
            return "no telemetry found"
        lines = [f"{len(self.samples)} samples from {self.source}"]
        for name in self.present:
            lines.append(f"  {name:10s} median {self.median(name)}")
        if self.missing:
            lines.append(f"  missing: {', '.join(self.missing)}")
        return "\n".join(lines)


def find_srt(video_path: str | Path) -> Path | None:
    p = Path(video_path)
    for candidate in (p.with_suffix(".SRT"), p.with_suffix(".srt")):
        if candidate.exists():
            return candidate
    return None


def _to_float(text: str) -> float | None:
    m = _NUM_RE.search(text)
    return float(m.group()) if m else None


def parse_srt(path: str | Path) -> Telemetry:
    """Parse a DJI subtitle sidecar into a telemetry series."""
    text = Path(path).read_text(errors="replace")
    blocks = re.split(r"\n\s*\n", text.strip())
    tel = Telemetry(source=str(path))
    for i, block in enumerate(blocks):
        if not block.strip():
            continue
        sample = TelemetrySample(index=i)
        if tm := _TIME_RE.search(block):
            raw = tm.group(1).replace(",", ".").replace("T", " ")
            for fmt in ("%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S"):
                try:
                    sample.timestamp = datetime.strptime(raw, fmt)
                    break
                except ValueError:
                    continue
        found = False
        for key, raw in _FIELD_RE.findall(block):
            key = key.lower()
            found = True
            if hasattr(sample, key) and key != "extra":
                setattr(sample, key, _to_float(raw))
            else:
                sample.extra[key] = raw
        if found or sample.timestamp:
            tel.samples.append(sample)
    for name in tel.present:
        tel.provenance[name] = "srt"
    return tel


def read_telemetry(video_path: str | Path) -> Telemetry:
    """Resolve telemetry from the best available source."""
    srt = find_srt(video_path)
    if srt is not None:
        return parse_srt(srt)
    return Telemetry(source="none")


def height_above_water(rel_alt: float, takeoff_to_water_m: float) -> float:
    """Correct ``rel_alt`` (height above takeoff) to height above the water surface.

    ``takeoff_to_water_m`` is positive when the takeoff point lies *above* the
    water surface, which is the usual case when launching from a bank.
    """
    return rel_alt + takeoff_to_water_m

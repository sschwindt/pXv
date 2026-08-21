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

# One bracket can hold several fields, e.g. "[rel_alt: 35.217 abs_alt: 900.696]"
# and "[gb_yaw: -2.0 gb_pitch: -78.4 gb_roll: 0.0]". Matching whole brackets and
# then splitting inside them keeps every field; matching bracket-as-one-field
# silently drops all but the first, which is how abs_alt and the gimbal angles
# went missing.
_BRACKET_RE = re.compile(r"\[([^\]]*)\]")
_PAIR_RE = re.compile(r"([A-Za-z_][A-Za-z0-9_]*)\s*:\s*([^\s\]]+)")
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
    dzoom_ratio: float | None = None
    fnum: float | None = None
    #: inter-frame interval in milliseconds, as reported by the aircraft
    diff_time_ms: float | None = None
    extra: dict[str, str] = field(default_factory=dict)


@dataclass
class Telemetry:
    samples: list[TelemetrySample] = field(default_factory=list)
    source: str = "none"
    #: field name -> where the value came from
    provenance: dict[str, str] = field(default_factory=dict)

    def __len__(self) -> int:
        return len(self.samples)

    FIELDS = ("latitude", "longitude", "rel_alt", "abs_alt", "gb_yaw", "gb_pitch",
              "gb_roll", "focal_len", "dzoom_ratio", "diff_time_ms")

    @property
    def present(self) -> list[str]:
        return [n for n in self.FIELDS if any(getattr(s, n) is not None for s in self.samples)]

    @property
    def missing(self) -> list[str]:
        return [n for n in self.FIELDS if n not in self.present]

    def spread(self, name: str) -> float | None:
        """Peak-to-peak range of a field, for judging how steady the hover was."""
        vals = [v for s in self.samples if (v := getattr(s, name)) is not None]
        return (max(vals) - min(vals)) if vals else None

    def is_nadir(self, tolerance_deg: float = 2.0) -> bool | None:
        """Whether the gimbal points straight down, within a tolerance.

        A locked gimbal is not the same as a nadir gimbal. An off-nadir view makes
        the ground sampling distance vary across the frame, so a single scale
        factor is wrong and a projective rectification is required.
        """
        pitch = self.median("gb_pitch")
        if pitch is None:
            return None
        return abs(abs(pitch) - 90.0) <= tolerance_deg

    def off_nadir_deg(self) -> float | None:
        pitch = self.median("gb_pitch")
        return None if pitch is None else abs(abs(pitch) - 90.0)

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
            spread = self.spread(name)
            extra = f"   range {spread:g}" if spread else ""
            lines.append(f"  {name:12s} median {self.median(name)}{extra}")
        if self.missing:
            lines.append(f"  missing: {', '.join(self.missing)}")
        off = self.off_nadir_deg()
        if off is not None and off > 2.0:
            lines.append(
                f"  WARNING: gimbal is {off:.1f} deg off nadir (pitch {self.median('gb_pitch')}). "
                "Ground sampling distance then varies across the frame, so a single "
                "scale factor is wrong. Use geometry mode 'camera' or 'gcp'."
            )
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
        pairs = []
        for bracket in _BRACKET_RE.findall(block):
            pairs.extend(_PAIR_RE.findall(bracket))
        # DiffTime sits outside the brackets on DJI files
        if dt := re.search(r"DiffTime\s*:\s*([0-9.]+)\s*ms", block, re.IGNORECASE):
            sample.diff_time_ms = float(dt.group(1))
            found = True
        for key, raw in pairs:
            key = key.lower()
            found = True
            if key != "extra" and hasattr(sample, key):
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


def focal_px_from_35mm(focal_len_35mm: float, image_width_px: int,
                       sensor_ref_mm: float = 36.0) -> float:
    """Convert DJI's 35 mm-equivalent focal length to pixels.

    DJI reports ``focal_len`` as a 35 mm equivalent that already includes
    ``dzoom_ratio``, so it should not be multiplied by the zoom again. The
    reference width is the 35 mm frame, 36 mm.
    """
    if focal_len_35mm <= 0 or image_width_px <= 0:
        raise ValueError("focal length and image width must be positive")
    return focal_len_35mm / sensor_ref_mm * float(image_width_px)

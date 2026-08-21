"""DJI telemetry parsing.

A single bracket can hold several fields, which is how ``abs_alt`` and the gimbal
angles get lost by a parser that treats one bracket as one field.
"""

from pathlib import Path

import pytest

from pxv.io.metadata import Telemetry, focal_px_from_35mm, height_above_water, parse_srt

SAMPLE = """1
00:00:00,000 --> 00:00:00,033
<font size="28">FrameCnt: 1, DiffTime: 33ms
2026-08-04 15:02:34.833
[iso: 100] [shutter: 1/4000.0] [fnum: 2.8] [ev: 0] [color_md : default] [ae_meter_md: 1] \
[focal_len: 26.80] [dzoom_ratio: 1.12], [latitude: 47.540613] [longitude: 11.353089] \
[rel_alt: 35.217 abs_alt: 900.696] [gb_yaw: -2.0 gb_pitch: -78.4 gb_roll: 0.0] </font>

2
00:00:00,033 --> 00:00:00,066
<font size="28">FrameCnt: 2, DiffTime: 33ms
2026-08-04 15:02:34.861
[focal_len: 26.80] [latitude: 47.540614] [longitude: 11.353090] \
[rel_alt: 35.243 abs_alt: 900.722] [gb_yaw: -2.1 gb_pitch: -78.4 gb_roll: 0.0] </font>
"""


@pytest.fixture
def telemetry(tmp_path) -> Telemetry:
    path = tmp_path / "clip.SRT"
    path.write_text(SAMPLE)
    return parse_srt(path)


def test_multi_field_brackets_are_all_captured(telemetry):
    s = telemetry.samples[0]
    assert s.rel_alt == pytest.approx(35.217)
    assert s.abs_alt == pytest.approx(900.696)      # second field in the same bracket
    assert s.gb_yaw == pytest.approx(-2.0)
    assert s.gb_pitch == pytest.approx(-78.4)       # second field in the same bracket
    assert s.gb_roll == pytest.approx(0.0)          # third field in the same bracket


def test_other_fields(telemetry):
    s = telemetry.samples[0]
    assert s.latitude == pytest.approx(47.540613)
    assert s.focal_len == pytest.approx(26.80)
    assert s.dzoom_ratio == pytest.approx(1.12)
    assert s.fnum == pytest.approx(2.8)
    assert s.diff_time_ms == pytest.approx(33.0)
    assert s.extra["iso"] == "100"
    assert s.extra["shutter"] == "1/4000.0"
    assert s.extra["color_md"] == "default"          # note the space before the colon
    assert s.timestamp is not None


def test_nothing_reported_missing(telemetry):
    assert not telemetry.missing


def test_spread_and_nadir_detection(telemetry):
    assert telemetry.spread("rel_alt") == pytest.approx(0.026)
    assert telemetry.is_nadir() is False
    assert telemetry.off_nadir_deg() == pytest.approx(11.6)
    assert "off nadir" in telemetry.describe()


def test_nadir_within_tolerance(tmp_path):
    path = tmp_path / "n.SRT"
    path.write_text(SAMPLE.replace("-78.4", "-89.6"))
    tel = parse_srt(path)
    assert tel.is_nadir() is True
    assert "off nadir" not in tel.describe()


def test_height_above_water_is_explicit():
    assert height_above_water(35.2, 0.0) == pytest.approx(35.2)
    assert height_above_water(35.2, -1.5) == pytest.approx(33.7)


def test_focal_conversion():
    assert focal_px_from_35mm(26.8, 1920) == pytest.approx(26.8 / 36.0 * 1920)
    with pytest.raises(ValueError):
        focal_px_from_35mm(0.0, 1920)


def test_real_isar_srt_if_present():
    path = (Path(__file__).resolve().parents[1] / "examples" / "isar-20260804"
            / "DJI_20260804150234_0002_D.SRT")
    if not path.exists():
        pytest.skip("Isar sample not present")
    tel = parse_srt(path)
    assert len(tel) > 3000
    assert not tel.missing
    assert tel.median("gb_pitch") == pytest.approx(-78.4)
    assert tel.is_nadir() is False

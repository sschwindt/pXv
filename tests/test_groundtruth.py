"""The ground-truth path, exercised with a synthetic FlowTracker2-style GeoPackage."""

import numpy as np
import pytest

geopandas = pytest.importorskip("geopandas")
from shapely.geometry import Point  # noqa: E402

from pxv.io.groundtruth import FT2_FLOOR_MS, default_sigma, read_groundtruth  # noqa: E402
from pxv.project import GroundTruthCfg  # noqa: E402

UK, Z0 = 0.12, 0.005


def make_gpkg(path, epsg=25832, with_sigma=True, with_vertical=True, with_depth=True):
    """Six verticals across a channel: three with both heights, three with one."""
    rows = []
    rng = np.random.default_rng(0)
    for i in range(6):
        x = 500000.0 + i * 2.0
        y = 5400000.0
        h = 0.7 + 0.05 * i
        heights = (0.6, 0.8) if i < 3 else (0.6,)
        for z in heights:
            speed = UK * np.log(z * h / Z0)
            bearing = np.radians(75.0)
            rows.append({
                "geometry": Point(x, y),
                "vertical_id": f"V{i}",
                "z_over_h": z,
                "h": h,
                "speed": speed,
                "u": speed * np.sin(bearing),
                "v": speed * np.cos(bearing),
                "sigma": max(0.01 * speed, FT2_FLOOR_MS),
            })
    gdf = geopandas.GeoDataFrame(rows, crs=f"EPSG:{epsg}")
    if not with_sigma:
        gdf = gdf.drop(columns=["sigma"])
    if not with_vertical:
        gdf = gdf.drop(columns=["vertical_id"])
    if not with_depth:
        gdf = gdf.drop(columns=["h"])
    gdf.to_file(path, layer="velocity", driver="GPKG")
    return path


def test_reads_all_attributes(tmp_path):
    p = make_gpkg(tmp_path / "gt.gpkg")
    gt = read_groundtruth(GroundTruthCfg(path=str(p), layer="velocity"))
    assert len(gt) == 9
    assert gt.epsg == 25832
    assert gt.has_direction
    assert gt.depth is not None
    assert gt.vertical_id is not None
    assert not gt.warnings


def test_missing_sigma_falls_back_to_instrument_spec(tmp_path):
    p = make_gpkg(tmp_path / "gt.gpkg", with_sigma=False)
    gt = read_groundtruth(GroundTruthCfg(path=str(p), layer="velocity"))
    assert gt.sigma is not None
    assert any("FlowTracker2" in w for w in gt.warnings)


def test_missing_vertical_id_is_reported(tmp_path):
    """Without it the roughness-free two-point method cannot be used."""
    p = make_gpkg(tmp_path / "gt.gpkg", with_vertical=False)
    gt = read_groundtruth(GroundTruthCfg(path=str(p), layer="velocity"))
    assert any("two-point" in w for w in gt.warnings)


def test_missing_depth_is_reported(tmp_path):
    p = make_gpkg(tmp_path / "gt.gpkg", with_depth=False)
    gt = read_groundtruth(GroundTruthCfg(path=str(p), layer="velocity"))
    assert any("depth" in w for w in gt.warnings)


def test_default_sigma_respects_the_floor():
    assert default_sigma(np.array([0.0]))[0] == pytest.approx(FT2_FLOOR_MS)
    assert default_sigma(np.array([1.0]))[0] == pytest.approx(0.01)


def test_conversion_recovers_the_surface_profile(tmp_path):
    from pxv.calibrate.profile import convert_to_surface

    p = make_gpkg(tmp_path / "gt.gpkg")
    gt = read_groundtruth(GroundTruthCfg(path=str(p), layer="velocity"))
    conv = convert_to_surface(gt.speed, gt.z_over_h, gt.depth, gt.sigma,
                              gt.vertical_id, method="hybrid")
    assert conv.z0_estimate == pytest.approx(Z0, rel=1e-4)
    expected = UK * np.log(gt.depth / Z0)
    assert np.allclose(conv.u_surface, expected, rtol=1e-5)

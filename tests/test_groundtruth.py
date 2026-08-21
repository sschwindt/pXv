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


def test_missing_sigma_falls_back_to_the_configured_relative_error(tmp_path):
    p = make_gpkg(tmp_path / "gt.gpkg", with_sigma=False)
    cfg = GroundTruthCfg(path=str(p), layer="velocity", sigma_rel=0.05)
    gt = read_groundtruth(cfg)
    assert gt.sigma is not None
    assert any("sigma_rel=5%" in w for w in gt.warnings)
    fast = gt.speed > 0.5
    assert np.allclose(gt.sigma[fast], 0.05 * gt.speed[fast])


def test_verticals_recovered_from_geometry(tmp_path):
    """Points sharing a location are paired even without a vertical column."""
    p = make_gpkg(tmp_path / "gt.gpkg", with_vertical=False)
    gt = read_groundtruth(GroundTruthCfg(path=str(p), layer="velocity"))
    assert gt.vertical_id is not None
    _, counts = np.unique(gt.vertical_id, return_counts=True)
    assert len(counts) == 6          # six verticals in the fixture
    assert (counts >= 2).sum() == 3  # three of them carry both heights


def test_vertical_pairing_can_be_switched_off(tmp_path):
    p = make_gpkg(tmp_path / "gt.gpkg", with_vertical=False)
    cfg = GroundTruthCfg(path=str(p), layer="velocity", vertical_from_geometry=False)
    gt = read_groundtruth(cfg)
    assert gt.vertical_id is None
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


def test_gauging_depth_convention(tmp_path):
    """Meas/Total is depth below the surface, so z/h above bed is 1 - ratio.

    Getting this backwards inverts the velocity profile, so it is worth pinning.
    """
    import geopandas as gpd
    from shapely.geometry import Point

    rows = [
        {"geometry": Point(0.0, 0.0), "Meas_Depth_m": 0.08, "Total_Depth_m": 0.40,
         "v_x_m_s": 1.5, "v_y_m_s": 0.0},      # 0.2 below surface, fast
        {"geometry": Point(0.0, 0.0), "Meas_Depth_m": 0.32, "Total_Depth_m": 0.40,
         "v_x_m_s": 1.0, "v_y_m_s": 0.0},      # 0.8 below surface, slow
    ]
    path = tmp_path / "g.gpkg"
    gpd.GeoDataFrame(rows, crs="EPSG:25832").to_file(path, layer="v", driver="GPKG")

    cfg = GroundTruthCfg(path=str(path), layer="v", col_u="v_x_m_s", col_v="v_y_m_s",
                         col_speed="", col_depth="Total_Depth_m",
                         depth_convention="below_surface")
    gt = read_groundtruth(cfg)
    assert np.allclose(sorted(np.round(gt.z_over_h, 2)), [0.2, 0.8])
    # the faster point must come out higher in the profile
    assert gt.z_over_h[np.argmax(gt.speed)] > gt.z_over_h[np.argmin(gt.speed)]

    cfg.depth_convention = "above_bed"
    flipped = read_groundtruth(cfg)
    assert np.allclose(sorted(np.round(flipped.z_over_h, 2)), [0.2, 0.8])
    assert flipped.z_over_h[np.argmax(flipped.speed)] < flipped.z_over_h[np.argmin(flipped.speed)]


def test_real_isar_file_if_present():
    """The shipped survey, if it is in the working tree."""
    from pathlib import Path

    path = Path(__file__).resolve().parents[1] / "examples" / "isar-20260804" / "IsarAug2026.gpkg"
    if not path.exists():
        pytest.skip("Isar sample not present")
    cfg = GroundTruthCfg(path=str(path), layer="ft_tke_points", col_u="v_x_m_s",
                         col_v="v_y_m_s", col_speed="", col_depth="Total_Depth_m")
    gt = read_groundtruth(cfg)
    assert gt.epsg == 25832
    assert gt.has_direction
    heights = np.round(gt.z_over_h[np.isfinite(gt.z_over_h)], 1)
    # the classic 0.2 / 0.6 / 0.8 below-surface gauging positions
    assert {0.2, 0.4, 0.8} <= set(np.unique(heights))

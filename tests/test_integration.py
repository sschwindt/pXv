"""End-to-end run against the lab clip in the repository."""

from pathlib import Path

import numpy as np
import pytest

from pxv.project import load_project

PROJECT = Path(__file__).resolve().parents[1] / "examples" / "mq-slots.yaml"
VIDEO = Path(__file__).resolve().parents[1] / "tractracSources" / "ssc-samples" / "MQ-slots.mp4"

pytestmark = pytest.mark.skipif(not VIDEO.exists(), reason="sample video not present")


@pytest.fixture(scope="module")
def result():
    from pxv.pipeline import run

    project = load_project(PROJECT)
    project.video.path = str(VIDEO)
    project.video.start = 300
    project.video.stop = 316          # 8 processed frames, enough to exercise everything
    project.video.step = 2
    return run(project, verbose=False)


def test_pipeline_produces_a_field(result):
    assert result.field.coverage() > 0.5
    assert result.dt == pytest.approx(2 / 30.0)
    assert np.isfinite(result.transform.scale_m_per_px)


def test_velocities_are_physical(result):
    speed = result.speed_world()[result.field.valid]
    assert speed.size > 100
    # a fishway pool recirculation, not a jet: centimetres per second
    assert 0.0 < np.median(speed) < 0.5


def test_both_engines_contribute(result):
    from pxv.velocimetry.fuse import SOURCE_PIV, SOURCE_PTV

    assert (result.field.source == SOURCE_PTV).sum() > 0
    assert (result.field.source == SOURCE_PIV).sum() > 0


def test_static_structures_report_near_zero_motion(result):
    """The flume rail at the top of the frame does not move."""
    speed = result.speed_world()
    top = speed[:2][result.field.valid[:2]]
    mid = speed[8:16][result.field.valid[8:16]]
    if top.size and mid.size:
        assert np.median(top) < np.median(mid)


def test_exports_are_written(result, tmp_path):
    from pxv.export import datasets, streamlines

    assert streamlines.plot_streamlines(result, tmp_path / "s.jpg").exists()
    assert datasets.write_geotiff(result, tmp_path / "v.tif").exists()
    assert datasets.write_gpkg(result, tmp_path / "v.gpkg").exists()
    assert datasets.write_ugrid(result, tmp_path / "m.nc").exists()


def test_ugrid_is_readable_and_shaped_right(result, tmp_path):
    netCDF4 = pytest.importorskip("netCDF4")
    path = datasets_write(result, tmp_path)
    with netCDF4.Dataset(path) as ds:
        assert ds.Conventions.startswith("CF-1.8 UGRID")
        ny, nx = result.field.u.shape
        assert ds.dimensions["nMesh2d_face"].size == ny * nx
        assert ds.dimensions["nMesh2d_node"].size == (ny + 1) * (nx + 1)
        assert set(("velocity_x", "velocity_y", "magnitude", "direction")) <= set(ds.variables)
        assert ds.variables["Mesh2d_face_nodes"].shape == (ny * nx, 4)


def datasets_write(result, tmp_path):
    from pxv.export import datasets

    return datasets.write_ugrid(result, tmp_path / "mesh.nc")

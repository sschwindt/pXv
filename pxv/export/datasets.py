"""Writing the velocity field for QGIS.

UGRID NetCDF is the primary mesh format: QGIS reads it natively as a Mesh Layer
through MDAL and renders arrows, streamlines and particle traces without any
further processing. GeoTIFF and GeoPackage are always written as well, so a
version mismatch in MDAL never leaves you with nothing.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np


def _world_arrays(result):
    wx, wy = result.world_grid()
    u, v = result.velocity_world()
    valid = result.field.valid
    return wx, wy, u, v, valid


def write_geotiff(result, path: str | Path) -> Path | None:
    """Three bands: u, v, magnitude. Needs a uniform (non-perspective) transform."""
    try:
        import rasterio
        from rasterio.transform import Affine
    except ImportError:
        return None
    t = result.transform
    if not np.isfinite(t.scale_m_per_px):
        return None

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    u, v = result.velocity_world()
    valid = result.field.valid
    u = np.where(valid, u, np.nan).astype(np.float32)
    v = np.where(valid, v, np.nan).astype(np.float32)
    mag = np.hypot(u, v).astype(np.float32)

    wx, wy = result.world_grid()
    cell = t.scale_m_per_px * result.field.cell_px
    west = float(wx[0, 0] - cell / 2.0)
    north = float(wy[0, 0] - cell / 2.0)
    transform = Affine.translation(west, north) * Affine.scale(cell, cell)

    crs = f"EPSG:{t.epsg}" if t.epsg else None
    with rasterio.open(
        path, "w", driver="GTiff", height=u.shape[0], width=u.shape[1], count=3,
        dtype="float32", crs=crs, transform=transform, nodata=np.nan,
    ) as dst:
        dst.write(u, 1); dst.set_band_description(1, "u")
        dst.write(v, 2); dst.set_band_description(2, "v")
        dst.write(mag, 3); dst.set_band_description(3, "magnitude")
    return path


def write_gpkg(result, path: str | Path, layer: str = "velocity") -> Path | None:
    """One point per grid cell, with provenance so mixed-engine runs stay auditable."""
    try:
        import geopandas as gpd
        from shapely.geometry import Point
    except ImportError:
        return None

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    wx, wy, u, v, valid = _world_arrays(result)
    m = valid.ravel()
    if not m.any():
        return None
    fld = result.field
    speed = np.hypot(u, v)
    src_names = np.array(["none", "piv", "ptv", "interpolated"])
    gdf = gpd.GeoDataFrame(
        {
            "u": u.ravel()[m],
            "v": v.ravel()[m],
            "magnitude": speed.ravel()[m],
            "direction": fld.direction_deg().ravel()[m],
            "n_samples": fld.n.ravel()[m],
            "source": src_names[fld.source.ravel()[m]],
        },
        geometry=[Point(a, b) for a, b in zip(wx.ravel()[m], wy.ravel()[m])],
        crs=f"EPSG:{result.transform.epsg}" if result.transform.epsg else None,
    )
    gdf.to_file(path, layer=layer, driver="GPKG")
    return path


def write_ugrid(result, path: str | Path) -> Path | None:
    """UGRID-conventions NetCDF with a quadrilateral mesh, readable by QGIS/MDAL."""
    try:
        from netCDF4 import Dataset
    except ImportError:
        return None

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    wx, wy, u, v, valid = _world_arrays(result)
    ny, nx = u.shape
    t = result.transform

    # node grid is one larger in each direction than the cell grid
    px = np.arange(nx + 1) * result.field.cell_px
    py = np.arange(ny + 1) * result.field.cell_px
    pxx, pyy = np.meshgrid(px, py)
    nodes_x, nodes_y = t.to_world(pxx, pyy)

    node_x = nodes_x.ravel()
    node_y = nodes_y.ravel()
    n_nodes = node_x.size
    n_faces = ny * nx

    def nid(j, i):
        return j * (nx + 1) + i

    faces = np.empty((n_faces, 4), dtype=np.int32)
    k = 0
    for j in range(ny):
        for i in range(nx):
            faces[k] = (nid(j, i), nid(j, i + 1), nid(j + 1, i + 1), nid(j + 1, i))
            k += 1

    speed = np.hypot(u, v)
    direction = result.field.direction_deg()
    fill = -9999.0

    with Dataset(path, "w", format="NETCDF4") as ds:
        ds.Conventions = "CF-1.8 UGRID-1.0"
        ds.title = f"pXv surface velocity: {result.project.name}"
        ds.institution = "pXv"
        ds.source = "pXv image velocimetry"

        ds.createDimension("nMesh2d_node", n_nodes)
        ds.createDimension("nMesh2d_face", n_faces)
        ds.createDimension("nMaxMesh2d_face_nodes", 4)
        ds.createDimension("time", 1)

        mesh = ds.createVariable("Mesh2d", "i4")
        mesh.cf_role = "mesh_topology"
        mesh.topology_dimension = 2
        mesh.node_coordinates = "Mesh2d_node_x Mesh2d_node_y"
        mesh.face_node_connectivity = "Mesh2d_face_nodes"
        mesh.face_dimension = "nMesh2d_face"

        vx = ds.createVariable("Mesh2d_node_x", "f8", ("nMesh2d_node",))
        vx.standard_name = "projection_x_coordinate"
        vx.units = "m"
        vx[:] = node_x
        vy = ds.createVariable("Mesh2d_node_y", "f8", ("nMesh2d_node",))
        vy.standard_name = "projection_y_coordinate"
        vy.units = "m"
        vy[:] = node_y

        fn = ds.createVariable("Mesh2d_face_nodes", "i4",
                               ("nMesh2d_face", "nMaxMesh2d_face_nodes"), fill_value=-1)
        fn.cf_role = "face_node_connectivity"
        fn.start_index = 0
        fn[:, :] = faces

        tv = ds.createVariable("time", "f8", ("time",))
        tv.standard_name = "time"
        tv.units = "seconds since 1970-01-01 00:00:00"
        tv[:] = [0.0]

        def add(name, data, units, long_name):
            var = ds.createVariable(name, "f8", ("time", "nMesh2d_face"), fill_value=fill)
            var.mesh = "Mesh2d"
            var.location = "face"
            var.units = units
            var.long_name = long_name
            var.coordinates = "Mesh2d_face_x Mesh2d_face_y"
            var[0, :] = np.where(valid, data, fill).ravel()

        unit = t.units + "/s"
        add("velocity_x", u, unit, "surface velocity, x component")
        add("velocity_y", v, unit, "surface velocity, y component")
        add("magnitude", speed, unit, "surface velocity magnitude")
        add("direction", direction, "degree", "surface velocity direction, clockwise from up-image")

        fx = ds.createVariable("Mesh2d_face_x", "f8", ("nMesh2d_face",))
        fx.standard_name = "projection_x_coordinate"
        fx.units = "m"
        fx[:] = wx.ravel()
        fy = ds.createVariable("Mesh2d_face_y", "f8", ("nMesh2d_face",))
        fy.standard_name = "projection_y_coordinate"
        fy.units = "m"
        fy[:] = wy.ravel()

        if t.epsg:
            crsv = ds.createVariable("projected_coordinate_system", "i4")
            crsv.epsg = t.epsg
            crsv.EPSG_code = f"EPSG:{t.epsg}"
    return path


def discharge_check(result, section_y_px: float | None = None,
                    depth_m: float = 0.0, alpha: float = 0.85,
                    known_q_m3s: float = 0.0) -> dict:
    """Integrate the surface field across one row and compare against a known discharge.

    Mass conservation is the cheapest independent check available, and in a flume
    with a metered discharge it is the only ground truth there is.
    """
    fld = result.field
    u, v = result.velocity_world()
    t = result.transform
    if not np.isfinite(t.scale_m_per_px):
        return {"ok": False, "reason": "needs a uniform metric transform"}

    if section_y_px is None:
        section_y_px = float(np.median(fld.y[fld.valid])) if fld.valid.any() else float(fld.y.mean())
    row = int(np.argmin(np.abs(fld.y[:, 0] - section_y_px)))

    valid = fld.valid[row]
    if not valid.any():
        return {"ok": False, "reason": "no valid cells on the chosen section"}

    # streamwise component is the one crossing the section, i.e. along image y
    v_row = np.abs(v[row][valid])
    dy = t.scale_m_per_px * fld.cell_px
    width_m = float(valid.sum() * dy)
    mean_surface = float(np.mean(v_row))
    out = {
        "ok": True,
        "row": row,
        "section_y_px": float(fld.y[row, 0]),
        "width_m": width_m,
        "mean_surface_speed_ms": mean_surface,
        "alpha": alpha,
    }
    if depth_m > 0:
        q = mean_surface * alpha * depth_m * width_m
        out["computed_discharge_m3s"] = q
        if known_q_m3s > 0:
            out["known_discharge_m3s"] = known_q_m3s
            out["relative_error"] = (q - known_q_m3s) / known_q_m3s
    elif known_q_m3s > 0 and width_m > 0 and mean_surface > 0:
        # invert for the depth the field implies, which is a sanity check in itself
        out["implied_depth_m"] = known_q_m3s / (mean_surface * alpha * width_m)
        out["known_discharge_m3s"] = known_q_m3s
    return out

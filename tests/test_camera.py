"""Telemetry-driven camera geometry.

A locked gimbal is not a nadir gimbal, and even a modest tilt makes the ground
sampling distance vary across the frame.
"""

import cv2
import numpy as np
import pytest

from pxv.geometry.world import (
    build_transform, camera_homography, camera_rotation, gsd_variation, intrinsics,
)
from pxv.project import GeometryCfg

W, H, F, HEIGHT = 1920, 1080, 1429.3, 35.2


def test_nadir_gives_uniform_gsd_equal_to_height_over_focal():
    m = camera_homography(HEIGHT, F, W, H, 0.0, -90.0, 0.0)
    centre, top, bottom = gsd_variation(m, W, H)
    assert centre == pytest.approx(HEIGHT / F, rel=1e-6)
    assert top == pytest.approx(centre, rel=1e-9)
    assert bottom == pytest.approx(centre, rel=1e-9)


def test_nadir_centre_pixel_maps_to_the_origin():
    m = camera_homography(HEIGHT, F, W, H, 0.0, -90.0, 0.0)
    p = cv2.perspectiveTransform(np.array([[[W / 2.0, H / 2.0]]]), m).reshape(2)
    assert np.allclose(p, [0.0, 0.0], atol=1e-9)


def test_nadir_image_up_is_north():
    m = camera_homography(HEIGHT, F, W, H, 0.0, -90.0, 0.0)
    above = cv2.perspectiveTransform(np.array([[[W / 2.0, H / 2.0 - 100]]]), m).reshape(2)
    assert above[1] > 0            # smaller row index is further north
    right = cv2.perspectiveTransform(np.array([[[W / 2.0 + 100, H / 2.0]]]), m).reshape(2)
    assert right[0] > 0            # larger column index is further east


def test_tilt_makes_gsd_vary_across_the_frame():
    m = camera_homography(HEIGHT, F, W, H, -2.0, -78.4, 0.0)
    centre, top, bottom = gsd_variation(m, W, H)
    assert top > centre > bottom          # the top of the frame is further away
    assert max(top, bottom) / min(top, bottom) > 1.1


def test_yaw_rotates_the_footprint():
    straight = camera_homography(HEIGHT, F, W, H, 0.0, -90.0, 0.0)
    turned = camera_homography(HEIGHT, F, W, H, 90.0, -90.0, 0.0)
    pt = np.array([[[W / 2.0, H / 2.0 - 100]]])
    a = cv2.perspectiveTransform(pt, straight).reshape(2)
    b = cv2.perspectiveTransform(pt, turned).reshape(2)
    assert a[1] == pytest.approx(np.hypot(*b), rel=1e-6)   # north becomes east
    assert b[0] == pytest.approx(a[1], rel=1e-6)
    assert abs(b[1]) < 1e-6


def test_rotation_is_orthonormal():
    r = camera_rotation(-2.0, -78.4, 3.0)
    assert np.allclose(r @ r.T, np.eye(3), atol=1e-12)
    assert np.linalg.det(r) == pytest.approx(1.0)


def test_intrinsics_shape():
    k = intrinsics(F, W, H)
    assert k[0, 2] == W / 2 and k[1, 2] == H / 2


def test_negative_height_rejected():
    with pytest.raises(ValueError):
        camera_homography(-1.0, F, W, H)


def test_build_transform_uses_telemetry():
    class Tel:
        def median(self, name):
            return {"rel_alt": HEIGHT, "gb_yaw": -2.0, "gb_pitch": -78.4,
                    "gb_roll": 0.0, "focal_len": 26.8,
                    "latitude": 47.540613, "longitude": 11.353089}.get(name)

    cfg = GeometryCfg(mode="camera", epsg=25832)
    t = build_transform(cfg, (H, W), telemetry=Tel())
    assert t.epsg == 25832
    # the frame centre should land on the aircraft's own position
    x, y = t.to_world(np.array([W / 2.0]), np.array([H / 2.0]))
    assert 676000 < float(x[0]) < 678000
    assert 5267000 < float(y[0]) < 5269000
    assert any("off nadir" in n for n in t.notes)

"""Co-registering a second camera stream onto the primary one.

A multispectral payload sits beside the RGB camera and sees the same scene through
different optics, so its frames must be brought into the primary image frame
before the two can be combined.
"""

from pathlib import Path

import cv2
import numpy as np
import pytest

from pxv.geometry.coregister import Registration, register_streams

ISAR = Path(__file__).resolve().parents[1] / "examples" / "isar-20260804"
PRIMARY = ISAR / "DJI_20260804150234_0002_D.MP4"
SECONDARY = ISAR / "DJI_20260804150234_0002_F.MP4"


def test_registration_quality_flag():
    good = Registration(np.eye(3), 100, 60, 0.6, 1.2)
    weak = Registration(np.eye(3), 100, 5, 8.0, 20.0)
    assert good.ok
    assert not weak.ok
    assert "weak" in weak.describe()
    assert "weak" not in good.describe()


def test_registration_warps_into_the_primary_frame():
    matrix = np.array([[1.1, 0.0, 20.0], [0.0, 1.1, -10.0], [0.0, 0.0, 1.0]])
    reg = Registration(matrix, 50, 40, 0.5, 1.0)
    frame = np.zeros((100, 200, 3), np.uint8)
    out = reg.warp(frame, (80, 160))
    assert out.shape[:2] == (80, 160)


def test_too_few_correspondences_raises(tmp_path):
    """Two streams that do not show the same scene must fail loudly."""
    paths = []
    for name, value in (("a.avi", 0), ("b.avi", 255)):
        p = tmp_path / name
        writer = cv2.VideoWriter(str(p), cv2.VideoWriter_fourcc(*"MJPG"), 10, (160, 120))
        for _ in range(5):
            writer.write(np.full((120, 160, 3), value, np.uint8))
        writer.release()
        paths.append(p)
    with pytest.raises((RuntimeError, IOError)):
        register_streams(paths[0], paths[1], [0, 2])


@pytest.mark.skipif(not (PRIMARY.exists() and SECONDARY.exists()),
                    reason="Isar footage not present")
def test_real_streams_register():
    reg = register_streams(PRIMARY, SECONDARY, [200, 700, 1400, 2200])
    assert reg.ok
    assert reg.residual_median < 2.0
    assert reg.n_inliers >= 20

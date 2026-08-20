import numpy as np
import pytest

try:
    import cv2
except ImportError:  # pragma: no cover
    cv2 = None


@pytest.fixture
def rng():
    return np.random.default_rng(1234)


def render_particles(shape, xs, ys, sigma=1.6):
    """Render point particles as Gaussian blobs."""
    h, w = shape
    img = np.zeros((h, w), np.float32)
    xi = np.clip(np.round(xs).astype(int), 0, w - 1)
    yi = np.clip(np.round(ys).astype(int), 0, h - 1)
    np.add.at(img, (yi, xi), 1.0)
    return cv2.GaussianBlur(img, (0, 0), sigma)


@pytest.fixture
def particle_pair(rng):
    """A pair of particle images with a known uniform displacement."""
    def make(du, dv, shape=(256, 256), n=1500):
        xs = rng.uniform(0, shape[1], n)
        ys = rng.uniform(0, shape[0], n)
        return render_particles(shape, xs, ys), render_particles(shape, xs + du, ys + dv)
    return make

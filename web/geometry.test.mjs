import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import test from 'node:test';
import { cameraGeometry, controlPointGeometry, linearGeometry, parseDjiSrt } from './geometry.mjs';

const isarSubtitles = new URL('../examples/isar-20260804/DJI_20260804150234_0002_D.SRT', import.meta.url);

function near(actual, expected, tolerance) {
  assert.ok(Math.abs(actual - expected) <= tolerance, `${actual} differs from ${expected}`);
}

test('Isar telemetry uses the stabilized reference window and DJI frame timing', async () => {
  const telemetry = parseDjiSrt(await readFile(isarSubtitles, 'utf8'), { startFrame: 1250, endFrame: 1650 });
  assert.equal(telemetry.frameCount, 3884);
  near(telemetry.fps, 30000 / 1001, 0.001);
  near(telemetry.heightAboveTakeoff, 35.2125, 0.00001);
  assert.equal(telemetry.focal35, 26.8);
  assert.deepEqual([telemetry.yaw, telemetry.pitch, telemetry.roll], [-2, -78.4, 0]);

  const geometry = cameraGeometry({ width: 1920, height: 1080, ...telemetry });
  assert.equal(geometry.epsg, 25832);
  // PROJ EPSG:4326 -> EPSG:25832 at the median DJI position.
  near(geometry.origin[0], 677078.134653, 0.001);
  near(geometry.origin[1], 5267926.802315, 0.001);
  near(geometry.gsd.center, 0.02514928, 1e-8);
  near(geometry.gsd.top, 0.02725935, 1e-8);
  near(geometry.gsd.bottom, 0.02334241, 1e-8);
  const [east, north] = geometry.displacement(1234, 765, 2, -1, 1001 / 30000);
  // Values from the Python image-to-plane homography for the same telemetry.
  near(east, 1.46219386, 1e-7);
  near(north, 0.77362654, 1e-7);
  for (const [x, y] of [[0, 0], [960, 540], [1234, 765], [1919, 1079]]) {
    const [u, v] = geometry.toImage(...geometry.toWorld(x, y));
    near(u, x, 1e-6);
    near(v, y, 1e-6);
  }
});

test('nadir geometry gives the analytic metric scale and east/north signs', () => {
  const geometry = cameraGeometry({ width: 1000, height: 600, heightAboveTakeoff: 19,
    waterOffset: 1, focal35: 36 });
  assert.equal(geometry.epsg, null);
  assert.equal(geometry.heightAboveWater, 20);
  assert.deepEqual(geometry.toWorld(500, 300), [0, 0]);
  const [east, north] = geometry.displacement(500, 300, 5, -2, 0.2);
  near(east, 0.5, 1e-12);
  near(north, 0.2, 1e-12);
  near(geometry.gsd.center, 0.02, 1e-12);
  near(geometry.gsd.top, geometry.gsd.bottom, 1e-12);
});

test('linear metric and pixel modes keep image axes and use elapsed seconds', () => {
  assert.deepEqual(linearGeometry().displacement(25, 50, 5, -3, 0.5), [10, -6]);
  const geometry = linearGeometry(0.04);
  assert.deepEqual(geometry.toImage(...geometry.toWorld(25, 50)), [25, 50]);
  const [u, v] = geometry.displacement(25, 50, 5, -3, 0.5);
  near(u, 0.4, 1e-12);
  near(v, -0.24, 1e-12);
  assert.throws(() => linearGeometry(0), /positive/);
  assert.throws(() => geometry.displacement(25, 50, 5, -3, 0), /positive/);
});

test('a DJI position in the southern hemisphere uses the corresponding local UTM zone', () => {
  const geometry = cameraGeometry({ width: 1920, height: 1080, heightAboveTakeoff: 30,
    focal35: 24, latitude: -33.8568, longitude: 151.2153 });
  assert.equal(geometry.epsg, 32756);
  assert.equal(geometry.inputEpsg, 4326);
  // PROJ EPSG:4326 → EPSG:32756 at the Sydney Opera House.
  near(geometry.origin[0], 334900.56965, 0.01);
  near(geometry.origin[1], 6252288.75289, 0.01);
});

test('control points recover affine scale, rotation, and spatial location without DJI data', () => {
  const image = [[20, 10], [120, 10], [20, 110]];
  const world = image.map(([x, y]) => [677000 + x * 0.025 + y * 0.004, 5268000 + x * 0.004 - y * 0.025]);
  const geometry = controlPointGeometry({ image, world, epsg: 'EPSG:25832' });
  assert.equal(geometry.epsg, 25832);
  assert.equal(geometry.controlPointCount, 3);
  assert.equal(geometry.model, 'affine');
  near(geometry.controlPointRmse, 0, 1e-8);
  const [x, y] = geometry.toImage(...geometry.toWorld(80, 130));
  near(x, 80, 1e-7);
  near(y, 130, 1e-7);
  const [east, north] = geometry.displacement(80, 130, 10, -4, 0.5);
  near(east, 0.468, 1e-8);
  near(north, 0.28, 1e-8);
  assert.throws(() => controlPointGeometry({ image: [[0, 0], [1, 1], [2, 2]], world: world.slice(0, 3), epsg: 25832 }), /collinear/);
});

test('a projective fit models variable metric scale and inverse position across the frame', () => {
  const image = [[100, 100], [900, 110], [110, 510], [880, 490], [570, 260]];
  const expectedWorld = (x, y) => {
    const divisor = 1 + 0.0001 * x + 0.0005 * y;
    return [677000 + (0.025 * x + 0.004 * y) / divisor, 5268000 + (0.004 * x - 0.025 * y) / divisor];
  };
  const world = image.map(point => expectedWorld(...point));
  const geometry = controlPointGeometry({ image, world, epsg: 25832, width: 1000, height: 600 });
  assert.equal(geometry.model, 'projective');
  assert.ok(geometry.gsd.top > geometry.gsd.bottom);
  const coordinate = geometry.toWorld(490, 350);
  const [e, n] = expectedWorld(490, 350);
  near(coordinate[0], e, 1e-8);
  near(coordinate[1], n, 1e-8);
  const [x, y] = geometry.toImage(...coordinate);
  near(x, 490, 1e-6);
  near(y, 350, 1e-6);
  assert.throws(() => controlPointGeometry({
    image: [[0, 0], [10, 0], [20, 0], [0, 10]], world: world.slice(0, 4), epsg: 25832,
  }), /collinear|invertible|horizon/);
});

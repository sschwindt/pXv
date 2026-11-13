import assert from "node:assert/strict";
import test from "node:test";
import { analyzeFrames, createAnalyzer } from "./velocimetry.mjs";

const width = 144;
const height = 144;
const regions = {
  staticPolygons: [[[0, 0], [144, 0], [144, 49], [0, 49]]],
  include: [[[30, 50], [115, 50], [115, 120], [30, 120]]],
};

function syntheticFrames({ moving = true } = {}) {
  let seed = 123456;
  const texture = Uint8Array.from({ length: width * height }, () => {
    seed = (Math.imul(seed, 1664525) + 1013904223) >>> 0;
    return (seed >>> 24) / 2;
  });
  return Array.from({ length: 8 }, (_, i) => {
    const gray = new Uint8Array(width * height);
    const cameraX = i % 3 - 1;
    const cameraY = i % 2;
    const flowX = moving ? 2 * i : 0;
    const flowY = moving ? i : 0;
    for (let y = 0; y < height; y++) {
      for (let x = 0; x < width; x++) {
        const rx = x - cameraX;
        const ry = y - cameraY;
        const water = rx > 30 && rx < 115 && ry > 50 && ry < 120;
        const sx = rx - (water ? flowX : 0);
        const sy = ry - (water ? flowY : 0);
        const index = y * width + x;
        gray[index] = sx >= 0 && sy >= 0 && sx < width && sy < height ? texture[sy * width + sx] : 40;
        const tracer = rx >= 52 + flowX && rx < 57 + flowX && ry >= 64 + flowY && ry < 69 + flowY;
        const stationaryBrightObject = rx >= 80 && rx < 85 && ry >= 75 && ry < 80;
        if (tracer || stationaryBrightObject) gray[index] = 246;
      }
    }
    return { gray, width, height, time: i / 10 };
  });
}

const options = { ...regions, method: "combined", minArea: 20, maxArea: 50, windowSize: 17, cellSize: 12 };

test("stabilized tracer tracking and texture correlation recover a known motion", () => {
  const analyzer = createAnalyzer(options);
  for (const frame of syntheticFrames()) analyzer.push(frame);
  const result = analyzer.finish();
  assert.equal(result.diagnostics.stabilizationFailures, 0);
  assert.ok(result.diagnostics.stabilizationResidualPx < 0.1);
  assert.equal(result.tracks.length, 1);
  assert.ok(result.ptvVectors.length > 0);
  assert.ok(result.ptvVectors.some(({ count }) => count > 1));
  assert.ok(result.ptvVectors.every(({ trackCount }) => trackCount === 1));
  assert.equal(result.pivGridStep, 9);
  assert.ok(result.pivVectors.length > 0);
  assert.ok(result.ptvVectors.every(({ u, v }) => Math.abs(u - 20) < 0.5 && Math.abs(v - 10) < 0.5));
  const accuratePiv = result.pivVectors.filter(({ u, v }) => Math.abs(u - 20) < 1 && Math.abs(v - 10) < 1);
  assert.ok(accuratePiv.length > result.pivVectors.length * 0.8);
});

test("camera motion and stationary bright objects alone do not produce a surface field", () => {
  const result = analyzeFrames(syntheticFrames({ moving: false }), options);
  assert.equal(result.vectors.length, 0);
  assert.equal(result.tracks.length, 0);
  assert.ok(result.diagnostics.pivStaticCells > 0);
  assert.equal(result.diagnostics.stabilizationFailures, 0);
});

test("failed bank stabilization withholds the affected frames", () => {
  const frames = syntheticFrames();
  const blankBank = frames.map((frame, index) => index ? { ...frame, gray: new Uint8Array(width * height) } : frame);
  const result = analyzeFrames(blankBank, options);
  assert.equal(result.vectors.length, 0);
  assert.equal(result.diagnostics.stabilizationFailures, frames.length - 1);
});

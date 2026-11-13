import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { test } from 'node:test';
import { fileURLToPath } from 'node:url';
import { readSurvey } from './geopackage.mjs';
import { cameraGeometry, parseDjiSrt } from './geometry.mjs';
import { analyzeSurvey, compareSurvey, summarizeSurvey } from './survey.mjs';

function close(actual, expected, tolerance = 1e-9) {
  assert.ok(Math.abs(actual - expected) < tolerance, `${actual} ≠ ${expected}`);
}

test('reads Isar point geometry and estimates one surface speed per usable position', () => {
  const directory = new URL('../examples/isar-20260804/', import.meta.url);
  const path = fileURLToPath(new URL('IsarAug2026.gpkg', directory));
  const survey = readSurvey(path);
  assert.equal(survey.epsg, 25832);
  assert.equal(survey.rows.length, 102);
  close(survey.rows[0].x, 677067.534);
  close(survey.rows[0].y, 5267942.413);
  const { verticals, summary, roughness, diagnostics } = analyzeSurvey(survey.rows);
  assert.equal(diagnostics.excludedRows, 6);
  assert.equal(diagnostics.verticalCount, 64);
  assert.equal(summary.count, 63);
  assert.equal(roughness.pairs, 29);
  close(roughness.estimate, 0.00982, 0.00001);
  close(summary.median, 1.238, 0.002);

  const subtitles = readFileSync(new URL('DJI_20260804150234_0002_D.SRT', directory), 'utf8');
  const telemetry = parseDjiSrt(subtitles, { startFrame: 1250, endFrame: 1650 });
  const geometry = cameraGeometry({ width: 1920, height: 1080, ...telemetry });
  const footprint = verticals.filter(point => {
    const [x, y] = geometry.toImage(point.x, point.y);
    return x >= 0 && x < 1920 && y >= 0 && y < 1080;
  });
  const visible = summarizeSurvey(footprint);
  assert.equal(visible.count, 9);
  close(visible.median, 1.243, 0.002);
});

test('converts measured depths below the surface and recovers an analytic log profile', () => {
  const totalDepth = 0.5;
  const z0 = 0.008;
  const surface = 1.25;
  const rows = [0.8, 0.2].map((fraction, index) => {
    const speed = surface * Math.log((1 - fraction) * totalDepth / z0) / Math.log(totalDepth / z0);
    return { id: index, name: 'A', x: 100 + index * 0.01, y: 100,
      measurementDepth: fraction * totalDepth, totalDepth, u: speed * 0.6, v: speed * 0.8 };
  });
  const { verticals, roughness } = analyzeSurvey(rows);
  assert.equal(verticals.length, 1);
  assert.equal(verticals[0].method, 'local-log-fit');
  close(verticals[0].speed, surface);
  close(verticals[0].u, surface * 0.6);
  close(roughness.estimate, z0);
});

test('limits matches to nearby measured vectors', () => {
  const points = [{ id: 1, x: 10, y: 10, speed: 1.2 }, { id: 2, x: 100, y: 100, speed: 1.5 }];
  const vectors = [
    { x: 10, y: 10, speed: 0.1, source: 'interpolated' },
    { x: 11, y: 10, speed: 1.4, source: 'ptv' },
    { x: 102, y: 100, speed: 0.2, source: 'ptv' },
  ];
  const match = compareSurvey(points, vectors);
  assert.equal(match.count, 1);
  close(match.rmse, 0.2);
  close(match.bias, 0.2);
  close(match.medianDistance, 1);
  assert.equal(compareSurvey(points, vectors, 0.5).rmse, null);
});

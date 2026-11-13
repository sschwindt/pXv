import test from 'node:test'
import assert from 'node:assert/strict'
import { assessVelocity } from './calibration.mjs'

const vector = (x, speed = 1, source = 'ptv', y = 0) => ({
  x, y, u: speed * 0.6, v: speed * 0.8, speed, source,
})

const reading = (x, speed = 2, y = 0) => ({ x, y, speed, sigma: 0.12, id: String(x) })

test('matches one reference per measured cell and leaves unmatched errors blank', () => {
  const vectors = [vector(0), vector(5, 1, 'interpolated'), vector(10)]
  const verticals = [reading(0.7), reading(0.1, 1.7), reading(5), reading(12)]
  const result = assessVelocity(vectors, verticals)
  assert.equal(result.status, 'validation')
  assert.equal(result.validation.count, 1)
  assert.equal(result.validation.total, 4)
  assert.equal(result.vectors[0].speed, 1)
  assert.equal(result.vectors[0].speedError, 0.7)
  assert.equal(result.vectors[0].speedResidual, -0.7)
  assert.equal(result.vectors[0].referenceSpeed, 1.7)
  assert.equal(result.vectors[0].referenceSigma, 0.12)
  assert.equal(result.vectors[0].errorBasis, 'validation')
  assert.equal(result.vectors[1].speedError, null)
  assert.equal(result.vectors[2].speedError, null)
  assert.equal(assessVelocity(vectors, verticals, { mode: 'none' }).vectors[0].speedError, null)
})

test('fits a per-method gain using training locations and tests only at a spatial holdout', () => {
  const vectors = Array.from({ length: 10 }, (_, index) => vector(index * 4))
  vectors.push(vector(40), vector(0, 1, 'piv', 20))
  const verticals = vectors.slice(0, 10).map(({ x }) => reading(x, x >= 28 ? 1.5 : 2))
  verticals.push(reading(0, 3, 20))
  const result = assessVelocity(vectors, verticals, { mode: 'calibration' })
  assert.equal(result.status, 'partial-calibration')
  assert.equal(result.bySource.ptv.gain, 2)
  assert.equal(result.bySource.piv.gain, null)
  assert.equal(result.bySource.ptv.trainingCount, 7)
  assert.equal(result.validation.count, 3)
  assert.equal(result.validation.rmse, 0.5)
  assert.equal(result.baseline.rmse, 0.5)
  assert.equal(result.comparison.count, 11)
  assert.equal(result.vectors[0].u, 1.2)
  assert.equal(result.vectors[0].v, 1.6)
  assert.equal(result.vectors[0].rawSpeed, 1)
  assert.equal(result.vectors[0].speed, 2)
  assert.equal(result.vectors[0].errorBasis, 'calibration')
  assert.equal(result.vectors[8].errorBasis, 'holdout')
  assert.equal(result.vectors[10].speed, 2)
  assert.equal(result.vectors[10].speedError, null)
  assert.equal(result.vectors[11].errorBasis, 'comparison')
  const newHoldout = verticals.map(point => point.x >= 28 ? { ...point, speed: 0 } : point)
  const changed = assessVelocity(vectors, newHoldout, { mode: 'calibration' })
  assert.equal(changed.bySource.ptv.gain, result.bySource.ptv.gain)
  assert.equal(changed.validation.rmse, 2)
  assert.deepEqual(vectors[0], vector(0))
})

test('withholds calibration when nearby matches cannot provide independent spatial support', () => {
  const vectors = Array.from({ length: 10 }, (_, index) => vector(index * 0.1))
  const verticals = vectors.map(({ x }) => reading(x))
  const result = assessVelocity(vectors, verticals, { mode: 'calibration' })
  assert.equal(result.comparison.count, 10)
  assert.equal(result.status, 'insufficient-calibration')
  assert.equal(result.applied, false)
  assert.equal(result.validation.count, 0)
  assert.equal(result.validation.rmse, null)
  assert.equal(result.vectors[0].errorBasis, 'comparison')
})

test('uses spatial coverage instead of the raw count to size a holdout', () => {
  const vectors = Array.from({ length: 9 }, (_, index) => vector(index * 4))
  for (let index = 1; index < 21; index++) vectors.push(vector(index * 0.1))
  const result = assessVelocity(vectors, vectors.map(({ x }) => reading(x)), { mode: 'calibration' })
  assert.equal(result.calibration.count, 6)
  assert.equal(result.validation.count, 3)
  assert.equal(result.bySource.ptv.gain, 2)
})

test('shared profile estimates remain local comparisons and cannot enter a split', () => {
  const vectors = Array.from({ length: 8 }, (_, index) => vector(index * 4))
  const verticals = vectors.map(({ x }) => ({ ...reading(x), method: 'site-log-fit' }))
  const validation = assessVelocity(vectors, verticals)
  const calibration = assessVelocity(vectors, verticals, { mode: 'calibration' })
  assert.equal(validation.validation.count, 8)
  assert.equal(calibration.comparison.count, 8)
  assert.equal(calibration.calibration.sharedProfileCount, 8)
  assert.equal(calibration.applied, false)
})

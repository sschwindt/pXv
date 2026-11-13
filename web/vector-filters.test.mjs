import assert from 'node:assert/strict'
import test from 'node:test'
import { filterVectors } from './vector-filters.mjs'

function vector(x, y, { u = 1, v = 0, pixelU = 20, pixelV = 0, source = 'piv', trackCount } = {}) {
  return Object.freeze({ x, y, u, v, speed: Math.hypot(u, v), source, count: 20, trackCount,
    pixel: Object.freeze({ x, y, u: pixelU, v: pixelV }) })
}

test('speed cap uses total projected magnitude, includes equality and bypasses pixel units', () => {
  const boundary = vector(2, 2, { u: 1.8, v: 2.4 })
  const tooFast = vector(1, 1, { u: 2.2, v: 2.2, source: 'ptv', trackCount: 1 })
  const vectors = Object.freeze([boundary, tooFast])
  const result = filterVectors(vectors, { units: 'm', maxSpeed: 3, ptvMinTracks: 2 })
  assert.deepEqual(result.accepted, [boundary])
  assert.deepEqual(result.rejected, [{ ...tooFast, reason: 'speed' }])
  assert.equal(result.counts.speed, 1)
  assert.equal(result.counts.ptvSupport, 0)
  assert.deepEqual(filterVectors(vectors, { maxSpeed: 3 }).accepted, vectors)
})

test('normalized median detects a reversed PIV vector at the same speed and keeps one-sided edges', () => {
  const vectors = [12, 36, 60].flatMap(y => [12, 36, 60].map(x => vector(x, y)))
  const center = vector(36, 36, { u: -1, pixelU: -20 })
  vectors[4] = center
  const options = { cellSize: 12, pivGridStep: 24, frameInterval: 0.05, pivThreshold: 2 }
  const result = filterVectors(vectors, options)
  assert.equal(result.counts.pivChecked, 1)
  assert.equal(result.counts.pivSparse, 8)
  assert.deepEqual(result.rejected, [{ ...center, reason: 'piv-median', score: 20 }])
  assert.equal(filterVectors(vectors, { ...options, pivThreshold: 20 }).accepted.length, 9)
})

test('PIV validation skips neighborhoods split by masked areas or occupied by a different source', () => {
  const neighbors = [12, 36, 60].flatMap(y => [12, 36, 60].map(x => vector(x, y)))
  neighbors[4] = vector(36, 36, { pixelU: -20 })
  const options = { cellSize: 24, frameInterval: 0.05, pivThreshold: 2 }
  const exclude = [[[45, 0], [46, 0], [46, 72], [45, 72]]]
  const acrossMask = filterVectors(neighbors, { ...options, exclude })
  assert.equal(acrossMask.counts.pivChecked, 0)
  assert.equal(acrossMask.counts.pivSparse, 9)
  const mixed = neighbors.map(item => item.x === 60 ? { ...item, source: 'ptv' } : item)
  assert.equal(filterVectors(mixed, options).counts.pivChecked, 0)
})

test('tracer support counts distinct trajectories, preserves unknown support and skips PIV', () => {
  const vectors = [vector(1, 1, { source: 'ptv', trackCount: 1 }),
    vector(2, 2, { source: 'ptv', trackCount: 2 }), vector(3, 3, { source: 'ptv' }), vector(4, 4)]
  const result = filterVectors(vectors, { ptvMinTracks: 2 })
  assert.deepEqual(result.accepted, vectors.slice(1))
  assert.deepEqual(result.rejected, [{ ...vectors[0], reason: 'ptv-support' }])
  assert.equal(result.counts.ptvSupport, 1)
  assert.equal(result.counts.ptvUnknown, 1)
})

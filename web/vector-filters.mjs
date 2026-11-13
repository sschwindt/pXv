import { inRegion } from './velocimetry.mjs'

const displacementFloor = 0.1

function median(values) {
  values.sort((a, b) => a - b)
  const middle = Math.floor(values.length / 2)
  return values.length % 2 ? values[middle] : (values[middle - 1] + values[middle]) / 2
}

function componentResidual(center, values, frameInterval) {
  const middle = median(values)
  const dispersion = median(values.map(value => Math.abs(value - middle)))
  return Math.abs(center - middle) / (dispersion + displacementFloor / frameInterval)
}

function cross(ax, ay, bx, by) {
  return ax * by - ay * bx
}

function sameWaterRegion(a, b, include, exclude) {
  if (!include.length && !exclude.length) return true
  const dx = b.x - a.x
  const dy = b.y - a.y
  const breaks = [0, 1]
  // The mask is constant between polygon boundary crossings. Test the open
  // intervals, so a neighbor can lie across an overlap of water polygons.
  for (const polygon of [...include, ...exclude]) {
    for (let i = 0, j = polygon.length - 1; i < polygon.length; j = i++) {
      const [x0, y0] = polygon[j]
      const [x1, y1] = polygon[i]
      const ex = x1 - x0
      const ey = y1 - y0
      const determinant = cross(dx, dy, ex, ey)
      if (determinant === 0) continue
      const t = cross(x0 - a.x, y0 - a.y, ex, ey) / determinant
      const s = cross(x0 - a.x, y0 - a.y, dx, dy) / determinant
      if (t > 0 && t < 1 && s >= 0 && s <= 1) breaks.push(t)
    }
  }
  breaks.sort((a, b) => a - b)
  return breaks.slice(1).every((end, i) => {
    if (end === breaks[i]) return true
    const midpoint = (end + breaks[i]) / 2
    return inRegion(a.x + dx * midpoint, a.y + dy * midpoint, include, exclude)
  })
}

function pivResiduals(vectors, { spacing, frameInterval, include, exclude }) {
  const cells = new Map()
  const piv = []
  const key = (column, row) => column + ':' + row
  for (const vector of vectors) {
    if (vector.source !== 'piv') continue
    const { pixel } = vector
    if (!pixel || ![pixel.x, pixel.y, pixel.u, pixel.v].every(Number.isFinite)) continue
    const column = Math.floor(pixel.x / spacing)
    const row = Math.floor(pixel.y / spacing)
    const entry = { vector, pixel, column, row }
    const cellKey = key(column, row)
    if (!cells.has(cellKey)) cells.set(cellKey, [])
    cells.get(cellKey).push(entry)
    piv.push(entry)
  }

  const residuals = new Map()
  for (const { vector, pixel, column, row } of piv) {
    if (cells.get(key(column, row)).length !== 1) continue
    const neighbors = []
    let left = false
    let right = false
    let above = false
    let below = false
    for (let dy = -1; dy <= 1; dy++) {
      for (let dx = -1; dx <= 1; dx++) {
        if (dx === 0 && dy === 0) continue
        const candidates = cells.get(key(column + dx, row + dy))
        if (candidates?.length !== 1) continue
        const neighbor = candidates[0].pixel
        if (!sameWaterRegion(pixel, neighbor, include, exclude)) continue
        neighbors.push(neighbor)
        left ||= dx < 0
        right ||= dx > 0
        above ||= dy < 0
        below ||= dy > 0
      }
    }
    // One-sided support can bias the median near a bank or a data gap.
    if (neighbors.length < 4 || !left || !right || !above || !below) continue
    const u = componentResidual(pixel.u, neighbors.map(neighbor => neighbor.u), frameInterval)
    const v = componentResidual(pixel.v, neighbors.map(neighbor => neighbor.v), frameInterval)
    residuals.set(vector, Math.max(u, v))
  }
  return residuals
}

/**
 * Screen projected output vectors without modifying the engine result.
 * PIV residuals compare image displacements using a 0.1 px floor, following
 * Westerweel & Scarano (2005), doi:10.1007/s00348-005-0016-6.
 */
export function filterVectors(vectors, {
  units = 'px', maxSpeed = null, cellSize = 32, pivGridStep = cellSize,
  frameInterval = null, pivThreshold = null, ptvMinTracks = null,
  include = [], exclude = [],
} = {}) {
  const counts = {
    total: vectors.length, accepted: 0, rejected: 0, speed: 0,
    pivMedian: 0, ptvSupport: 0, pivChecked: 0, pivSparse: 0, ptvUnknown: 0,
  }
  const rejectedByVector = new Map()
  const useSpeed = units === 'm' && Number.isFinite(maxSpeed) && maxSpeed > 0
  const useTracks = Number.isInteger(ptvMinTracks) && ptvMinTracks > 0
  for (const vector of vectors) {
    if (useSpeed && vector.speed > maxSpeed) {
      rejectedByVector.set(vector, { ...vector, reason: 'speed' })
      counts.speed++
    } else if (useTracks && vector.source === 'ptv') {
      if (!Number.isInteger(vector.trackCount)) {
        counts.ptvUnknown++
      } else if (vector.trackCount < ptvMinTracks) {
        rejectedByVector.set(vector, { ...vector, reason: 'ptv-support' })
        counts.ptvSupport++
      }
    }
  }

  const spacing = Math.max(cellSize, pivGridStep)
  if (Number.isFinite(pivThreshold) && pivThreshold > 0) {
    const eligible = vectors.filter(vector => vector.source === 'piv' && !rejectedByVector.has(vector))
    const residuals = frameInterval > 0 && Number.isFinite(frameInterval) && spacing > 0 && Number.isFinite(spacing)
      ? pivResiduals(eligible, { spacing, frameInterval, include, exclude })
      : new Map()
    counts.pivChecked = residuals.size
    counts.pivSparse = eligible.length - residuals.size
    for (const [vector, score] of residuals) {
      if (score <= pivThreshold) continue
      rejectedByVector.set(vector, { ...vector, reason: 'piv-median', score })
      counts.pivMedian++
    }
  }

  const accepted = vectors.filter(vector => !rejectedByVector.has(vector))
  const rejected = vectors.filter(vector => rejectedByVector.has(vector)).map(vector => rejectedByVector.get(vector))
  counts.accepted = accepted.length
  counts.rejected = rejected.length
  return { accepted, rejected, counts }
}

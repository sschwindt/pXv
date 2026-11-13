const minimumFitMatches = 5
const minimumHoldoutMatches = 3
const minimumFitSpeed = 0.05 // Ratios near rest are poorly conditioned.

function median(values) {
  if (!values.length) return null
  const sorted = values.slice().sort((a, b) => a - b)
  const middle = Math.floor(sorted.length / 2)
  return sorted.length % 2 ? sorted[middle] : (sorted[middle - 1] + sorted[middle]) / 2
}

const coordinates = (a, b) => a.x - b.x || a.y - b.y
const pointKey = ({ x, y }) => x + ':' + y
const distance = (a, b) => Math.hypot(a.x - b.x, a.y - b.y)
const isMeasured = vector => !['interpolated', 'rejected'].includes(vector.source)

function getSpeed(vector) {
  const speed = vector.speed ?? ([vector.u, vector.v].every(Number.isFinite)
    ? Math.hypot(vector.u, vector.v) : null)
  return Number.isFinite(speed) && speed >= 0 ? speed : null
}

function findMatches(vectors, verticals, radius) {
  const grid = new Map()
  const bin = value => Math.floor(value / radius)
  const key = (column, row) => column + ':' + row
  vectors.forEach((vector, index) => {
    if (!isMeasured(vector) || ![vector.x, vector.y].every(Number.isFinite)) return
    const speed = getSpeed(vector)
    if (speed === null) return
    const cell = key(bin(vector.x), bin(vector.y))
    if (!grid.has(cell)) grid.set(cell, [])
    grid.get(cell).push({ vector, index, speed })
  })

  const proposals = []
  for (const point of verticals) {
    const col = bin(point.x)
    const row = bin(point.y)
    let nearest
    for (let y = row - 1; y <= row + 1; y++) {
      for (let x = col - 1; x <= col + 1; x++) {
        for (const candidate of grid.get(key(x, y)) ?? []) {
          const gap = distance(point, candidate.vector)
          if (gap > radius) continue
          const closer = !nearest || gap < nearest.distance ||
            (gap === nearest.distance && coordinates(candidate.vector, nearest.vector) < 0)
          if (closer) nearest = { ...candidate, distance: gap, point }
        }
      }
    }
    if (nearest) proposals.push(nearest)
  }

  // A cell represents one image observation. If several survey points select
  // it, only the closest pair is used. Assigning the others to a more distant
  // vector would overstate the amount of collocated evidence.
  proposals.sort((a, b) => a.distance - b.distance || coordinates(a.point, b.point))
  const cells = new Set()
  const references = new Set()
  return proposals.filter(({ vector, point }) => {
    const cell = pointKey(vector)
    const reference = pointKey(point)
    if (cells.has(cell) || references.has(reference)) return false
    cells.add(cell)
    references.add(reference)
    return true
  })
}

function fitSource(matches, { separation, holdoutFraction }) {
  // These speeds share a roughness fit based on all survey readings. Using
  // them on either side would couple the calibration and holdout responses.
  matches = matches.filter(({ point }) => point.method !== 'site-log-fit')
  if (matches.length < minimumFitMatches + minimumHoldoutMatches) return null
  const apart = (a, b) => distance(a.point, b.point) >= separation &&
    distance(a.vector, b.vector) >= separation
  const spread = axis => {
    const values = matches.map(({ point }) => point[axis])
    return values.reduce((maximum, value) => Math.max(maximum, value), -Infinity) -
      values.reduce((minimum, value) => Math.min(minimum, value), Infinity)
  }
  const axis = spread('x') >= spread('y') ? 'x' : 'y'
  const ordered = matches.slice().sort((a, b) => b.point[axis] - a.point[axis] ||
    coordinates(a.point, b.point))
  // Thin close neighbours before assigning one spatial end of the scene to
  // validation. Otherwise a densely sampled patch could set the holdout size
  // for the whole scene. Neither operation uses speed.
  const spatial = []
  for (const pair of ordered) {
    if (spatial.every(other => apart(pair, other))) spatial.push(pair)
  }
  if (spatial.length < minimumFitMatches + minimumHoldoutMatches) return null
  const holdoutTarget = Math.min(spatial.length - minimumFitMatches,
    Math.max(minimumHoldoutMatches, Math.ceil(holdoutFraction * spatial.length)))
  const heldOut = spatial.slice(0, holdoutTarget)
  const training = spatial.slice(holdoutTarget).filter(pair =>
    pair.point.speed >= minimumFitSpeed && pair.speed >= minimumFitSpeed)
  if (training.length < minimumFitMatches) return null
  const gain = median(training.map(pair => pair.point.speed / pair.speed))
  if (!Number.isFinite(gain) || gain <= 0) return null
  return { gain, training: new Set(training), heldOut: new Set(heldOut) }
}

function summarize(matches, total, radius, baseline = false) {
  const modelled = match => baseline ? match.rawSpeed : match.modelled
  const errors = matches.map(match => modelled(match) - match.observed)
  const mean = values => values.length ? values.reduce((sum, value) => sum + value, 0) / values.length : null
  return {
    count: matches.length, total, radius, matches,
    rmse: errors.length ? Math.sqrt(mean(errors.map(error => error ** 2))) : null,
    bias: mean(errors), mae: mean(errors.map(Math.abs)),
    observedMedian: median(matches.map(match => match.observed)),
    modelledMedian: median(matches.map(modelled)),
    medianDistance: median(matches.map(match => match.distance)),
  }
}

/**
 * Assess speeds and optionally fit a scalar gain separately for each engine.
 * Coordinates must use a common projected metric CRS. Survey speeds must
 * represent the water surface. The result contains only collocated speed
 * residuals, which combine video, reference, and representativeness errors.
 * A spatial holdout is reserved independently of the measured velocities.
 */
export function assessVelocity(vectors, verticals, {
  mode = 'validation', radius = 1.5, separation = 2 * radius, holdoutFraction = 0.3,
} = {}) {
  if (!['none', 'validation', 'calibration'].includes(mode)) throw new Error('Choose a survey use mode.')
  if (!Number.isFinite(radius) || radius <= 0) throw new Error('Match radius must be positive.')
  if (!Number.isFinite(separation) || separation < radius) {
    throw new Error('Spatial separation must be at least the match radius.')
  }
  if (!(holdoutFraction > 0 && holdoutFraction < 1)) throw new Error('Holdout fraction must be between 0 and 1.')

  const estimated = verticals.filter(point => [point.x, point.y, point.speed].every(Number.isFinite) && point.speed >= 0)
  const pairs = mode === 'none' ? [] : findMatches(vectors, estimated, radius)
  const sources = [...new Set(vectors.filter(isMeasured).map(vector => vector.source ?? 'video'))]
  const pairsBySource = new Map(sources.map(source => [source, []]))
  for (const pair of pairs) pairsBySource.get(pair.vector.source ?? 'video').push(pair)
  const fits = new Map()
  if (mode === 'calibration') {
    for (const [source, candidates] of pairsBySource) {
      const fit = fitSource(candidates, { separation, holdoutFraction })
      if (fit) fits.set(source, fit)
    }
  }

  const matches = pairs.map(pair => {
    const { point, vector, distance: gap, speed: rawSpeed, index } = pair
    const source = vector.source ?? 'video'
    const fit = fits.get(source)
    const gain = fit?.gain ?? 1
    const role = fit?.training.has(pair) ? 'calibration'
      : fit?.heldOut.has(pair) ? 'holdout' : mode === 'validation' ? 'validation' : 'comparison'
    return {
      id: point.id ?? null, name: point.name ?? null, x: point.x, y: point.y, vectorX: vector.x, vectorY: vector.y,
      index, source, role, gain, rawSpeed, distance: gap,
      observed: point.speed, modelled: rawSpeed * gain,
      sigma: Number.isFinite(point.sigma) && point.sigma >= 0 ? point.sigma : null,
    }
  })
  const comparison = summarize(matches, estimated.length, radius)
  const matched = new Map(matches.map(match => [match.index, match]))
  const validationPairs = mode === 'calibration' ? matches.filter(match => match.role === 'holdout') : matches
  const validation = summarize(validationPairs, estimated.length, radius)
  const baseline = summarize(validationPairs, estimated.length, radius, true)
  const trainingPairs = matches.filter(match => match.role === 'calibration')
  const bySource = Object.fromEntries(sources.map(source => {
    const subset = matches.filter(match => match.source === source)
    const validationSubset = validationPairs.filter(match => match.source === source)
    return [source, {
      gain: fits.get(source)?.gain ?? null,
      matches: subset.length,
      trainingCount: subset.filter(match => match.role === 'calibration').length,
      validation: summarize(validationSubset, estimated.length, radius),
      baseline: summarize(validationSubset, estimated.length, radius, true),
    }]
  }))
  const calibrated = vectors.map((vector, index) => {
    const rawSpeed = getSpeed(vector)
    const gain = isMeasured(vector) ? fits.get(vector.source ?? 'video')?.gain ?? 1 : 1
    const match = matched.get(index)
    const speed = rawSpeed === null ? null : rawSpeed * gain
    const speedResidual = match ? speed - match.observed : null
    return {
      ...vector, gain, rawSpeed, speed,
      u: Number.isFinite(vector.u) ? vector.u * gain : vector.u,
      v: Number.isFinite(vector.v) ? vector.v * gain : vector.v,
      speedResidual, speedError: speedResidual === null ? null : Math.abs(speedResidual),
      errorBasis: match?.role ?? null, referenceSpeed: match?.observed ?? null,
      referenceSigma: match?.sigma ?? null, referenceId: match?.id ?? null,
      matchDistance: match?.distance ?? null,
    }
  })

  let status = mode
  if (mode !== 'none' && !estimated.length) status = 'no-reference'
  else if (mode !== 'none' && !matches.length) status = 'no-overlap'
  else if (mode === 'calibration') {
    status = !fits.size ? 'insufficient-calibration'
      : fits.size < sources.length ? 'partial-calibration' : 'calibrated'
  }
  const calibration = {
    ...summarize(trainingPairs, estimated.length, radius),
    minimumFitMatches, minimumHoldoutMatches, minimumFitSpeed,
    sharedProfileCount: pairs.filter(({ point }) => point.method === 'site-log-fit').length,
  }
  return {
    vectors: calibrated, matches, comparison, validation, baseline, calibration, status,
    applied: fits.size > 0, bySource, radius, separation,
  }
}

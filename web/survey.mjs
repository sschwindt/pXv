const defaults = {
  tolerance: 0.10,
  relativeSigma: 0.05,
  sigmaFloor: 0.0025,
  fallbackRoughness: 0.02,
  minSubmergence: 5,
};

function quantile(sorted, fraction) {
  if (!sorted.length) return null;
  const index = (sorted.length - 1) * fraction;
  const lo = Math.floor(index);
  return sorted[lo] + (sorted[Math.ceil(index)] - sorted[lo]) * (index - lo);
}

function mean(values) {
  return values.length ? values.reduce((sum, value) => sum + value, 0) / values.length : null;
}

export function summarizeSurvey(verticals) {
  const speeds = verticals.map(point => point.speed).filter(Number.isFinite).sort((a, b) => a - b);
  const sigmas = verticals.map(point => point.sigma).filter(Number.isFinite).sort((a, b) => a - b);
  return {
    count: speeds.length,
    median: quantile(speeds, 0.5),
    mean: mean(speeds),
    p10: quantile(speeds, 0.1),
    p90: quantile(speeds, 0.9),
    min: speeds[0] ?? null,
    max: speeds.at(-1) ?? null,
    medianSigma: quantile(sigmas, 0.5),
  };
}

function groupVerticals(rows, { tolerance, relativeSigma, sigmaFloor }) {
  const groups = [];
  const bins = new Map();
  const spacing = tolerance > 0 ? tolerance : 1;
  const key = (column, row) => column + ':' + row;
  let excludedRows = 0;
  for (const row of rows) {
    const { x, y, u, v, measurementDepth, totalDepth } = row;
    const speed = row.speed ?? ([u, v].every(Number.isFinite) ? Math.hypot(u, v) : null);
    const surface = row.surface === true;
    if (![x, y, speed].every(Number.isFinite) || speed < 0 || (!surface &&
        (![measurementDepth, totalDepth].every(Number.isFinite) || measurementDepth < 0 || measurementDepth >= totalDepth))) {
      excludedRows++;
      continue;
    }
    const reading = {
      ...row, speed, heightFraction: surface ? 1 : 1 - measurementDepth / totalDepth,
      sigma: Number.isFinite(row.sigma) && row.sigma >= 0 ? row.sigma : Math.max(sigmaFloor, relativeSigma * speed),
    };
    const column = Math.floor(x / spacing);
    const line = Math.floor(y / spacing);
    let first;
    for (let iy = line - 1; iy <= line + 1; iy++) {
      for (let ix = column - 1; ix <= column + 1; ix++) {
        for (const index of bins.get(key(ix, iy)) ?? []) {
          const group = groups[index];
          if ((first === undefined || index < first) && Math.hypot(group.x - x, group.y - y) <= tolerance) first = index;
        }
      }
    }
    if (first !== undefined) groups[first].readings.push(reading);
    else {
      const id = key(column, line);
      if (!bins.has(id)) bins.set(id, []);
      bins.get(id).push(groups.length);
      groups.push({ id: row.id, name: row.name, x, y, sourceX: row.sourceX, sourceY: row.sourceY, readings: [reading] });
    }
  }
  for (const group of groups) {
    group.readings.sort((a, b) => b.heightFraction - a.heightFraction);
  }
  return { groups, excludedRows };
}

function logFit(lower, upper) {
  const logSpan = Math.log(upper.heightFraction / lower.heightFraction);
  if (logSpan <= 0 || upper.speed <= lower.speed) return null;
  const slope = (upper.speed - lower.speed) / logSpan;
  return {
    roughness: lower.heightFraction * lower.totalDepth * Math.exp(-lower.speed / slope),
    weight: -Math.log(upper.heightFraction) / logSpan,
    slope,
  };
}

function estimateRoughness(groups, fallback) {
  const samples = [];
  for (const { readings } of groups) {
    if (readings.length < 2) continue;
    const upper = readings[0];
    const lower = readings.at(-1);
    if (upper.heightFraction - lower.heightFraction <= 0.001 || !Number.isFinite(upper.totalDepth)) continue;
    const fit = logFit(lower, upper);
    if (fit?.roughness > 0 && fit.roughness < lower.totalDepth) samples.push(fit.roughness);
  }
  samples.sort((a, b) => a - b);
  return {
    estimate: quantile(samples, 0.5) ?? fallback,
    p10: quantile(samples, 0.1),
    p90: quantile(samples, 0.9),
    pairs: samples.length,
    usedFallback: !samples.length,
  };
}

function siteLaw(reading, roughness) {
  const relativeSubmergence = reading.totalDepth / roughness;
  const ratio = 1 + Math.log(reading.heightFraction) / Math.log(relativeSubmergence);
  if (relativeSubmergence <= 1 || !(ratio > 0)) return null;
  return { speed: reading.speed / ratio, sigma: reading.sigma / ratio };
}

function compatibleFlow(lower, upper) {
  if (!lower.speed || ![lower.u, lower.v, upper.u, upper.v].every(Number.isFinite)) return true;
  const cosine = (upper.u * lower.u + upper.v * lower.v) / (upper.speed * lower.speed);
  return cosine >= Math.SQRT1_2;
}

function extrapolate(group, roughness, minSubmergence) {
  const [upper] = group.readings;
  const totalDepth = upper.totalDepth;
  const flags = [];
  if (upper.heightFraction === 1) {
    return { speed: upper.speed, sigma: upper.sigma, method: 'surface-reading', flags };
  }
  if (totalDepth / roughness.estimate < minSubmergence) flags.push('low-submergence');

  // Start at the widest pair. Require that the readings roughly agree in
  // direction, the lower reading is above the roughness region, and the
  // extrapolation to the surface is no longer than the measured log span.
  const fitOptions = group.readings.slice(1).reverse();
  for (const lower of fitOptions) {
    const fit = logFit(lower, upper);
    if (!fit || fit.weight > 1 || lower.heightFraction * totalDepth < minSubmergence * fit.roughness ||
        !compatibleFlow(lower, upper)) continue;
    const speed = upper.speed + fit.weight * (upper.speed - lower.speed);
    const residuals = fitOptions.filter(reading => reading !== lower &&
      reading.heightFraction > lower.heightFraction).map(reading => {
      const predicted = upper.speed + fit.slope * Math.log(reading.heightFraction / upper.heightFraction);
      return reading.speed - predicted;
    });
    const mismatch = Math.sqrt(mean(residuals.map(value => value ** 2)) ?? 0);
    if (mismatch > 2 * upper.sigma) flags.push('profile-mismatch');
    const sigma = Math.hypot((1 + fit.weight) * upper.sigma, fit.weight * lower.sigma, mismatch);
    return { speed, sigma, method: 'local-log-fit', flags };
  }

  if (fitOptions.length) flags.push('local-fit-unavailable');
  const estimate = siteLaw(upper, roughness.estimate);
  if (!estimate) {
    return { speed: null, sigma: null, method: 'unavailable', flags: [...flags, 'roughness-limit'] };
  }
  if (roughness.usedFallback) flags.push('assumed-roughness');
  // The paired roughness spread measures sensitivity across the site. It
  // contributes to a working per-vertical uncertainty, not a confidence interval.
  const low = siteLaw(upper, roughness.p10 ?? roughness.estimate / 2);
  const high = siteLaw(upper, roughness.p90 ?? roughness.estimate * 2);
  if (!high) flags.push('roughness-sensitive');
  const sensitivity = high && low ? (high.speed - low.speed) / 2 : null;
  return {
    speed: estimate.speed,
    sigma: sensitivity === null ? null : Math.hypot(estimate.sigma, sensitivity),
    method: 'site-log-fit',
    flags,
  };
}

/** Infer surface speeds from the measured height above the bed at each vertical. */
export function analyzeSurvey(rows, options = {}) {
  const settings = { ...defaults, ...options };
  const { groups, excludedRows } = groupVerticals(rows, settings);
  const roughness = estimateRoughness(groups, settings.fallbackRoughness);
  const verticals = groups.map(group => {
    const [upper] = group.readings;
    const estimate = extrapolate(group, roughness, settings.minSubmergence);
    const factor = upper.speed > 0 ? estimate.speed / upper.speed : 0;
    return {
      ...group, ...estimate, totalDepth: upper.totalDepth,
      u: estimate.speed === null || !Number.isFinite(upper.u) ? null : upper.u * factor,
      v: estimate.speed === null || !Number.isFinite(upper.v) ? null : upper.v * factor,
    };
  });
  const diagnostics = {
    rows: rows.length,
    excludedRows,
    verticalCount: groups.length,
    localFits: verticals.filter(point => point.method === 'local-log-fit').length,
    siteFits: verticals.filter(point => point.method === 'site-log-fit').length,
    unavailable: verticals.filter(point => point.speed === null).length,
    medianSiteLaw: summarizeSurvey(groups.map(({ readings: [upper] }) => siteLaw(upper, roughness.estimate) ?? {})).median,
  };
  return { verticals, summary: summarizeSurvey(verticals), roughness, diagnostics };
}

/** Compare nearest measured cells within a radius in projected metres. */
export function compareSurvey(verticals, vectors, radius = 1.5) {
  if (!Number.isFinite(radius) || radius <= 0) throw new Error('Match radius must be positive.');
  const measured = vectors.filter(vector => vector.source !== 'interpolated' && vector.source !== 'rejected')
    .map(vector => {
      const speed = vector.speed ?? ([vector.u, vector.v].every(Number.isFinite)
        ? Math.hypot(vector.u, vector.v) : null);
      return { ...vector, speed };
    })
    .filter(vector => [vector.x, vector.y, vector.speed].every(Number.isFinite));
  const matches = [];
  const estimated = verticals.filter(point => Number.isFinite(point.speed));
  for (const point of estimated) {
    let distance = Infinity;
    let closest;
    for (const vector of measured) {
      const separation = Math.hypot(point.x - vector.x, point.y - vector.y);
      if (separation <= radius && separation < distance) {
        closest = vector;
        distance = separation;
      }
    }
    if (closest) {
      matches.push({
        id: point.id, name: point.name, x: point.x, y: point.y, distance,
        observed: point.speed, modelled: closest.speed, source: closest.source,
      });
    }
  }
  const median = values => quantile(values.sort((a, b) => a - b), 0.5);
  const errors = matches.map(match => match.modelled - match.observed);
  return {
    count: matches.length,
    total: estimated.length,
    radius,
    rmse: errors.length ? Math.sqrt(mean(errors.map(error => error ** 2))) : null,
    bias: mean(errors),
    mae: mean(errors.map(Math.abs)),
    observedMedian: median(matches.map(match => match.observed)),
    modelledMedian: median(matches.map(match => match.modelled)),
    medianDistance: median(matches.map(match => match.distance)),
    matches,
  };
}

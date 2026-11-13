const defaults = {
  method: "ptv",
  include: [],
  exclude: [],
  staticPolygons: [],
  threshold: 0.86,
  minArea: 20,
  maxArea: 400,
  maxDisplacement: 12,
  minTrackFrames: 4,
  maxGapFrames: 1,
  cellSize: 32,
  minSamples: 1,
  windowSize: 48,
  overlap: 0.5,
  pivPairStride: 1,
  pivSearchRadius: 8,
  correlationThreshold: 0.55,
  maxZeroLagRatio: 0.95,
  staticThreshold: 0.5,
  stabilizationRadius: 8,
};

const identity = { a: 1, b: 0, tx: 0, ty: 0 };

function settings(options) {
  const cfg = { ...defaults, ...options };
  if (!["ptv", "piv", "combined"].includes(cfg.method)) {
    throw new Error("Choose PTV, PIV, or combined analysis.");
  }
  for (const key of ["threshold", "overlap", "correlationThreshold", "maxZeroLagRatio"]) {
    if (!Number.isFinite(cfg[key]) || cfg[key] < 0 || cfg[key] > 1) {
      throw new Error(`${key} must be between 0 and 1.`);
    }
  }
  for (const key of ["minArea", "maxArea", "minTrackFrames", "cellSize", "minSamples",
    "windowSize", "pivPairStride", "pivSearchRadius", "stabilizationRadius"]) {
    if (!Number.isInteger(cfg[key]) || cfg[key] < 1) {
      throw new Error(`${key} must be a positive integer.`);
    }
  }
  if (cfg.minArea > cfg.maxArea || cfg.minTrackFrames < 2 || cfg.windowSize < 8 || cfg.overlap >= 1) {
    throw new Error("Check the tracer area, track length, PIV window size, and overlap.");
  }
  if (cfg.pivSearchRadius > 64 || cfg.stabilizationRadius > 64) {
    throw new Error("Search radii cannot exceed 64 pixels.");
  }
  if (!Number.isFinite(cfg.maxDisplacement) || cfg.maxDisplacement <= 0 ||
      !Number.isFinite(cfg.staticThreshold) || cfg.staticThreshold < 0 ||
      !Number.isInteger(cfg.maxGapFrames) || cfg.maxGapFrames < 0) {
    throw new Error("Check the displacement bounds and maximum track gap.");
  }
  return cfg;
}

function median(values) {
  if (!values.length) return null;
  const sorted = values.slice().sort((a, b) => a - b);
  const mid = Math.floor(sorted.length / 2);
  return sorted.length % 2 ? sorted[mid] : (sorted[mid - 1] + sorted[mid]) / 2;
}

function insidePolygon(x, y, polygon) {
  let inside = false;
  for (let i = 0, j = polygon.length - 1; i < polygon.length; j = i++) {
    const [ax, ay] = polygon[i];
    const [bx, by] = polygon[j];
    if ((ay > y) !== (by > y) && x < ax + (bx - ax) * (y - ay) / (by - ay)) inside = !inside;
  }
  return inside;
}

export function inRegion(x, y, include, exclude) {
  return (!include.length || include.some(polygon => insidePolygon(x, y, polygon))) &&
    !exclude.some(polygon => insidePolygon(x, y, polygon));
}

function regionMask(width, height, { include, exclude }) {
  if (!include.length && !exclude.length) return null;
  const mask = new Uint8Array(width * height);
  for (let y = 0; y < height; y++) {
    for (let x = 0; x < width; x++) mask[y * width + x] = inRegion(x, y, include, exclude);
  }
  return mask;
}

function makeDetector(width, height, cfg, mask) {
  const visited = new Uint8Array(width * height);
  const queue = new Int32Array(width * height);
  const cutoff = cfg.threshold * 255;
  return gray => {
    const detections = [];
    visited.fill(0);
    for (let start = 0; start < gray.length; start++) {
      if (gray[start] <= cutoff || visited[start] || (mask && !mask[start])) continue;
      let length = 1;
      let sumX = 0;
      let sumY = 0;
      queue[0] = start;
      visited[start] = 1;
      for (let head = 0; head < length; head++) {
        const index = queue[head];
        const x = index % width;
        const y = (index - x) / width;
        sumX += x;
        sumY += y;
        for (let iy = Math.max(0, y - 1); iy <= Math.min(height - 1, y + 1); iy++) {
          for (let ix = Math.max(0, x - 1); ix <= Math.min(width - 1, x + 1); ix++) {
            const next = iy * width + ix;
            if (gray[next] <= cutoff || visited[next] || (mask && !mask[next])) continue;
            visited[next] = 1;
            queue[length++] = next;
          }
        }
      }
      if (length >= cfg.minArea && length <= cfg.maxArea) {
        detections.push({ x: sumX / length, y: sumY / length, area: length });
      }
    }
    return detections;
  };
}

export function detectTracers(gray, width, height, options = {}) {
  const cfg = settings(options);
  if (gray.length !== width * height) throw new Error("Grayscale buffer does not match frame dimensions.");
  return makeDetector(width, height, cfg, regionMask(width, height, cfg))(gray);
}

function toFrame(x, y, pose) {
  return { x: pose.a * x - pose.b * y + pose.tx, y: pose.b * x + pose.a * y + pose.ty };
}

function toReference(x, y, pose) {
  const dx = x - pose.tx;
  const dy = y - pose.ty;
  const scale = pose.a * pose.a + pose.b * pose.b;
  return { x: (pose.a * dx + pose.b * dy) / scale, y: (pose.a * dy - pose.b * dx) / scale };
}

function preparePatch(frame, x, y, radius, stride) {
  const count = (Math.floor(2 * radius / stride) + 1) ** 2;
  const values = new Float64Array(count);
  const offsets = new Int32Array(count);
  let sum = 0;
  let index = 0;
  for (let dy = -radius; dy <= radius; dy += stride) {
    for (let dx = -radius; dx <= radius; dx += stride) {
      const offset = dy * frame.width + dx;
      const value = frame.gray[y * frame.width + x + offset];
      offsets[index] = offset;
      values[index++] = value;
      sum += value;
    }
  }
  const mean = sum / count;
  let energy = 0;
  for (let i = 0; i < count; i++) {
    values[i] -= mean;
    energy += values[i] * values[i];
  }
  return { values, offsets, energy, texture: Math.sqrt(energy / count) };
}

function normalizedCorrelation(patch, frame, center) {
  const { values, offsets, energy } = patch;
  let sum = 0;
  let squared = 0;
  let dot = 0;
  for (let i = 0; i < values.length; i++) {
    const sample = frame.gray[center + offsets[i]];
    sum += sample;
    squared += sample * sample;
    dot += values[i] * sample;
  }
  const variance = squared - sum * sum / values.length;
  return variance > 0 && energy > 0 ? dot / Math.sqrt(energy * variance) : -1;
}

function makeSearch(radius) {
  const side = 2 * radius + 1;
  const scores = new Float64Array(side * side);
  return (patch, frame, x, y) => {
    let best = -Infinity;
    let bx = 0;
    let by = 0;
    for (let dy = -radius; dy <= radius; dy++) {
      const row = (y + dy) * frame.width + x;
      for (let dx = -radius; dx <= radius; dx++) {
        const score = normalizedCorrelation(patch, frame, row + dx);
        scores[(dy + radius) * side + dx + radius] = score;
        if (score > best) {
          best = score;
          bx = dx;
          by = dy;
        }
      }
    }
    const peak = (by + radius) * side + bx + radius;
    const clipped = Math.abs(bx) === radius || Math.abs(by) === radius;
    const fx = Math.abs(bx) < radius ? subpixel(scores[peak - 1], best, scores[peak + 1]) : 0;
    const fy = Math.abs(by) < radius ? subpixel(scores[peak - side], best, scores[peak + side]) : 0;
    let second = -1;
    for (let dy = -radius; dy <= radius; dy++) {
      for (let dx = -radius; dx <= radius; dx++) {
        if (Math.abs(dx - bx) <= 2 && Math.abs(dy - by) <= 2) continue;
        second = Math.max(second, scores[(dy + radius) * side + dx + radius]);
      }
    }
    const zero = scores[radius * side + radius];
    return { x: x + bx + fx, y: y + by + fy, score: best, second, zero, clipped };
  };
}

function subpixel(before, peak, after) {
  const curvature = before - 2 * peak + after;
  return curvature < -1e-8 ? Math.max(-0.5, Math.min(0.5, (before - after) / (2 * curvature))) : 0;
}

function containsPatch(frame, x, y, margin) {
  return x >= margin && y >= margin && x + margin < frame.width && y + margin < frame.height;
}

function bankPatches(frame, cfg) {
  const radius = 12;
  const margin = radius + cfg.stabilizationRadius;
  const spacing = Math.max(12, Math.floor(Math.min(frame.width, frame.height) / 24));
  const bins = new Map();
  for (let y = margin; y + margin < frame.height; y += spacing) {
    for (let x = margin; x + margin < frame.width; x += spacing) {
      if (!cfg.staticPolygons.some(polygon =>
        insidePolygon(x, y, polygon) &&
        insidePolygon(x - radius, y - radius, polygon) &&
        insidePolygon(x + radius, y + radius, polygon) &&
        insidePolygon(x + radius, y - radius, polygon) &&
        insidePolygon(x - radius, y + radius, polygon))) continue;
      const patch = preparePatch(frame, x, y, radius, 2);
      const bin = Math.floor(4 * y / frame.height) * 5 + Math.floor(5 * x / frame.width);
      if (patch.texture >= 4 && patch.texture > (bins.get(bin)?.patch.texture ?? 0)) {
        bins.set(bin, { x, y, patch });
      }
    }
  }
  return [...bins.values()];
}

function fitSimilarity(matches) {
  const average = key => matches.reduce((sum, match) => sum + match[key], 0) / matches.length;
  const x = average("x");
  const y = average("y");
  const u = average("u");
  const v = average("v");
  let norm = 0;
  let dot = 0;
  let cross = 0;
  for (const match of matches) {
    const dx = match.x - x;
    const dy = match.y - y;
    const du = match.u - u;
    const dv = match.v - v;
    norm += dx * dx + dy * dy;
    dot += dx * du + dy * dv;
    cross += dx * dv - dy * du;
  }
  const a = dot / norm;
  const b = cross / norm;
  return { a, b, tx: u - a * x + b * y, ty: v - b * x - a * y };
}

function residual(match, pose) {
  const point = toFrame(match.x, match.y, pose);
  return Math.hypot(match.u - point.x, match.v - point.y);
}

function alignFrame(frame, patches, search) {
  const matches = [];
  for (const { x, y, patch } of patches) {
    const peak = search(patch, frame, x, y);
    if (peak.score >= 0.7 && !peak.clipped) matches.push({ x, y, u: peak.x, v: peak.y });
  }
  if (matches.length < 2) return null;
  const tx = median(matches.map(p => p.u - p.x));
  const ty = median(matches.map(p => p.v - p.y));
  const translation = { ...identity, tx, ty };
  let inliers = matches.filter(match => residual(match, translation) <= 2);
  if (inliers.length < 2 || inliers.length < matches.length / 2) return null;
  let pose = translation;
  if (inliers.length >= 4) {
    const fit = fitSimilarity(inliers);
    const scale = Math.hypot(fit.a, fit.b);
    if (scale >= 0.98 && scale <= 1.02 && Math.abs(fit.b) <= 0.035) {
      const refined = inliers.filter(match => residual(match, fit) <= 0.75);
      if (refined.length >= Math.max(4, inliers.length / 2)) {
        inliers = refined;
        pose = fitSimilarity(inliers);
      }
    }
  }
  return { pose, residual: median(inliers.map(match => residual(match, pose))) };
}

function pivGridStep(cfg) {
  return Math.max(1, Math.round(cfg.windowSize * (1 - cfg.overlap)));
}

function makePivGrid(frame, cfg) {
  const radius = Math.floor((cfg.windowSize - 1) / 2);
  const margin = radius + cfg.pivSearchRadius;
  const step = pivGridStep(cfg);
  const cells = [];
  for (let y = margin; y + margin < frame.height; y += step) {
    for (let x = margin; x + margin < frame.width; x += step) {
      if (inRegion(x, y, cfg.include, cfg.exclude) &&
          inRegion(x - radius, y - radius, cfg.include, cfg.exclude) &&
          inRegion(x + radius, y + radius, cfg.include, cfg.exclude) &&
          inRegion(x - radius, y + radius, cfg.include, cfg.exclude) &&
          inRegion(x + radius, y - radius, cfg.include, cfg.exclude)) cells.push({ x, y });
    }
  }
  return cells;
}

function correlatePair(a, b, poseA, poseB, cells, search, cfg) {
  const dt = b.time - a.time;
  const radius = Math.floor((cfg.windowSize - 1) / 2);
  const stride = Math.max(1, Math.round(cfg.windowSize / 12));
  const results = [];
  for (const { x, y } of cells) {
    const p = toFrame(x, y, poseA);
    const q = toFrame(x, y, poseB);
    const ax = Math.round(p.x);
    const ay = Math.round(p.y);
    const bx = Math.round(q.x + ax - p.x);
    const by = Math.round(q.y + ay - p.y);
    if (!containsPatch(a, ax, ay, radius) ||
        !containsPatch(b, bx, by, radius + cfg.pivSearchRadius)) continue;
    const patch = preparePatch(a, ax, ay, radius, stride);
    if (patch.texture < 2) continue;
    const peak = search(patch, b, bx, by);
    if (peak.clipped || peak.score < cfg.correlationThreshold || peak.score - peak.second < 0.025) continue;
    const source = toReference(ax, ay, poseA);
    const target = toReference(peak.x, peak.y, poseB);
    const dx = target.x - source.x;
    const dy = target.y - source.y;
    const zeroLagRatio = peak.zero / peak.score;
    results.push({ x, y, u: dx / dt, v: dy / dt, correlation: peak.score, zeroLagRatio,
      static: Math.hypot(dx, dy) <= cfg.staticThreshold, count: 1, source: "piv" });
  }
  return results;
}

export function pivPair(a, b, options = {}) {
  const cfg = settings(options);
  checkFrame(a);
  checkFrame(b, a);
  let poseB = identity;
  if (cfg.staticPolygons.length) {
    const alignment = alignFrame(b, bankPatches(a, cfg), makeSearch(cfg.stabilizationRadius));
    if (!alignment) throw new Error("Could not align the selected static areas between frames.");
    poseB = alignment.pose;
  }
  return correlatePair(a, b, identity, poseB, makePivGrid(a, cfg), makeSearch(cfg.pivSearchRadius), cfg);
}

function checkFrame(frame, previous) {
  const { gray, width, height, time } = frame;
  if (!Number.isInteger(width) || !Number.isInteger(height) || width < 1 || height < 1 ||
      gray?.length !== width * height || !Number.isFinite(time)) {
    throw new Error("Each frame needs dimensions, a matching grayscale buffer, and a time in seconds.");
  }
  if (previous && (width !== previous.width || height !== previous.height || time <= previous.time)) {
    throw new Error("Frames must have the same dimensions and strictly increasing times.");
  }
}

function newTrack(point) {
  return { points: [point], u: 0, v: 0 };
}

function addPoint(track, point) {
  const before = track.points.at(-1);
  const dt = point.time - before.time;
  const u = (point.x - before.x) / dt;
  const v = (point.y - before.y) / dt;
  const weight = track.points.length === 1 ? 1 : 0.4;
  track.u += weight * (u - track.u);
  track.v += weight * (v - track.v);
  track.points.push(point);
}

function trackMatches(active, detections, cfg, nominalDt) {
  const cellSize = cfg.maxDisplacement * (cfg.maxGapFrames + 1);
  const cellKey = (x, y) => `${Math.floor(x / cellSize)}:${Math.floor(y / cellSize)}`;
  const bins = new Map();
  detections.forEach((point, index) => {
    const key = cellKey(point.x, point.y);
    if (!bins.has(key)) bins.set(key, []);
    bins.get(key).push(index);
  });
  const trackBest = [];
  const pointBest = [];
  active.forEach((track, index) => {
    const prev = track.points.at(-1);
    const elapsed = detections[0].time - prev.time;
    const scale = elapsed / nominalDt;
    const radius = cfg.maxDisplacement * scale;
    const px = prev.x + track.u * elapsed;
    const py = prev.y + track.v * elapsed;
    const errorLimit = track.points.length > 2 ? Math.min(radius, Math.max(2.5, radius * 0.55)) : radius;
    for (let y = Math.floor((px - errorLimit) / cellSize); y <= Math.floor((py + errorLimit) / cellSize); y++) {
      for (let x = Math.floor((px - errorLimit) / cellSize); x <= Math.floor((px + errorLimit) / cellSize); x++) {
        for (const next of bins.get(`${x}:${y}`) ?? []) {
          const point = detections[next];
          if (Math.hypot(point.x - prev.x, point.y - prev.y) > radius) continue;
          const error = Math.hypot(point.x - px, point.y - py);
          if (error > errorLimit) continue;
          const areaPenalty = Math.abs(Math.log(point.area / prev.area));
          const score = error + areaPenalty;
          if (!trackBest[index] || score < trackBest[index].score) trackBest[index] = { next, score };
          if (!pointBest[next] || score < pointBest[next].score) pointBest[next] = { index, score };
        }
      }
    }
  });
  const matched = new Set();
  trackBest.forEach(({ next }, index) => {
    if (pointBest[next].index !== index) return;
    addPoint(active[index], detections[next]);
    matched.add(next);
  });
  return matched;
}

function localVelocity(points, index) {
  const start = Math.max(0, index - 2);
  const end = Math.min(points.length, index + 3);
  const count = end - start;
  const center = points[index];
  let sumT = 0;
  let sumX = 0;
  let sumY = 0;
  let sumTT = 0;
  let sumTX = 0;
  let sumTY = 0;
  for (let i = start; i < end; i++) {
    const point = points[i];
    const dt = point.time - center.time;
    const dx = point.x - center.x;
    const dy = point.y - center.y;
    sumT += dt;
    sumX += dx;
    sumY += dy;
    sumTT += dt * dt;
    sumTX += dt * dx;
    sumTY += dt * dy;
  }
  const denominator = sumTT - sumT * sumT / count;
  return { u: (sumTX - sumT * sumX / count) / denominator, v: (sumTY - sumT * sumY / count) / denominator };
}

function addSample(bins, sample, cellSize) {
  const x = Math.floor(sample.x / cellSize);
  const y = Math.floor(sample.y / cellSize);
  const key = `${x}:${y}`;
  if (!bins.has(key)) bins.set(key, []);
  bins.get(key).push(sample);
}

function aggregate(bins, minSamples, source) {
  const vectors = new Map();
  for (const [key, samples] of bins) {
    if (samples.length < minSamples) continue;
    const vector = {
      x: median(samples.map(p => p.x)),
      y: median(samples.map(p => p.y)),
      u: median(samples.map(p => p.u)),
      v: median(samples.map(p => p.v)),
      count: samples.length,
      source,
    };
    if (source === "ptv") vector.trackCount = new Set(samples.map(sample => sample.trackIndex)).size;
    vectors.set(key, vector);
  }
  return vectors;
}

export function createAnalyzer(options = {}) {
  const cfg = settings(options);
  const ptvEnabled = cfg.method !== "piv";
  const pivEnabled = cfg.method !== "ptv";
  const stabilize = cfg.staticPolygons.length > 0;
  const bankSearch = stabilize ? makeSearch(cfg.stabilizationRadius) : null;
  const pivSearch = pivEnabled ? makeSearch(cfg.pivSearchRadius) : null;
  const tracks = [];
  const ptvBins = new Map();
  const pivCells = new Map();
  const bankResiduals = [];
  let detector;
  let grid;
  let bank;
  let frameCount = 0;
  let detectionCount = 0;
  let ptvSampleCount = 0;
  let pivPairCount = 0;
  let stabilizationFailures = 0;
  let previousFrame;
  let previousPose;
  let previousTime;
  let dimensions;
  let nominalDt;
  let active = [];
  let finished;

  function closeTrack(track) {
    const { points } = track;
    if (points.length < cfg.minTrackFrames) return;
    const samples = points.map((point, index) => ({ ...point, ...localVelocity(points, index) }));
    const moving = samples.filter(p => Math.hypot(p.u, p.v) * nominalDt > cfg.staticThreshold);
    if (moving.length <= points.length / 2) return;
    for (const sample of moving) {
      if (!inRegion(sample.x, sample.y, cfg.include, cfg.exclude)) continue;
      addSample(ptvBins, { ...sample, trackIndex: tracks.length }, cfg.cellSize);
      ptvSampleCount++;
    }
    tracks.push({
      points: points.map(({ x, y, time }) => ({ x, y, time })),
      u: median(moving.map(point => point.u)),
      v: median(moving.map(point => point.v)),
    });
  }

  function advanceTracks(frame, pose) {
    active = active.filter(track => {
      if (frameCount - track.points.at(-1).frame <= cfg.maxGapFrames + 1) return true;
      closeTrack(track);
      return false;
    });
    if (!pose) return;
    const detections = detector(frame.gray).map(point => ({
      ...point, ...toReference(point.x, point.y, pose), time: frame.time, frame: frameCount,
    }));
    detectionCount += detections.length;
    if (!detections.length) return;
    const matched = active.length ? trackMatches(active, detections, cfg, nominalDt) : new Set();
    detections.forEach((point, index) => {
      if (!matched.has(index)) active.push(newTrack(point));
    });
  }

  function push(frame) {
    if (finished) throw new Error("Start a new analyzer to add more frames.");
    checkFrame(frame, dimensions && { ...dimensions, time: previousTime });
    if (frameCount === 0) {
      dimensions = { width: frame.width, height: frame.height };
      if (ptvEnabled) detector = makeDetector(frame.width, frame.height, cfg, regionMask(frame.width, frame.height, cfg));
      if (pivEnabled) grid = makePivGrid(frame, cfg);
      if (stabilize) bank = bankPatches(frame, cfg);
    } else {
      const dt = frame.time - previousTime;
      nominalDt = nominalDt === undefined ? dt : Math.min(nominalDt, dt);
    }

    let pose = identity;
    if (stabilize && frameCount > 0) {
      const alignment = alignFrame(frame, bank, bankSearch);
      pose = alignment?.pose;
      if (alignment) bankResiduals.push(alignment.residual);
      else stabilizationFailures++;
    }

    if (ptvEnabled) advanceTracks(frame, pose);
    if (pivEnabled && previousPose && pose && (frameCount - 1) % cfg.pivPairStride === 0) {
      const samples = correlatePair(previousFrame, frame, previousPose, pose, grid, pivSearch, cfg);
      for (const sample of samples) {
        const key = `${sample.x}:${sample.y}`;
        if (!pivCells.has(key)) pivCells.set(key, []);
        pivCells.get(key).push(sample);
      }
      pivPairCount++;
    }
    if (pivEnabled) previousFrame = frame;
    previousPose = pose;
    previousTime = frame.time;
    frameCount++;
    cfg.onProgress?.({ stage: "analysis", completed: frameCount });
  }

  function finish() {
    if (finished) return finished;
    if (frameCount < 2) throw new Error("At least two decoded frames are needed.");
    for (const track of active) closeTrack(track);
    active = [];
    previousFrame = null;
    let pivStaticCells = 0;
    let pivRejectedCells = 0;
    const pivBins = new Map();
    for (const samples of pivCells.values()) {
      const moving = samples.filter(sample => !sample.static);
      if (moving.length <= samples.length * 0.35) {
        pivStaticCells++;
        continue;
      }
      const clean = moving.filter(sample => sample.zeroLagRatio <= cfg.maxZeroLagRatio);
      if (clean.length < Math.max(Math.min(3, pivPairCount), cfg.minSamples)) {
        pivRejectedCells++;
        continue;
      }
      const u = median(clean.map(sample => sample.u));
      const v = median(clean.map(sample => sample.v));
      const coherency = Math.hypot(u, v) / median(clean.map(sample => Math.hypot(sample.u, sample.v)));
      if (coherency < 0.65) {
        pivRejectedCells++;
        continue;
      }
      const { x, y } = clean[0];
      addSample(pivBins, { x, y, u, v, count: clean.length }, cfg.cellSize);
    }

    const piv = aggregate(pivBins, 1, "piv");
    for (const [key, vector] of piv) {
      vector.count = pivBins.get(key).reduce((sum, sample) => sum + sample.count, 0);
    }
    const ptv = aggregate(ptvBins, cfg.minSamples, "ptv");
    const vectors = new Map([...piv, ...ptv]);
    finished = {
      vectors: [...vectors.values()],
      ptvVectors: [...ptv.values()],
      pivVectors: [...piv.values()],
      tracks,
      pivGridStep: pivEnabled ? pivGridStep(cfg) : null,
      diagnostics: {
        frameCount, detectionCount, trackCount: tracks.length, ptvSampleCount, pivPairCount,
        pivCandidateCells: pivCells.size, pivStaticCells, pivRejectedCells,
        stabilizationMode: stabilize ? "bank" : "off", stabilizationFailures,
        stabilizationResidualPx: median(bankResiduals),
      },
    };
    return finished;
  }
  return { push, finish };
}

export function analyzeFrames(frames, options = {}) {
  const analyzer = createAnalyzer(options);
  for (const frame of frames) analyzer.push(frame);
  return analyzer.finish();
}

export function trackTracers(frames, options = {}) {
  return analyzeFrames(frames, { ...options, method: "ptv" }).tracks;
}

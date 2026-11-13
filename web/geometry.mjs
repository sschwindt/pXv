import { autoUtmEpsg, describeCrs, reprojectPoint } from './spatial-reference.mjs';

const radians = Math.PI / 180;

const telemetryFields = {
  rel_alt: 'heightAboveTakeoff',
  focal_len: 'focal35',
  gb_yaw: 'yaw',
  gb_pitch: 'pitch',
  gb_roll: 'roll',
  latitude: 'latitude',
  longitude: 'longitude',
};

function median(values) {
  if (!values.length) return null;
  values.sort((a, b) => a - b);
  const middle = Math.floor(values.length / 2);
  return (values[middle] + values[Math.ceil(values.length / 2) - 1]) / 2;
}

function subtitleTime(hours, minutes, seconds, millis) {
  return Number(hours) * 3600 + Number(minutes) * 60 + Number(seconds) + Number(millis) / 1000;
}

/** Read DJI subtitle timing and summarize telemetry near the stabilization reference frame. */
export function parseDjiSrt(text, { startFrame = 0, endFrame = Infinity } = {}) {
  const samples = [];
  for (const block of text.trim().split(/\r?\n[\t ]*\r?\n/)) {
    const timing = block.match(/(\d+):(\d\d):(\d\d)[,.](\d{3})\s*-->\s*(\d+):(\d\d):(\d\d)[,.](\d{3})/);
    const counter = block.match(/\bFrameCnt\s*:\s*(\d+)/i);
    const index = counter ? Number(counter[1]) - 1 : samples.length;
    const values = {};
    for (const [, bracket] of block.matchAll(/\[([^\]]*)\]/g)) {
      for (const [, key, value] of bracket.matchAll(/([a-z_]+)\s*:\s*([-+]?\d+(?:\.\d+)?)(?=\s|$)/gi)) {
        const field = telemetryFields[key.toLowerCase()];
        if (field) values[field] = Number(value);
      }
    }
    if (timing) {
      samples.push({ index, time: subtitleTime(...timing.slice(1, 5)),
        end: subtitleTime(...timing.slice(5, 9)), ...values });
    }
  }
  if (!samples.length) throw new Error('No DJI subtitle timings found.');
  const first = samples[0];
  const last = samples.at(-1);
  const duration = last.end - first.time;
  const frameCount = last.index + 1;
  const fps = last.time > first.time && last.index > first.index
    ? (last.index - first.index) / (last.time - first.time) : null;
  const reference = samples.filter(sample => sample.index >= startFrame &&
    sample.index < Math.min(endFrame, startFrame + 30));
  const telemetry = { fps, frameCount, duration };
  for (const field of Object.values(telemetryFields)) {
    telemetry[field] = median(reference.map(sample => sample[field]).filter(Number.isFinite));
  }
  return telemetry;
}

/** ETRS89 / UTM zone 32N. DJI WGS 84 fixes are used as approximate ETRS89 coordinates. */
export function utm32(latitude, longitude) {
  return reprojectPoint(longitude, latitude, 4326, 25832);
}

function aroundX([x, y, z], angle) {
  const c = Math.cos(angle);
  const s = Math.sin(angle);
  return [x, c * y - s * z, s * y + c * z];
}

function aroundZ([x, y, z], angle) {
  const c = Math.cos(angle);
  const s = Math.sin(angle);
  return [c * x - s * y, s * x + c * y, z];
}

function displacementFrom(toWorld) {
  return (x, y, dx, dy, dt) => {
    if (!Number.isFinite(dt) || dt <= 0) throw new Error('Frame interval must be positive.');
    const [x0, y0] = toWorld(x, y);
    const [x1, y1] = toWorld(x + dx, y + dy);
    return [(x1 - x0) / dt, (y1 - y0) / dt];
  };
}

/** Project a DJI camera ray onto the local horizontal water surface. */
export function cameraGeometry({ width, height, heightAboveTakeoff, waterOffset = 0,
  focal35, yaw = 0, pitch = -90, roll = 0, latitude, longitude, epsg: requestedEpsg }) {
  if (![width, height, heightAboveTakeoff, waterOffset, focal35, yaw, pitch, roll].every(Number.isFinite)) {
    throw new Error('Enter the camera height, focal length and gimbal angles, or load DJI telemetry.');
  }
  const heightAboveWater = heightAboveTakeoff + waterOffset;
  if (width <= 0 || height <= 0 || focal35 <= 0 || heightAboveWater <= 0) {
    throw new Error('Image dimensions, focal length and height above water must be positive.');
  }
  let epsg = autoUtmEpsg(latitude, longitude);
  if (requestedEpsg != null) {
    const crs = describeCrs(requestedEpsg);
    if (crs?.projection !== 'utm' || !epsg || crs.southern !== (latitude < 0)) {
      throw new Error('Choose a local UTM coordinate system matching the DJI GPS position.');
    }
    epsg = crs.epsg;
  } else if (epsg === 32632 && latitude >= 40 && latitude <= 62 && longitude >= 6 && longitude < 12) {
    // Keep the ETRS89 camera frame used by the Isar preset and adjacent surveys.
    epsg = 25832;
  }
  const origin = epsg ? reprojectPoint(longitude, latitude, 4326, epsg) : [0, 0];
  const cx = width / 2;
  const cy = height / 2;
  // DJI focal_len is already zoom-adjusted and expressed in 35 mm equivalent.
  const focalPx = focal35 * width / 36;
  const bearing = yaw * radians;
  const tilt = (90 + pitch) * radians;
  const imageRoll = roll * radians;

  function toWorld(x, y) {
    const cameraRay = [(x - cx) / focalPx, (y - cy) / focalPx, 1];
    const [east, south, down] = aroundX(aroundZ(cameraRay, -imageRoll), tilt);
    const [de, dn, up] = aroundZ([east, -south, -down], -bearing);
    if (up >= -1e-9) return [NaN, NaN];
    const scale = -heightAboveWater / up;
    return [origin[0] + de * scale, origin[1] + dn * scale];
  }

  function toImage(easting, northing) {
    const [east, north, up] = aroundZ([easting - origin[0], northing - origin[1], -heightAboveWater], bearing);
    const [x, y, z] = aroundZ(aroundX([east, -north, -up], -tilt), imageRoll);
    if (z <= 1e-9) return [NaN, NaN];
    return [cx + focalPx * x / z, cy + focalPx * y / z];
  }

  function gsdAt(y) {
    const [x0, y0] = toWorld(cx, y);
    const [x1, y1] = toWorld(cx + 1, y);
    return Math.hypot(x1 - x0, y1 - y0);
  }

  return { units: 'm', epsg, inputEpsg: epsg ? 4326 : null, origin, heightAboveWater, toWorld, toImage,
    displacement: displacementFrom(toWorld), gsd: { center: gsdAt(cy), top: gsdAt(1), bottom: gsdAt(height - 1) } };
}

function centroid(points) {
  const count = points.length;
  return [points.reduce((sum, point) => sum + point[0], 0) / count,
    points.reduce((sum, point) => sum + point[1], 0) / count];
}

function normalizePoints(points) {
  const center = centroid(points);
  const scale = Math.sqrt(points.reduce((sum, [x, y]) =>
    sum + (x - center[0]) ** 2 + (y - center[1]) ** 2, 0) / (2 * points.length));
  if (!(scale > 0) || !Number.isFinite(scale)) throw new Error('The control points must have distinct coordinates.');
  const normalize = (x, y) => [(x - center[0]) / scale, (y - center[1]) / scale];
  const restore = (x, y) => [center[0] + x * scale, center[1] + y * scale];
  return { points: points.map(([x, y]) => normalize(x, y)), center, normalize, restore };
}

const dot = (a, b) => a.reduce((sum, value, index) => sum + value * b[index], 0);

function leastSquares(rows, values) {
  // Modified Gram-Schmidt QR avoids squaring the condition number.
  const width = rows[0].length;
  const columns = Array.from({ length: width }, (_, index) => rows.map(row => row[index]));
  const triangular = Array.from({ length: width }, () => []);
  const projected = [];
  for (let i = 0; i < width; i++) {
    const column = columns[i];
    const norm = Math.sqrt(dot(column, column));
    if (!(norm > 1e-5 * Math.sqrt(rows.length))) {
      throw new Error('The control points are collinear or poorly distributed. Spread them across the image and check their coordinates.');
    }
    const orthogonal = column.map(value => value / norm);
    triangular[i][i] = norm;
    projected[i] = dot(orthogonal, values);
    for (let j = i + 1; j < width; j++) {
      const coefficient = dot(orthogonal, columns[j]);
      triangular[i][j] = coefficient;
      columns[j] = columns[j].map((value, index) => value - coefficient * orthogonal[index]);
    }
  }
  const result = [];
  for (let i = width - 1; i >= 0; i--) {
    let value = projected[i];
    for (let j = i + 1; j < width; j++) value -= triangular[i][j] * result[j];
    result[i] = value / triangular[i][i];
  }
  return result;
}

function fitControlPoints(source, target, perspective) {
  const rows = [];
  const values = [];
  for (let i = 0; i < source.points.length; i++) {
    const [x, y] = source.points[i];
    const [e, n] = target.points[i];
    const east = [x, y, 1, 0, 0, 0];
    const north = [0, 0, 0, x, y, 1];
    if (perspective) {
      east.push(-e * x, -e * y);
      north.push(-n * x, -n * y);
    }
    rows.push(east, north);
    values.push(e, n);
  }
  const [a, b, c, d, e, f, g = 0, h = 0] = leastSquares(rows, values);
  const determinant = a * (e - f * h) - b * (d - f * g) + c * (d * h - e * g);
  if (!(Math.abs(determinant) > 1e-8)) {
    throw new Error('The ground control points do not define an invertible map. Check their coordinates.');
  }
  const denominator = (x, y) => 1 + g * x + h * y;
  if (source.points.some(([x, y]) => denominator(x, y) <= 1e-8)) {
    throw new Error('The projective horizon crosses the control points. Check the point correspondences.');
  }
  const toWorld = (px, py) => {
    const [x, y] = source.normalize(px, py);
    const divisor = denominator(x, y);
    return divisor > 1e-8 ? target.restore((a * x + b * y + c) / divisor, (d * x + e * y + f) / divisor)
      : [NaN, NaN];
  };
  const toImage = (east, north) => {
    const [u, v] = target.normalize(east, north);
    const j = a - u * g;
    const k = b - u * h;
    const l = d - v * g;
    const m = e - v * h;
    const inverse = j * m - k * l;
    const x = ((u - c) * m - k * (v - f)) / inverse;
    const y = (j * (v - f) - (u - c) * l) / inverse;
    return Math.abs(inverse) > 1e-12 && denominator(x, y) > 1e-8 ? source.restore(x, y) : [NaN, NaN];
  };
  return { toWorld, toImage };
}

/** Fit a water-plane map from three affine or at least four projective control points. */
export function controlPointGeometry({ image, world, epsg: inputCode, width, height }) {
  const validPoint = point => Array.isArray(point) && point.length === 2 && point.every(Number.isFinite);
  if (!Array.isArray(image) || !Array.isArray(world) || image.length < 3 || world.length !== image.length ||
      !image.every(validPoint) || !world.every(validPoint)) {
    throw new Error('Provide three or more corresponding image and ground points.');
  }
  const crs = describeCrs(inputCode);
  if (!crs) throw new Error('Choose a supported coordinate system for the ground control points.');
  const inputEpsg = crs.epsg;
  const geographic = reprojectPoint(...world[0], inputEpsg, 4326);
  const epsg = crs.projection === 'utm' ? inputEpsg : autoUtmEpsg(geographic[1], geographic[0]);
  if (!epsg) throw new Error('The control points need a supported local UTM coordinate system.');
  const ground = world.map(([x, y]) => reprojectPoint(x, y, inputEpsg, epsg));
  const source = normalizePoints(image);
  const target = normalizePoints(ground);
  const perspective = image.length >= 4;
  const { toWorld, toImage } = fitControlPoints(source, target, perspective);
  const controlPointRmse = Math.sqrt(image.reduce((sum, point, i) => {
    const [e, n] = toWorld(...point);
    return sum + (e - ground[i][0]) ** 2 + (n - ground[i][1]) ** 2;
  }, 0) / image.length);
  const [cx, cy] = source.center;
  const sampleX = Number.isFinite(width) ? width / 2 : cx;
  const centerY = Number.isFinite(height) ? height / 2 : cy;
  if (width > 0 && height > 0 && [[0, 0], [width - 1, 0], [0, height - 1], [width - 1, height - 1]]
    .some(point => !toWorld(...point).every(Number.isFinite))) {
    throw new Error('The projective horizon crosses the video frame. Spread the control points and check their positions.');
  }
  const displacement = displacementFrom(toWorld);
  const scaleAt = y => Math.hypot(...displacement(sampleX, y, 1, 0, 1));
  return { units: 'm', epsg, inputEpsg, origin: toWorld(cx, cy), toWorld, toImage,
    displacement, model: perspective ? 'projective' : 'affine',
    gsd: { center: scaleAt(centerY), top: scaleAt(Number.isFinite(height) ? 0 : centerY),
      bottom: scaleAt(Number.isFinite(height) ? height - 1 : centerY) },
    controlPointRmse, controlPointCount: image.length };
}

/** Calibrate a flat image plane with a known scale. With no scale, use pixels. */
export function linearGeometry(metresPerPixel = null) {
  const scale = metresPerPixel ?? 1;
  if (!Number.isFinite(scale) || scale <= 0) throw new Error('Metres per pixel must be positive.');
  const toWorld = (x, y) => [x * scale, y * scale];
  const toImage = (x, y) => [x / scale, y / scale];
  const gsd = metresPerPixel === null ? null : { center: scale, top: scale, bottom: scale };
  return { units: metresPerPixel === null ? 'px' : 'm', epsg: null, origin: [0, 0], gsd,
    toWorld, toImage, displacement: displacementFrom(toWorld) };
}

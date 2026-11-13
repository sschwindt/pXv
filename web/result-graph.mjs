import { reprojectPoint } from './spatial-reference.mjs'

export const graphLimits = Object.freeze({ minWidth: 320, minHeight: 240, maxDimension: 8192, maxPixels: 24_000_000 })
export const graphFormats = Object.freeze({ webp: 'image/webp', jpg: 'image/jpeg' })
export const defaultGraphQuality = 0.86
export const surveyColor = '#ff3a9c'

export function validateGraphSize(width, height) {
  const { minWidth, minHeight, maxDimension, maxPixels } = graphLimits
  if (![width, height].every(Number.isInteger) || width < minWidth || height < minHeight ||
      width > maxDimension || height > maxDimension || width * height > maxPixels) {
    throw new Error('Choose a whole-number image size from 320 × 240 px up to 8,192 px per side and 24 megapixels total.')
  }
}

const ink = '#17231d'
const secondary = '#526058'
const tracerColor = '#d6f9a9'
const excludedColor = '#ffb195'
const scaleText = value => Number(value.toPrecision(3)).toString()
const validPoint = point => point?.length === 2 && point.every(Number.isFinite)
const speedColor = fraction => 'hsl(' + (179 - 127 * Math.min(1, Math.max(0, fraction))) + ' 86% 69%)'

function niceLength(value) {
  const base = 10 ** Math.floor(Math.log10(value))
  const multiple = value / base
  return base * (multiple >= 5 ? 5 : multiple >= 2 ? 2 : 1)
}

/** Return a bar length in source pixels for a horizontal segment at the image centre. */
export function graphScaleBar(geometry, x, y, maximumPixels) {
  if (!(maximumPixels > 0)) return null
  const metric = geometry?.units === 'm'
  const distance = metric ? pixels => {
    const first = geometry.toWorld(x - pixels / 2, y)
    const last = geometry.toWorld(x + pixels / 2, y)
    return validPoint(first) && validPoint(last) ? Math.hypot(last[0] - first[0], last[1] - first[1]) : NaN
  } : pixels => pixels
  const limit = distance(maximumPixels)
  if (!(limit > 0) || !Number.isFinite(limit)) return null
  const length = niceLength(limit)
  if (!metric) return { pixels: length, length, units: 'px' }
  let low = 0
  let high = maximumPixels
  for (let step = 0; step < 32; step++) {
    const test = (low + high) / 2
    if (distance(test) < length) low = test
    else high = test
  }
  return { pixels: (low + high) / 2, length, units: 'm' }
}

/** Find the direction of true north at an image position, with a grid-north fallback. */
export function graphNorth(geometry, x, y) {
  if (!geometry?.epsg) return null
  const world = geometry.toWorld(x, y)
  if (!validPoint(world)) return null
  function direction(point, label) {
    if (!validPoint(point)) return null
    const dx = point[0] - x
    const dy = point[1] - y
    const length = Math.hypot(dx, dy)
    return length > 1e-8 ? { x: dx / length, y: dy / length, label } : null
  }
  try {
    const [longitude, latitude] = reprojectPoint(...world, geometry.epsg, 4326)
    const north = reprojectPoint(longitude, latitude + 0.00001, 4326, geometry.epsg)
    const trueNorth = direction(geometry.toImage(...north), 'N')
    if (trueNorth) return trueNorth
  } catch {
    // The latitude step may cross the validity boundary of a projected CRS.
  }
  return direction(geometry.toImage(world[0], world[1] + 1), 'Grid N')
}

function line(context, points, color, width, outline = 0) {
  if (!points.length) return
  context.beginPath()
  context.moveTo(...points[0])
  for (const point of points.slice(1)) context.lineTo(...point)
  if (outline) {
    context.strokeStyle = '#112017'
    context.lineWidth = width + outline
    context.stroke()
  }
  context.strokeStyle = color
  context.lineWidth = width
  context.stroke()
}

function arrow(context, x, y, angle, length, color, stroke, outline = 0) {
  const end = [x + Math.cos(angle) * length, y + Math.sin(angle) * length]
  line(context, [[x, y], end], color, stroke, outline)
  const head = Math.min(4 * stroke, length * .36)
  const wings = [-.56, .56].map(turn => [
    end[0] - Math.cos(angle + turn) * head, end[1] - Math.sin(angle + turn) * head,
  ])
  line(context, [wings[0], end, wings[1]], color, stroke, outline)
}

function surveyMarker(context, x, y, radius) {
  context.beginPath()
  context.moveTo(x, y - radius)
  context.lineTo(x + radius, y)
  context.lineTo(x, y + radius)
  context.lineTo(x - radius, y)
  context.closePath()
  context.fillStyle = surveyColor
  context.fill()
  context.strokeStyle = '#231322'
  context.lineWidth = Math.max(1.25, radius * .38)
  context.stroke()
}

function cross(context, x, y, radius, color, width, outline = 0) {
  line(context, [[x - radius, y - radius], [x + radius, y + radius]], color, width, outline)
  line(context, [[x + radius, y - radius], [x - radius, y + radius]], color, width, outline)
}

function label(context, text, x, y, fontSize, { color = ink, weight = 400, align = 'left', width } = {}) {
  context.fillStyle = color
  context.font = weight + ' ' + fontSize + 'px Arial, sans-serif'
  context.textAlign = align
  context.textBaseline = 'middle'
  if (width !== undefined) {
    const ellipsis = '…'
    if (context.measureText(text).width > width) {
      let end = text.length
      while (end > 0 && context.measureText(text.slice(0, end) + ellipsis).width > width) end--
      text = text.slice(0, end) + ellipsis
    }
  }
  context.fillText(text, x, y)
}

function drawMap(context, { image, vectors, geometry, tracks, survey, rejected, masks }, map, maximum) {
  const { x, y, scale } = map
  const project = (px, py) => [x + px * scale, y + py * scale]
  const stroke = Math.max(.65, map.width / 1050)
  context.save()
  context.beginPath()
  context.rect(x, y, map.width, map.height)
  context.clip()
  context.drawImage(image, x, y, map.width, map.height)
  for (const [polygons, color] of [[masks?.include, '#b9ef9c'], [masks?.staticPolygons, '#ffe1ac']]) {
    for (const polygon of polygons ?? []) {
      if (polygon.length < 3) continue
      line(context, [...polygon, polygon[0]].map(point => project(...point)), color, stroke, stroke)
    }
  }
  for (const track of tracks) {
    line(context, track.points.map(point => project(point.x, point.y)), tracerColor, stroke, stroke)
  }
  for (const vector of vectors) {
    const { x: px, y: py, u, v } = vector.pixel
    const [startX, startY] = project(px, py)
    const fraction = maximum > 0 ? vector.speed / maximum : 0
    const length = fraction * Math.max(5, map.width / 62)
    if (length >= stroke) {
      arrow(context, startX, startY, Math.atan2(v, u), length, speedColor(fraction), stroke * 1.2, stroke)
    }
  }
  for (const vector of rejected) {
    const [px, py] = project(vector.pixel.x, vector.pixel.y)
    cross(context, px, py, 2.5 * stroke, excludedColor, stroke, stroke)
  }
  if (geometry?.epsg) {
    for (const point of survey) {
      const position = geometry.toImage(point.x, point.y)
      if (!validPoint(position)) continue
      const [px, py] = project(...position)
      surveyMarker(context, px, py, Math.max(4.5, 6 * stroke))
    }
  }
  context.restore()
}

function drawFooter(context, { width, image, geometry, vectors, tracks, survey, rejected, maximum }, map, footer) {
  const { inset, top, type } = footer
  const half = width / 2
  const right = width - inset
  const rampWidth = half - inset - 1.1 * type
  const speedTitle = 'Surface speed (' + (geometry?.units === 'm' ? 'm/s' : 'px/s') + ')'
  label(context, speedTitle, inset, top + .8 * type, type, { weight: 700, width: rampWidth })
  if (vectors.length && maximum > 0) {
    const gradient = context.createLinearGradient(inset, 0, inset + rampWidth, 0)
    for (let step = 0; step <= 8; step++) gradient.addColorStop(step / 8, speedColor(step / 8))
    context.fillStyle = gradient
    context.fillRect(inset, top + 1.5 * type, rampWidth, .58 * type)
    context.strokeStyle = '#93a097'
    context.lineWidth = Math.max(1, type * .05)
    context.strokeRect(inset, top + 1.5 * type, rampWidth, .58 * type)
    for (const [fraction, align] of [[0, 'left'], [.5, 'center'], [1, 'right']]) {
      label(context, scaleText(maximum * fraction), inset + fraction * rampWidth, top + 2.85 * type, type, { align })
    }
  } else {
    label(context, vectors.length ? '0' : 'No accepted vectors', inset, top + 2.15 * type, type,
      { color: secondary, width: rampWidth })
  }

  const localX = image.width / 2
  const localY = image.height / 2
  const scaleBar = graphScaleBar(geometry, localX, localY, Math.min(image.width * .36, (half - 5 * type) / map.scale))
  const north = graphNorth(geometry, localX, localY)
  const northX = right - 1.35 * type
  const northY = top + 2.02 * type
  const scaleWidth = northX - half - 1.65 * type
  label(context, 'Scale at image centre', half, top + .8 * type, type, { weight: 700, width: scaleWidth })
  if (scaleBar) {
    const end = half + scaleBar.pixels * map.scale
    const level = top + 1.82 * type
    line(context, [[half, level], [end, level]], ink, Math.max(1, type * .13))
    for (const tick of [half, end]) line(context, [[tick, level - type * .27], [tick, level + type * .27]], ink, Math.max(1, type * .11))
    label(context, scaleText(scaleBar.length) + ' ' + scaleBar.units, half, top + 2.85 * type, type)
  } else {
    label(context, 'Unavailable', half, top + 2.15 * type, type, { color: secondary, width: scaleWidth })
  }
  if (north) {
    label(context, north.label, northX, top + .44 * type, type, { weight: 700, align: 'center' })
    arrow(context, northX - north.x * .66 * type, northY - north.y * .66 * type,
      Math.atan2(north.y, north.x), 1.5 * type, ink, Math.max(1, type * .11))
  } else {
    label(context, 'North', right, top + 1.25 * type, type, { weight: 700, align: 'right' })
    label(context, 'unavailable', right, top + 2.25 * type, type, { color: secondary, align: 'right' })
  }

  const keys = []
  if (tracks.length) keys.push(['Tracer track', tracerColor, line])
  if (survey.length && geometry?.epsg) keys.push(['Survey point', surveyColor, surveyMarker])
  if (rejected.length) keys.push(['Excluded vector', excludedColor, cross])
  for (const [index, [title, color, mark]] of keys.entries()) {
    const left = inset + index * (width - 2 * inset) / keys.length
    const level = top + 4.14 * type
    const radius = type * .26
    if (mark === line) line(context, [[left, level], [left + 2 * radius, level]], color, type * .12, type * .13)
    else if (mark === surveyMarker) surveyMarker(context, left + radius, level, radius)
    else mark(context, left + radius, level, radius, color, type * .1, type * .13)
    label(context, title, left + .8 * type, level, type, { color: secondary,
      width: (width - 2 * inset) / keys.length - type })
  }
}

/**
 * Render the reference frame and velocity overlays as a WebP or JPEG.
 * Georeferenced plots include a north arrow and a metric scale bar.
 *
 * @param {object} graph - Image, velocity vectors, geometry, and export options.
 * @param {number} graph.width - Output width in pixels.
 * @param {number} graph.height - Output height in pixels.
 * @param {string} [graph.format=webp] - Choose ``webp`` or ``jpg``.
 * @param {number} [graph.quality=0.86] - Compression quality from zero to one.
 * @returns {Promise<Blob>} Encoded image.
 */
export async function renderResultGraph({ width, height, image, vectors, geometry,
  title = 'pXv', tracks = [], survey = [], rejected = [], masks,
  format = 'webp', quality = defaultGraphQuality }) {
  validateGraphSize(width, height)
  if (!Object.hasOwn(graphFormats, format)) throw new Error('Choose WebP or JPG for the result graph.')
  if (!Number.isFinite(quality) || quality < 0 || quality > 1) throw new Error('Image quality must be between 0 and 1.')
  if (!(image?.width > 0) || !(image?.height > 0)) throw new Error('The reference frame is still loading.')
  const graph = new OffscreenCanvas(width, height)
  const context = graph.getContext('2d', { alpha: false })
  if (!context) throw new Error('Could not create an image canvas at this resolution.')
  const type = Math.max(11, Math.min(width / 53, height / 34))
  const inset = type * 1.12
  const mapTop = type * 2.65
  const keys = tracks.length || (survey.length && geometry?.epsg) || rejected.length
  const footerHeight = type * (keys ? 5.2 : 4)
  const mapArea = height - mapTop - footerHeight
  const scale = Math.min((width - 2 * inset) / image.width, mapArea / image.height)
  const map = {
    x: (width - image.width * scale) / 2, y: mapTop + (mapArea - image.height * scale) / 2,
    width: image.width * scale, height: image.height * scale, scale,
  }
  const maximum = vectors.reduce((max, vector) => Math.max(max, vector.speed), 0)

  context.fillStyle = '#f5f7f3'
  context.fillRect(0, 0, width, height)
  context.fillStyle = '#e6ece5'
  context.fillRect(inset, mapTop, width - 2 * inset, mapArea)
  label(context, title, inset, type * 1.28, type, { weight: 700, width: width - 2 * inset })
  context.imageSmoothingQuality = 'high'
  context.lineCap = 'round'
  context.lineJoin = 'round'
  drawMap(context, { image, vectors, geometry, tracks, survey, rejected, masks }, map, maximum)
  drawFooter(context, { width, image, geometry, vectors, tracks, survey, rejected, maximum }, map, {
    inset, top: height - footerHeight + type * .25, type,
  })
  const mimeType = graphFormats[format]
  const imageBlob = await graph.convertToBlob({ type: mimeType, quality })
  if (imageBlob.type !== mimeType) throw new Error('This browser cannot export ' + format.toUpperCase() + '. Choose the other format.')
  return imageBlob
}

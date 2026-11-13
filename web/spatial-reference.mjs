const rad = Math.PI / 180
const semiMajorAxis = 6378137
const utmScale = 0.9996
const falseEasting = 500000
const falseSouthNorthing = 10000000
const mercatorMaxLatitude = 85.06
const mercatorMaxNorthing = semiMajorAxis * Math.asinh(Math.tan(mercatorMaxLatitude * rad))

export function parseEpsg(value) {
  if (typeof value === 'number') return Number.isInteger(value) && value > 0 ? value : null
  if (typeof value !== 'string') return null
  const text = value.trim()
  if (/^(?:urn:ogc:def:crs:ogc:[^:]*:)?crs84$/i.test(text)) return 4326
  const match = text.match(/^(?:EPSG\s*:\s*|urn:ogc:def:crs:EPSG:[^:]*:|https?:\/\/www\.opengis\.net\/def\/crs\/EPSG\/[^/]+\/)?(\d+)$/i)
  if (!match) return null
  const epsg = Number(match[1])
  return Number.isSafeInteger(epsg) && epsg > 0 ? epsg : null
}

export function describeCrs(value) {
  const epsg = parseEpsg(value)
  if (epsg === 4326 || epsg === 4258) {
    const datum = epsg === 4326 ? 'WGS 84' : 'ETRS89'
    return { epsg, name: datum, datum, units: 'degrees', projection: 'geographic' }
  }
  if (epsg === 3857) {
    return { epsg, name: 'WGS 84 / Pseudo-Mercator', datum: 'WGS 84', units: 'm', projection: 'web-mercator' }
  }
  let zone
  let southern = false
  let datum = 'WGS 84'
  if (epsg >= 32601 && epsg <= 32660) zone = epsg - 32600
  if (epsg >= 32701 && epsg <= 32760) {
    zone = epsg - 32700
    southern = true
  }
  if (epsg >= 25828 && epsg <= 25838) {
    zone = epsg - 25800
    datum = 'ETRS89'
  }
  if (!zone) return null
  return { epsg, name: datum + ' / UTM zone ' + zone + (southern ? 'S' : 'N'),
    datum, units: 'm', projection: 'utm', zone, southern }
}

export function autoUtmEpsg(latitude, longitude) {
  if (![latitude, longitude].every(Number.isFinite) || latitude < -80 || latitude > 84 ||
      longitude < -180 || longitude > 180) return null
  let zone = Math.min(60, 1 + Math.floor((longitude + 180) / 6))
  if (latitude >= 56 && latitude < 64 && longitude >= 3 && longitude < 12) zone = 32
  if (latitude >= 72 && latitude < 84 && longitude >= 0 && longitude < 42) {
    zone = longitude < 9 ? 31 : longitude < 21 ? 33 : longitude < 33 ? 35 : 37
  }
  return (latitude < 0 ? 32700 : 32600) + zone
}

function series(inverseFlattening) {
  const f = 1 / inverseFlattening
  const n = f / (2 - f)
  return {
    e: Math.sqrt(f * (2 - f)),
    scale: utmScale * semiMajorAxis * (1 + n ** 2 / 4 + n ** 4 / 64) / (1 + n),
    forward: [n / 2 - 2 * n ** 2 / 3 + 5 * n ** 3 / 16 + 41 * n ** 4 / 180,
      13 * n ** 2 / 48 - 3 * n ** 3 / 5 + 557 * n ** 4 / 1440,
      61 * n ** 3 / 240 - 103 * n ** 4 / 140, 49561 * n ** 4 / 161280],
    inverse: [n / 2 - 2 * n ** 2 / 3 + 37 * n ** 3 / 96 - n ** 4 / 360,
      n ** 2 / 48 + n ** 3 / 15 - 437 * n ** 4 / 1440,
      17 * n ** 3 / 480 - 37 * n ** 4 / 840, 4397 * n ** 4 / 161280],
  }
}

const ellipsoids = { 'WGS 84': series(298.257223563), ETRS89: series(298.257222101) }

function transverseSeries(xi, eta, coefficients, sign) {
  let northing = xi
  let easting = eta
  coefficients.forEach((coefficient, index) => {
    const order = 2 * (index + 1)
    northing += sign * coefficient * Math.sin(order * xi) * Math.cosh(order * eta)
    easting += sign * coefficient * Math.cos(order * xi) * Math.sinh(order * eta)
  })
  return [northing, easting]
}

function wrapLongitude(degrees) {
  return degrees > 180 || degrees < -180 ? ((degrees + 540) % 360) - 180 : degrees
}

function checkGeographic(lon, lat) {
  if (![lon, lat].every(Number.isFinite) || Math.abs(lon) > 180 || Math.abs(lat) > 90) {
    throw new Error('Expected longitude (X) and latitude (Y) in decimal degrees.')
  }
}

function checkUtm(lon, lat, crs) {
  // Permit adjacent zones for local surveys spanning a zone boundary.
  const delta = wrapLongitude(lon - (6 * crs.zone - 183))
  const wrongHemisphere = crs.southern ? lat > 0.1 : lat < -0.1
  if (lat < -80.000000001 || lat > 84.000000001 || wrongHemisphere || Math.abs(delta) > 12.000000001) {
    throw new Error('Coordinates are outside the usable extent of EPSG:' + crs.epsg + '.')
  }
  return delta
}

// EPSG method 9807 (IOGP Guidance Note 7-2), UTM origin at the equator.
function forwardUtm(lon, lat, crs) {
  const lambda = checkUtm(lon, lat, crs) * rad
  const phi = lat * rad
  const { e, scale, forward } = ellipsoids[crs.datum]
  const q = Math.asinh(Math.tan(phi)) - e * Math.atanh(e * Math.sin(phi))
  const beta = Math.atan(Math.sinh(q))
  const eta = Math.atanh(Math.cos(beta) * Math.sin(lambda))
  const xi = Math.atan2(Math.tan(beta), Math.cos(lambda))
  const [northing, easting] = transverseSeries(xi, eta, forward, 1)
  return [falseEasting + scale * easting, (crs.southern ? falseSouthNorthing : 0) + scale * northing]
}

function inverseUtm(x, y, crs) {
  const localNorthing = y - (crs.southern ? falseSouthNorthing : 0)
  if (Math.abs(x - falseEasting) > 1500000 || localNorthing < -8900000 || localNorthing > 9400000) {
    throw new Error('Expected UTM easting (X) and northing (Y) in metres.')
  }
  const { e, scale, inverse } = ellipsoids[crs.datum]
  const [xi, eta] = transverseSeries(localNorthing / scale, (x - falseEasting) / scale, inverse, -1)
  const beta = Math.asin(Math.sin(xi) / Math.cosh(eta))
  const q0 = Math.asinh(Math.tan(beta))
  let q = q0
  for (let i = 0; i < 8; i++) q = q0 + e * Math.atanh(e * Math.tanh(q))
  const lat = Math.atan(Math.sinh(q)) / rad
  const lon = wrapLongitude(6 * crs.zone - 183 + Math.atan2(Math.sinh(eta), Math.cos(xi)) / rad)
  checkUtm(lon, lat, crs)
  return [lon, lat]
}

function toGeographic(x, y, crs) {
  if (crs.projection === 'utm') return inverseUtm(x, y, crs)
  if (crs.projection === 'web-mercator') {
    if (Math.abs(x) > Math.PI * semiMajorAxis + 1e-8 || Math.abs(y) > mercatorMaxNorthing) {
      throw new Error('Coordinates are outside the usable extent of Web Mercator.')
    }
    return [Math.max(-180, Math.min(180, x / (semiMajorAxis * rad))),
      Math.atan(Math.sinh(y / semiMajorAxis)) / rad]
  }
  checkGeographic(x, y)
  return [x, y]
}

export function datumNote(from, to) {
  const source = describeCrs(from)
  const target = describeCrs(to)
  return source && target && source.datum !== target.datum
    ? 'The WGS 84 and ETRS89 conversion uses EPSG:1149. Its stated datum accuracy is 1 m within its European area of use.'
    : ''
}

/**
 * Reproject an X/Y position. Geographic input and output always use longitude
 * first. WGS 84 ↔ ETRS89 uses the EPSG:1149 approximate datum equivalence.
 */
export function reprojectPoint(x, y, from, to) {
  const source = describeCrs(from)
  const target = describeCrs(to)
  if (!source || !target) throw new Error('Choose supported EPSG codes for both coordinate systems.')
  if (![x, y].every(Number.isFinite)) throw new Error('Coordinates must be finite numbers.')
  const [lon, lat] = toGeographic(x, y, source)
  if (source.epsg === target.epsg) return [x, y]
  if (target.projection === 'utm') return forwardUtm(lon, lat, target)
  if (target.projection === 'web-mercator') {
    if (Math.abs(lat) > mercatorMaxLatitude) throw new Error('Latitude is outside the Web Mercator extent.')
    return [semiMajorAxis * lon * rad, semiMajorAxis * Math.asinh(Math.tan(lat * rad))]
  }
  return [lon, lat]
}

export function reprojectSurvey(survey, to) {
  const from = parseEpsg(survey.epsg)
  const epsg = parseEpsg(to)
  if (!describeCrs(from) || !describeCrs(epsg)) {
    throw new Error('Select a supported coordinate system for the survey and video before reprojecting.')
  }
  let omitted = 0
  const rows = []
  for (const row of survey.rows) {
    if (![row.x, row.y].every(Number.isFinite)) {
      omitted++
      continue
    }
    try {
      const [x, y] = reprojectPoint(row.x, row.y, from, epsg)
      rows.push({ ...row, x, y, sourceX: row.sourceX ?? row.x, sourceY: row.sourceY ?? row.y })
    } catch (error) {
      throw new Error('Survey point ' + (row.name ?? row.id ?? rows.length + 1) + ': ' + error.message)
    }
  }
  if (!rows.length) throw new Error('No survey points have usable coordinates.')
  const warnings = [...(survey.warnings ?? [])]
  if (omitted) warnings.push(omitted + ' survey rows had missing coordinates and were skipped.')
  const note = datumNote(from, epsg)
  if (note) warnings.push(note)
  return { ...survey, epsg, sourceEpsg: survey.sourceEpsg ?? from, rows, warnings }
}

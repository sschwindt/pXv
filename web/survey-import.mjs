import { parseEpsg } from './spatial-reference.mjs'

const aliases = {
  x: ['x', 'easting', 'longitude', 'lon', 'lng', 'x_m', 'utm_easting', 'utm_x'],
  y: ['y', 'northing', 'latitude', 'lat', 'y_m', 'utm_northing', 'utm_y'],
  speed: ['surface_speed_m_s', 'surface_velocity_m_s', 'surface_m_s', 'speed_m_s', 'speed_mps', 'velocity_m_s',
    'speed', 'velocity', 'magnitude', 'speed_m_s_1'],
  u: ['u', 'vx', 'v_x_m_s', 'u_m_s', 'east_m_s', 'east_velocity', 'velocity_x', 'v_east'],
  v: ['v', 'vy', 'v_y_m_s', 'v_m_s', 'north_m_s', 'north_velocity', 'velocity_y', 'v_north'],
  sigma: ['sigma', 'sigma_m_s', 'uncertainty', 'uncertainty_m_s', 'standard_error_m_s', 'working_uncertainty_m_s'],
  measurementDepth: ['measurementDepth', 'measurement_depth_m', 'meas_depth_m', 'measurement_depth', 'depth_below_surface_m'],
  totalDepth: ['totalDepth', 'total_depth_m', 'water_depth_m', 'total_depth', 'depth_m'],
  surface: ['surface', 'is_surface', 'surface_measurement', 'measurement_type'],
  id: ['id', 'fid', 'point_id', 'station_id'],
  name: ['name', 'point_name', 'station', 'station_name'],
}

const normalize = text => text.trim().toLowerCase().replace(/[^a-z0-9]/g, '')

function defaultMapping(columns) {
  return Object.fromEntries(Object.entries(aliases).map(([key, candidates]) => [
    key,
    candidates.map(normalize).map(name => columns.find(column => normalize(column) === name)).find(Boolean) ?? '',
  ]))
}

function parseDelimited(text, delimiter, limit = Infinity) {
  const rows = []
  let row = []
  let value = ''
  let quoted = false
  let justClosed = false
  for (let i = 0; i < text.length; i++) {
    const char = text[i]
    if (quoted) {
      if (char !== '"') value += char
      else if (text[i + 1] === '"') {
        value += '"'
        i++
      } else {
        quoted = false
        justClosed = true
      }
    } else if (char === '"' && !value && !justClosed) {
      quoted = true
    } else if (char === delimiter || char === '\r' || char === '\n') {
      row.push(value)
      value = ''
      justClosed = false
      if (char === delimiter) continue
      if (char === '\r' && text[i + 1] === '\n') i++
      if (row.some(cell => cell.trim())) rows.push(row)
      if (rows.length >= limit) return rows
      row = []
    } else if (justClosed && char.trim()) {
      throw new Error('Unexpected text after a quoted value in survey CSV.')
    } else if (!justClosed) {
      value += char
    }
  }
  if (quoted) throw new Error('Survey CSV contains an unfinished quoted field.')
  row.push(value)
  if (row.some(cell => cell.trim())) rows.push(row)
  return rows
}

function inspectCsv(text) {
  const delimiters = [',', ';', '\t'].map(delimiter => {
    try {
      return { delimiter, length: parseDelimited(text, delimiter, 1)[0]?.length ?? 0 }
    } catch {
      return { delimiter, length: 0 }
    }
  })
  const { delimiter, length } = delimiters.reduce((best, entry) => entry.length > best.length ? entry : best)
  if (length < 2) throw new Error('Survey CSV requires a header and comma, semicolon or tab separated columns.')
  const [header, ...data] = parseDelimited(text, delimiter)
  const columns = header.map(column => column.trim())
  if (columns.some(column => !column) || new Set(columns).size !== columns.length) {
    throw new Error('Survey CSV must have a unique name for every column.')
  }
  if (data.some(row => row.length !== columns.length)) {
    throw new Error('Every survey CSV row must have the same number of fields as the header.')
  }
  const records = data.map(row => ({ properties: Object.fromEntries(columns.map((column, i) => [column, row[i]])) }))
  const crsColumn = columns.find(column => ['epsg', 'crs', 'srid'].includes(normalize(column)))
  const values = crsColumn ? [...new Set(data.map(row => row[columns.indexOf(crsColumn)].trim()).filter(Boolean))] : []
  const codes = new Set(values.map(parseEpsg))
  if (codes.size > 1) throw new Error('Survey CSV declares more than one coordinate system. Use one CRS per file.')
  const epsg = [...codes][0] ?? null
  const warnings = values.length && !epsg ? ['The survey CSV CRS value is unrecognized. Enter its EPSG code.'] : []
  return { format: 'csv', epsg, columns, records, warnings, skipped: 0, decimalComma: delimiter !== ',' }
}

function inspectJson(data) {
  let records
  let format
  let skipped = 0
  let declaredCrs
  if (Array.isArray(data?.rows)) {
    format = 'survey-json'
    records = data.rows.filter(row => row && typeof row === 'object' && !Array.isArray(row))
      .map(row => ({ properties: row }))
    skipped = data.rows.length - records.length
    declaredCrs = data.epsg
  } else if (data?.type === 'FeatureCollection' || data?.type === 'Feature') {
    format = 'geojson'
    const features = data.type === 'Feature' ? [data] : data.features
    if (!Array.isArray(features)) throw new Error('GeoJSON FeatureCollection must contain a features array.')
    records = features.filter(feature => feature?.type === 'Feature' && feature.geometry?.type === 'Point')
      .map(feature => ({ coordinates: feature.geometry.coordinates, properties: feature.properties ?? {}, id: feature.id }))
    skipped = features.length - records.length
    if (Object.hasOwn(data, 'epsg')) declaredCrs = data.epsg
    else if (Object.hasOwn(data, 'crs')) declaredCrs = data.crs?.properties?.name ?? data.crs?.properties?.code ?? null
    else declaredCrs = 4326 // RFC 7946 coordinates are WGS 84, longitude first.
  } else {
    throw new Error('Choose a GeoJSON point feature file or JSON created by the pXv survey exporter.')
  }
  const columns = [...new Set(records.flatMap(record => Object.keys(record.properties)))]
  const epsg = parseEpsg(declaredCrs)
  const warnings = declaredCrs != null && !epsg ? ['The survey JSON CRS value is unrecognized. Enter its EPSG code.'] : []
  return { format, epsg, columns, records, warnings, skipped, decimalComma: false }
}

export function inspectSurvey(source, { filename = '' } = {}) {
  let info
  if (typeof source === 'string') {
    const text = source.trim()
    if (!text) throw new Error('The survey file is empty.')
    if (/\.(json|geojson)$/i.test(filename) || /^[{\[]/.test(text)) {
      let data
      try { data = JSON.parse(text) } catch { throw new Error('The survey file is not valid JSON.') }
      info = inspectJson(data)
    } else {
      info = inspectCsv(text)
    }
  } else {
    info = inspectJson(source)
  }
  if (!info.records.length) throw new Error('The survey file has no point records.')
  return { ...info, mapping: defaultMapping(info.columns) }
}

function number(value, decimalComma) {
  if (typeof value === 'number') return Number.isFinite(value) ? value : null
  if (typeof value !== 'string' || !value.trim()) return null
  const text = decimalComma ? value.trim().replace(',', '.') : value.trim()
  if (!/^[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?$/.test(text)) return null
  const numeric = Number(text)
  return Number.isFinite(numeric) ? numeric : null
}

function surfaceFlag(value) {
  if (value === true || value === 1) return true
  if (value === false || value === 0) return false
  if (typeof value !== 'string') return null
  const flag = normalize(value)
  if (['true', 'yes', '1', 'surface', 'surfacevelocity'].includes(flag)) return true
  if (['false', 'no', '0', 'depth', 'subsurface', 'belowsurface'].includes(flag)) return false
  return null
}

/** Normalize survey positions, velocities in m/s, and optional depth and uncertainty. */
export function parseSurvey(source, { filename = '', epsg = null, measurement = 'auto', mapping = {} } = {}) {
  const info = Array.isArray(source?.records) && source.mapping ? source : inspectSurvey(source, { filename })
  const fields = { ...info.mapping, ...mapping }
  if (!['auto', 'surface', 'depth'].includes(measurement)) throw new Error('Choose how the survey velocities were measured.')
  for (const field of Object.values(fields)) {
    if (field && !info.columns.includes(field)) throw new Error('Survey column "' + field + '" is missing.')
  }
  if (info.format !== 'geojson' && (!fields.x || !fields.y)) {
    throw new Error('Assign X and Y survey columns. Use longitude for X and latitude for Y when in degrees.')
  }
  if (!fields.speed && (!fields.u || !fields.v)) {
    throw new Error('Assign a speed column or both velocity component columns in m/s.')
  }
  const override = epsg != null && epsg !== ''
  const sourceEpsg = override ? parseEpsg(epsg) : info.epsg
  if (override && !sourceEpsg) throw new Error('Enter a valid EPSG code for the survey.')
  const rows = []
  let skipped = info.skipped
  let missingMeasurement = 0
  for (const [index, record] of info.records.entries()) {
    const { coordinates, properties, id: featureId } = record
    const get = key => fields[key] ? properties[fields[key]] : null
    const numeric = key => number(get(key), info.decimalComma)
    const x = info.format === 'geojson' ? number(coordinates?.[0], false) : numeric('x')
    const y = info.format === 'geojson' ? number(coordinates?.[1], false) : numeric('y')
    const u = numeric('u')
    const v = numeric('v')
    const magnitude = numeric('speed')
    const hasComponents = u !== null && v !== null
    const speed = magnitude !== null && magnitude >= 0 ? magnitude : hasComponents ? Math.hypot(u, v) : null
    if (x === null || y === null || speed === null) {
      skipped++
      continue
    }
    const totalDepth = numeric('totalDepth')
    const measurementDepth = numeric('measurementDepth')
    const sigma = numeric('sigma')
    const flag = surfaceFlag(get('surface'))
    const depthPresent = measurementDepth !== null && totalDepth !== null
    const surface = measurement === 'surface' || (measurement === 'auto' &&
      (flag ?? (normalize(fields.speed ?? '').startsWith('surface') || (depthPresent && measurementDepth === 0))))
    if (measurement === 'auto' && flag === null && !depthPresent && !surface) {
      missingMeasurement++
      continue
    }
    if (!surface && (!depthPresent || measurementDepth < 0 || measurementDepth >= totalDepth)) {
      skipped++
      continue
    }
    rows.push({
      id: get('id') ?? featureId ?? index + 1,
      name: get('name') ?? null,
      x, y, u: hasComponents ? u : null, v: hasComponents ? v : null, speed,
      sigma: sigma !== null && sigma >= 0 ? sigma : null,
      measurementDepth: surface ? 0 : measurementDepth,
      totalDepth: totalDepth > 0 ? totalDepth : null,
      surface,
    })
  }
  if (missingMeasurement) {
    const error = new Error('Specify whether the survey speeds are at the surface or measured below it.')
    error.code = 'SURVEY_MEASUREMENT_REQUIRED'
    throw error
  }
  if (!rows.length) {
    throw new Error('No usable survey points were found. Check the position, velocity and measurement depth fields.')
  }
  const warnings = [...info.warnings]
  if (!sourceEpsg) warnings.push('The survey has no declared coordinate system. Choose the EPSG code from its source.')
  if (override && info.epsg && sourceEpsg !== info.epsg) {
    warnings.push('EPSG:' + sourceEpsg + ' was assigned to coordinates declared as EPSG:' + info.epsg + ' in the file.')
  }
  if (skipped) warnings.push(skipped + ' records without usable point, velocity or depth data were skipped.')
  if (rows.some(row => row.u !== null)) {
    warnings.push('Velocity components are used for speed and within-profile checks. Coordinate reprojection moves point positions.')
  }
  return { epsg: sourceEpsg, rows, warnings, skipped, columns: info.columns, mapping: fields, format: info.format }
}

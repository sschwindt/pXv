import assert from 'node:assert/strict'
import test from 'node:test'
import { autoUtmEpsg, datumNote, describeCrs, parseEpsg, reprojectPoint, reprojectSurvey } from './spatial-reference.mjs'
import { inspectSurvey, parseSurvey } from './survey-import.mjs'

function near(actual, expected, tolerance) {
  assert.ok(Math.abs(actual - expected) <= tolerance, actual + ' outside tolerance of ' + expected)
}

test('UTM and Web Mercator agree with the published PROJ examples in both hemispheres', () => {
  // https://proj.org/en/stable/usage/quickstart.html
  const etrs = reprojectPoint(12, 56, 4326, 25832)
  near(etrs[0], 687071.44, 0.01)
  near(etrs[1], 6210141.33, 0.01)
  // https://proj.org/en/stable/operations/projections/utm.html
  const south = reprojectPoint(174, -44, 4326, 32759)
  near(south[0], 740526.32, 0.01)
  near(south[1], 5123750.87, 0.01)
  const [longitude, latitude] = reprojectPoint(...south, 32759, 4326)
  near(longitude, 174, 1e-8)
  near(latitude, -44, 1e-8)
  // https://proj.org/en/stable/operations/projections/webmerc.html
  const mercator = reprojectPoint(2, 49, 4326, 3857)
  near(mercator[0], 222638.98, 0.01)
  near(mercator[1], 6274861.39, 0.01)
  assert.equal(autoUtmEpsg(59, 4), 32632)
  assert.equal(autoUtmEpsg(76, 20), 32633)
  assert.equal(autoUtmEpsg(-44, 173.9), 32759)
})

test('survey reprojection preserves position provenance and reports invalid coordinates', () => {
  assert.equal(parseEpsg('urn:ogc:def:crs:EPSG::25832'), 25832)
  assert.equal(parseEpsg('urn:ogc:def:crs:OGC:1.3:CRS84'), 4326)
  assert.equal(describeCrs(27700), null)
  assert.match(datumNote(4258, 4326), /EPSG:1149/)
  const survey = { epsg: 4326, rows: [{ id: 'A', x: 11.35, y: 47.54, speed: 1.2 }] }
  const projected = reprojectSurvey(survey, 25832)
  assert.equal(projected.sourceEpsg, 4326)
  assert.equal(projected.rows[0].sourceX, 11.35)
  assert.equal(projected.rows[0].sourceY, 47.54)
  assert.equal(projected.rows[0].speed, 1.2)
  assert.equal(survey.rows[0].x, 11.35)
  assert.throws(() => reprojectSurvey(survey, 27700), /supported coordinate system/)
  assert.throws(() => reprojectSurvey({ epsg: 4326, rows: [{ id: 'B', x: 700000, y: 4000000 }] }, 25832),
    /Survey point B.*decimal degrees/)
})

test('CSV allows explicit coordinate and velocity mapping, quoted text, and decimal commas', () => {
  const inspection = inspectSurvey('E Koord;N Koord;Messwert;Messfehler;Station;EPSG\r\n' +
    '677000,2;5267900,3;1,45;0,03;"A; Ufer";25832\r\n' +
    '677002;5267901;;0,04;"B";25832')
  assert.equal(inspection.epsg, 25832)
  const mapping = { x: 'E Koord', y: 'N Koord', speed: 'Messwert', sigma: 'Messfehler' }
  assert.throws(() => parseSurvey(inspection, { mapping }), { code: 'SURVEY_MEASUREMENT_REQUIRED' })
  const survey = parseSurvey(inspection, { measurement: 'surface', mapping })
  assert.equal(survey.skipped, 1)
  assert.deepEqual(survey.rows[0], { id: 1, name: 'A; Ufer', x: 677000.2, y: 5267900.3, u: null, v: null,
    speed: 1.45, sigma: 0.03, measurementDepth: 0, totalDepth: null, surface: true })
  const noCrs = parseSurvey('x,y,surface_speed_m_s\n12,49,1.2')
  assert.equal(noCrs.epsg, null)
  assert.match(noCrs.warnings[0], /no declared coordinate system/)
})

test('GeoJSON imports longitude first and derives depth mode from FlowTracker fields', () => {
  const survey = parseSurvey({
    type: 'FeatureCollection',
    features: [{
      type: 'Feature', id: 'Station A',
      geometry: { type: 'Point', coordinates: [11.35, 47.54] },
      properties: { v_x_m_s: 0.9, v_y_m_s: 1.2, Meas_Depth_m: 0.1, Total_Depth_m: 0.4 },
    }, {
      type: 'Feature', geometry: { type: 'LineString', coordinates: [[11.35, 47.54], [11.35, 47.53]] },
    }],
  })
  assert.equal(survey.epsg, 4326)
  assert.equal(survey.skipped, 1)
  assert.equal(survey.rows[0].id, 'Station A')
  assert.equal(survey.rows[0].x, 11.35)
  assert.equal(survey.rows[0].measurementDepth, 0.1)
  assert.equal(survey.rows[0].surface, false)
  near(survey.rows[0].speed, 1.5, 1e-12)
})

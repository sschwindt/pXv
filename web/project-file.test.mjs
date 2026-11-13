import assert from 'node:assert/strict'
import test from 'node:test'
import { matchesProjectVideo, parseProject, serializeProject, videoDescriptor } from './project-file.mjs'

const footage = { name: 'river.mov', size: 24000000, lastModified: 1 }
const video = videoDescriptor(footage, false, { width: 1920, height: 1080, frameCount: 550, fps: 29.97 })
const project = {
  video,
  settings: {
    method: 'combined', start: 0, stop: 550, step: 2,
    scaleMode: 'control', offset: -2.1, metres: 0.025,
    threshold: 0.86, minArea: 20, maxArea: 400, travel: 12, cellSize: 24,
    minTrackFrames: 4, windowSize: 48, pivRadius: 8,
    limitSpeed: false, speedLimit: 3, filterPiv: true, pivLimit: 2, filterPtv: false, ptvLimit: 2,
    surveyMode: 'calibration', matchRadius: 1.5, allowReprojection: true,
    showWater: true, showTracks: false, showVectors: true, showSurvey: true, showRejected: false, showHelp: false,
    graphWidth: 2560, graphHeight: 1440, graphFormat: 'jpg', graphQuality: 92,
  },
  engineOptions: {
    include: [[[0, 0], [1200, 100], [500, 1000]]],
    exclude: [[[0, 0], [50, 0], [0, 50]]],
    staticPolygons: [[[0, 0], [100, 0], [0, 100]]],
    minSamples: 3, maxZeroLagRatio: 0.85, staticThreshold: 0, maxGapFrames: 0,
  },
  control: {
    image: [[5, 10], [10, 20], [20, 10]], world: [[677050, 5267980], [677055, 5267960], [677060, 5267980]],
    epsg: 25832, reference: 0,
  },
  captions: { name: 'river.srt', text: '00:00:00,000 --> 00:00:00,033\n[rel_alt: 35.21]' },
  survey: {
    name: 'river.csv', epsg: 4326, warnings: ['One depth is missing.'], skipped: 0,
    rows: [{ id: 1, name: 'Wässer', x: 11, y: 48, speed: 0, sigma: 0, measurementDepth: 0, totalDepth: null,
      sourceX: 11, sourceY: 48, surface: true }, { id: 'ptv2', x: null, y: null, u: null, v: null }],
    mapping: { x: 'longitude', y: 'latitude', speed: 'speed', v: '' },
    columns: ['longitude', 'latitude', 'speed'],
    format: 'csv',
  },
}

test('project files round-trip settings, masks, original survey readings, and georeferencing', () => {
  const content = serializeProject(project)
  const restored = parseProject(content)
  assert.deepEqual(restored, { format: 'pxv', version: 1, ...project })
  assert.notEqual(restored.engineOptions.include, project.engineOptions.include)
  assert.equal(matchesProjectVideo(restored.video, { ...footage, lastModified: 500 }), true)
  assert.equal(matchesProjectVideo(restored.video, { ...footage, size: footage.size - 1 }), false)
  assert.equal(matchesProjectVideo(restored.video, { ...footage, name: 'another.mov' }), false)
})

test('invalid versions, impossible frame selections, and unsupported embedded paths are refused', () => {
  assert.throws(() => parseProject('not JSON'), /not valid JSON/)
  const base = JSON.parse(serializeProject(project))
  assert.throws(() => parseProject(JSON.stringify({ ...base, version: 17 })), /unsupported/)
  assert.throws(() => serializeProject({ ...project, settings: { ...project.settings, stop: 551 } }), /settings.stop/)
  assert.throws(() => serializeProject({ ...project, video: { ...video, name: '../../other.mov' } }), /video.name/)
  assert.throws(() => serializeProject({ ...project, settings: { ...project.settings, start: 1 } }), /control points/)
  assert.throws(() => serializeProject({ ...project, settings: { ...project.settings, graphWidth: 8192, graphHeight: 8192 } }), /pixel/)
})

test('unknown properties stay out of loaded projects and optional data may be omitted', () => {
  const original = JSON.parse(serializeProject({
    ...project,
    captions: null, survey: null, control: null,
    settings: { ...project.settings, scaleMode: 'pixel', surveyMode: 'none' },
  }))
  Object.defineProperty(original.engineOptions, '__proto__', { value: { polluted: true }, enumerable: true })
  original.video.url = 'https://example.test/video.mov'
  delete original.settings.graphFormat
  delete original.settings.graphQuality
  const restored = parseProject(JSON.stringify(original))
  assert.equal(Object.hasOwn(restored.engineOptions, '__proto__'), false)
  assert.equal(Object.hasOwn(restored.video, 'url'), false)
  assert.equal(restored.survey, null)
  assert.equal(restored.captions, null)
  assert.equal(restored.control, null)
  assert.equal(restored.settings.graphFormat, 'webp')
  assert.equal(restored.settings.graphQuality, 86)
})

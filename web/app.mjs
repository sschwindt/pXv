import { cameraGeometry, controlPointGeometry, linearGeometry, parseDjiSrt } from './geometry.mjs'
import { analyzeSurvey, summarizeSurvey } from './survey.mjs'
import { isar } from './isar.mjs'
import { filterVectors } from './vector-filters.mjs'
import { createWorkbook } from './xlsx.mjs'
import { assessVelocity } from './calibration.mjs'
import { inspectAlignment, setupImportWizard } from './import-wizard.mjs'
import { autoUtmEpsg, describeCrs, reprojectPoint, reprojectSurvey } from './spatial-reference.mjs'
import { openMp4, verifyVideoDecoder } from './mp4.mjs'
import { matchesProjectVideo, parseProject, projectFileMaxBytes, serializeProject, videoDescriptor } from './project-file.mjs'
import { defaultGraphQuality, graphLimits, renderResultGraph, surveyColor, validateGraphSize } from './result-graph.mjs'

const $ = id => document.getElementById(id)
const controls = $('settings-form')
const canvas = $('overlay')
const context = canvas.getContext('2d')
const input = id => $(id).valueAsNumber
const method = () => controls.elements.method.value
const fixed = (value, digits = 2) => Number.isFinite(value) ? value.toFixed(digits) : '—'
const speedText = (value, units = 'm') => Number.isFinite(value) ? fixed(value) + ' ' + units + '/s' : '—'
const state = { generation: 0, busy: false, job: null, mask: null }
const projectNumbers = {
  start: 'start', stop: 'stop', step: 'step', offset: 'offset', metres: 'metres', threshold: 'threshold',
  minArea: 'area-min', maxArea: 'area-max', travel: 'travel', cellSize: 'cell', minTrackFrames: 'track-frames',
  windowSize: 'window-size', pivRadius: 'piv-radius', speedLimit: 'speed-limit', pivLimit: 'piv-limit',
  ptvLimit: 'ptv-limit', matchRadius: 'match-radius', graphWidth: 'graph-width', graphHeight: 'graph-height',
  graphQuality: 'graph-quality',
}
const projectChecks = {
  limitSpeed: 'limit-speed', filterPiv: 'filter-piv', filterPtv: 'filter-ptv',
  showWater: 'show-water', showTracks: 'show-tracks', showVectors: 'show-vectors',
  showSurvey: 'show-survey', showRejected: 'show-rejected', showHelp: 'show-help',
}

async function read(url, type) {
  const response = await fetch(url)
  if (!response.ok) throw new Error('Could not open ' + url.split('/').pop() + '.')
  return response[type]()
}

function status(message, error = false) {
  $('status').textContent = message
  $('status').classList.toggle('error', error)
}

function makeWorker(source) {
  state.worker?.terminate()
  const worker = new Worker('/analysis-worker.mjs', { type: 'module' })
  state.worker = worker
  worker.onmessage = event => { if (state.worker === worker) receive(event.data) }
  worker.onerror = event => {
    if (state.worker !== worker) return
    setBusy(false)
    status(event.message || 'The analysis worker stopped.', true)
  }
  worker.postMessage({ type: 'open', source })
}

function setBusy(busy) {
  state.busy = busy
  $('run').disabled = busy || !state.meta
  $('stop-run').hidden = !busy
  $('progress').hidden = !busy
  for (const id of ['open-import', 'review-import', 'edit-reference', 'load-project']) $(id).disabled = busy
  $('save-project').disabled = busy || !state.meta || !state.sourceReady
  $('export-graph').disabled = !graphAvailable()
  for (const field of controls.querySelectorAll('fieldset, details button, details input, details select')) {
    field.disabled = busy
  }
}

function updateControls() {
  const camera = $('scale-mode').value === 'camera'
  $('start').readOnly = $('scale-mode').value === 'control'
  $('control-option').disabled = !state.control
  $('camera-fields').hidden = !camera
  $('linear-field').hidden = $('scale-mode').value !== 'linear'
  $('ptv-settings').hidden = method() === 'piv'
  $('travel-field').hidden = method() === 'piv'
  $('track-length-field').hidden = method() === 'piv'
  $('piv-settings').hidden = method() === 'ptv'
  $('method-help').textContent = {
    ptv: 'Track bright surface tracers.',
    piv: 'Correlate local surface texture across frame pairs.',
    combined: 'Use tracer vectors where available and filtered PIV elsewhere.',
  }[method()]
  const window = state.meta && Number.isFinite(input('start')) && Number.isFinite(input('stop'))
    ? fixed(input('start') / state.meta.fps, 2) + '–' + fixed(input('stop') / state.meta.fps, 2) + ' s · stop excluded.'
    : 'Zero-based, stop excluded.'
  $('time-window').textContent = window
}

function telemetryFor(reference) {
  if (!state.captions) throw new Error('Open a DJI SRT or choose another scale mode.')
  if (state.telemetry?.reference !== reference) {
    const data = parseDjiSrt(state.captions, { startFrame: reference, endFrame: reference + 1 })
    state.telemetry = { reference, data }
  }
  return state.telemetry.data
}

function geometryFor(reference) {
  const mode = $('scale-mode').value
  if (mode === 'linear') return linearGeometry(input('metres'))
  if (mode === 'pixel') return linearGeometry()
  if (mode === 'control') {
    if (!state.controlGeometry) throw new Error('Enter ground control points through Import.')
    if (reference !== state.control.reference) throw new Error('Ground control points refer to frame ' + state.control.reference + '.')
    return state.controlGeometry
  }
  const telemetry = telemetryFor(reference)
  return cameraGeometry({ ...telemetry, width: state.meta.width, height: state.meta.height, waterOffset: input('offset') })
}

function currentGeometry() {
  if (!state.meta) return null
  try {
    return geometryFor(state.reference ?? input('start'))
  } catch {
    return null
  }
}

function updateScale(geometry) {
  const mode = $('scale-mode').value
  if (mode !== 'camera') {
    if (mode === 'control') {
      $('scale-help').textContent = geometry
        ? 'EPSG:' + geometry.epsg + ' · frame ' + state.control.reference + ' · ' + geometry.controlPointCount +
          ' points · ' + geometry.model + ' fit · residual ' + fixed(geometry.controlPointRmse, 3) + ' m at control points.'
        : 'Use the control points’ reference frame or edit their coordinates in Import.'
    } else {
      $('scale-help').textContent = mode === 'linear' ? 'Use a measured scale for a flat, nadir view.'
        : 'Velocity is reported in image pixels per second.'
    }
    return
  }
  let height
  try {
    const reference = state.reference ?? input('start')
    height = telemetryFor(reference).heightAboveTakeoff
  } catch {
    height = null
  }
  $('srt-height').value = Number.isFinite(height) ? fixed(height, 3) : ''
  if (geometry?.gsd) {
    const { bottom, top } = geometry.gsd
    $('scale-help').textContent = fixed(geometry.heightAboveWater, 2) + ' m above water · ' +
      fixed(Math.min(bottom, top) * 1000, 1) + '–' + fixed(Math.max(bottom, top) * 1000, 1) +
      ' mm/px along image centreline.'
  } else if (!state.captions) {
    $('scale-help').textContent = 'Load a DJI SRT with height, focal length and gimbal angles.'
  } else if (!Number.isFinite(height)) {
    $('scale-help').textContent = 'DJI height is unavailable at the reference frame.'
  } else if (!state.meta) {
    $('scale-help').textContent = 'Open the corresponding video to calculate the camera scale.'
  } else if (!(height + input('offset') > 0)) {
    $('scale-help').textContent = 'Enter an offset that puts the camera above the water.'
  } else {
    $('scale-help').textContent = 'Camera scale requires focal length and gimbal angles at the reference frame.'
  }
}

async function showPreview(blob, frame, generation) {
  try {
    const image = await createImageBitmap(blob)
    if (generation !== state.generation || frame !== state.reference) {
      image.close()
      return
    }
    state.image?.close()
    state.image = image
    state.imageFrame = frame
    $('image-placeholder').hidden = true
    $('frame-label').textContent = 'Reference frame ' + frame
    render()
  } catch {
    status('The reference frame could not be displayed.', true)
  }
}

function setSurvey(data) {
  if (!data || !Array.isArray(data.rows) || !data.rows.length ||
      data.rows.some(row => !row || typeof row !== 'object')) {
    throw new Error('Choose a survey with usable point readings.')
  }
  state.surveyData = data
  state.surveyCache = null
}

async function openSource(source, isExample, imported, project) {
  const generation = ++state.generation
  state.worker?.terminate()
  state.worker = null
  state.source = source
  state.example = isExample
  state.exampleVariant = project?.video.variant ?? null
  state.result = null
  state.meta = null
  state.sourceReady = !isExample || Boolean(imported)
  state.restoringProject = Boolean(project)
  state.reference = null
  state.captions = imported?.captions ?? null
  state.captionsName = imported?.captionsName ?? null
  state.telemetry = null
  state.surveyData = null
  state.surveyCache = null
  state.surveyImport = imported?.surveyInput ? { surveyInput: imported.surveyInput,
    file: imported.file, measurement: imported.measurement } : null
  state.control = imported?.georeference.control ?? null
  state.controlGeometry = state.control ? imported.georeference.geometry : null
  state.allowReprojection = imported?.allowReprojection ?? false
  $('survey-mode').value = imported?.surveyMode ?? 'validation'
  $('match-radius').value = imported?.radius ?? 1.5
  $('control-option').hidden = !state.control
  if (imported?.survey) setSurvey(imported.survey)
  state.job = null
  state.mask = null
  state.image?.close()
  state.image = null
  state.imageFrame = null
  state.engineOptions = structuredClone(isExample ? isar.options : {
    method: 'ptv', include: [], staticPolygons: [], minSamples: 3, minTrackFrames: 4,
  })
  $('start').value = state.control?.reference ?? (isExample ? isar.start : 0)
  $('stop').value = isExample ? isar.stop : 2
  $('step').value = 1
  $('scale-mode').value = imported?.georeference.mode ?? (isExample ? 'camera' : 'pixel')
  $('offset').value = imported?.georeference.offset ?? 0
  $('metres').value = imported?.georeference.metres ?? 0.025
  $('threshold').value = 0.86
  $('area-min').value = 20
  $('area-max').value = 400
  $('travel').value = 12
  $('cell').value = isExample ? 24 : 32
  $('window-size').value = 48
  $('piv-radius').value = 8
  $('track-frames').value = state.engineOptions.minTrackFrames
  $('limit-speed').checked = isExample
  $('speed-limit').value = 3
  $('filter-piv').checked = false
  $('piv-limit').value = 2
  $('filter-ptv').checked = false
  $('ptv-limit').value = 2
  $('show-rejected').checked = false
  controls.elements.method.value = 'ptv'
  $('show-water').checked = false
  $('graph-width').value = 1920
  $('graph-height').value = 1080
  $('graph-format').value = 'webp'
  $('graph-quality').value = Math.round(defaultGraphQuality * 100)
  if (project) applyProjectSettings(project)
  $('project-title').textContent = isExample ? 'Isar River' : source.name.replace(/\.(mp4|mov|m4v)$/i, '')
  $('project-subtitle').textContent = isExample ? '4 August 2026 · Seeded drone survey' : 'Local footage'
  $('video-meta').textContent = 'Opening footage…'
  $('image-placeholder').textContent = 'Loading reference frame…'
  $('image-placeholder').hidden = false
  $('export-survey').disabled = true
  $('frame-label').textContent = 'Reference frame'
  $('stop-run').hidden = true
  $('progress').hidden = true
  setMask(null)
  setBusy(false)
  status('')
  projectStatus(project ? 'Project loaded. Run analysis to regenerate the results.' : '')
  updateControls()
  render()
  if (isExample && !imported) {
    let config
    try {
      config = await read('/example/config', 'json')
    } catch (error) {
      if (generation === state.generation) status(error.message, true)
      return
    }
    if (generation !== state.generation) return
    state.exampleVariant = config.variant
    const query = '?variant=' + encodeURIComponent(config.variant)
    state.source = '/example/video' + query
    $('start').value = config.start
    if (config.stop !== null) $('stop').value = config.stop
    $('project-subtitle').textContent = '4 August 2026 · ' + (config.fixture ? 'Short seeded clip' : 'Seeded drone survey')
    makeWorker(state.source)
    const results = await Promise.allSettled([read('/example/captions' + query, 'text'), read('/example/survey', 'json')])
    if (generation !== state.generation) return
    if (results[0].status === 'fulfilled' && !state.captions) {
      state.captions = results[0].value
      state.captionsName = config.captionsName
    }
    if (results[1].status === 'fulfilled' && !state.surveyData) {
      try { setSurvey({ ...results[1].value, name: 'IsarAug2026.gpkg' }) } catch (error) { status(error.message, true) }
    }
    const missing = results.filter(result => result.status === 'rejected').map(result => result.reason.message)
    if (missing.length) status(missing.join(' '), true)
    state.sourceReady = true
    $('save-project').disabled = !state.meta || state.busy
    render()
  } else {
    if (state.exampleVariant === 'fixture') $('project-subtitle').textContent = '4 August 2026 · Short seeded clip'
    makeWorker(source)
  }
}

function receive(data) {
  if (data.type === 'opened') {
    const reopened = Boolean(state.meta)
    state.meta = data
    canvas.width = data.width
    canvas.height = data.height
    $('start').max = data.frameCount - 2
    $('stop').max = data.frameCount
    if (!state.example && !reopened && !state.restoringProject) $('stop').value = Math.min(data.frameCount, 150)
    if (state.exampleVariant === 'fixture' && !reopened && !state.restoringProject) $('stop').value = data.frameCount
    $('save-project').disabled = !state.sourceReady || state.busy
    $('video-meta').textContent = data.width + ' × ' + data.height + ' · ' + fixed(data.fps) +
      ' fps · ' + data.frameCount.toLocaleString() + ' frames'
    state.reference = input('start')
    state.worker.postMessage({ type: 'preview', frame: state.reference })
    updateControls()
    render()
    return
  }
  if (data.type === 'preview') {
    showPreview(data.preview, data.frame, state.generation)
    $('run').disabled = state.busy
    return
  }
  if (data.type === 'progress') {
    $('progress').value = data.completed / data.total
    status('Analyzing frame ' + data.completed + ' of ' + data.total + '…')
    return
  }
  if (data.type === 'result') {
    state.result = { ...data.result, ...state.job }
    state.reference = data.frame
    showPreview(data.preview, data.frame, state.generation)
    state.job = null
    setBusy(false)
    status('Analysis complete.')
    render()
    return
  }
  if (data.type === 'error') {
    setBusy(false)
    state.job = null
    status(data.message, true)
    if (!state.image) {
      $('image-placeholder').textContent = data.message
      if (!state.meta) $('video-meta').textContent = 'Footage unavailable'
    }
  }
}

function metricVectors(vectors, geometry) {
  return vectors.map(vector => {
    const [x, y] = geometry.toWorld(vector.x, vector.y)
    const dt = 0.01
    const [u, v] = geometry.displacement(vector.x, vector.y, vector.u * dt, vector.v * dt, dt)
    return { x, y, u, v, speed: Math.hypot(u, v), count: vector.count, trackCount: vector.trackCount,
      source: vector.source, pixel: vector }
  }).filter(vector => Number.isFinite(vector.speed))
}

function pointsInFrame(geometry) {
  if (!state.survey?.aligned || !geometry?.epsg || geometry.epsg !== state.survey.epsg) return null
  return state.survey.verticals.filter(point => {
    const [x, y] = geometry.toImage(point.x, point.y)
    return x >= 0 && y >= 0 && x < canvas.width && y < canvas.height
  })
}

function updateSurvey(geometry) {
  const data = state.surveyData
  const geometryKey = geometry?.epsg ? $('scale-mode').value + ':' + state.reference + ':' + input('offset') + ':' + geometry.epsg : null
  const cached = state.surveyCache
  if (cached?.data === data && cached.geometryKey === geometryKey && cached.allowReprojection === state.allowReprojection) {
    state.survey = cached.survey
    return cached.alignment
  }
  const alignment = inspectAlignment(data, geometry, state.meta)
  let survey = null
  if (data) {
    const useAligned = alignment.kind === 'same' || (alignment.kind === 'reproject' && state.allowReprojection)
    let mapped = useAligned ? alignment.aligned : data
    if (!useAligned && describeCrs(data.epsg) && describeCrs(data.epsg).projection !== 'utm') {
      const first = data.rows.find(row => Number.isFinite(row.x) && Number.isFinite(row.y))
      if (first) {
        try {
          const [lon, lat] = reprojectPoint(first.x, first.y, data.epsg, 4326)
          const local = autoUtmEpsg(lat, lon)
          if (local) mapped = reprojectSurvey(data, local)
        } catch {
          // Use exact coordinate grouping when local metric conversion is unavailable.
        }
      }
    }
    const projected = describeCrs(mapped.epsg)?.projection === 'utm'
    const analyzed = analyzeSurvey(mapped.rows, projected ? {} : { tolerance: 0 })
    survey = { ...analyzed, epsg: mapped.epsg, sourceEpsg: mapped.sourceEpsg ?? data.epsg, aligned: useAligned }
  }
  state.survey = survey
  state.surveyCache = { data, geometryKey, allowReprojection: state.allowReprojection, alignment, survey }
  return alignment
}

function surveyComparison(geometry, visible, filtered, options) {
  const radius = input('match-radius')
  const mode = state.surveyData ? $('survey-mode').value : 'none'
  const canMatch = geometry?.epsg && state.survey?.aligned && Number.isFinite(radius) && radius >= 0.01
  const assessment = filtered ? assessVelocity(filtered.accepted, canMatch ? visible ?? [] : [], {
    mode, radius: canMatch ? radius : 1.5,
  }) : null
  if (assessment && filtered) {
    const cap = options.maxSpeed
    const after = assessment.applied && cap != null ? filterVectors(assessment.vectors, { units: geometry.units, maxSpeed: cap }) : null
    filtered.accepted = after?.accepted ?? assessment.vectors
    if (after?.rejected.length) {
      filtered.rejected.push(...after.rejected.map(vector => ({ ...vector, reason: 'speed-after-calibration' })))
      filtered.counts.speed += after.counts.speed
      filtered.counts.rejected += after.counts.rejected
      filtered.counts.accepted = filtered.accepted.length
    }
  }
  return assessment
}

function showSurveyStatus(alignment, assessment, visibleSurvey) {
  const banner = $('survey-banner')
  const mode = $('survey-mode').value
  const applied = alignment.kind === 'reproject' && state.allowReprojection
  let warning = ''
  if (!state.surveyData) warning = alignment.message
  else if (!state.survey?.aligned) warning = 'Video velocities cannot be validated or calibrated. ' + alignment.message
  else if (!visibleSurvey?.some(point => Number.isFinite(point.speed))) {
    warning = 'Video velocities cannot be validated or calibrated. No usable survey estimates lie in the video footprint.'
  } else if (assessment?.status === 'no-overlap') {
    warning = 'Video velocities could not be validated or calibrated. No measured cells matched the survey within the selected radius.'
  }
  $('survey-banner-text').textContent = warning
  banner.hidden = !warning
  $('survey-controls').hidden = !state.surveyData
  $('survey-alignment').textContent = applied
    ? alignment.message.replace('Reproject ', 'Reprojected ')
    : alignment.message
  $('export-survey').disabled = !state.survey
  $('match-radius').disabled = mode === 'none'
  const messages = []
  if (state.surveyData && mode === 'none') messages.push('Reference only. The survey does not change video speeds.')
  if (mode !== 'none' && !$('match-radius').disabled && (!(input('match-radius') >= 0.01))) {
    messages.push('Enter a match radius of at least 0.01 m.')
  }
  if (assessment?.status === 'insufficient-calibration' || assessment?.status === 'partial-calibration') {
    messages.push((assessment.applied ? 'Some methods could not be calibrated. ' : 'Calibration could not be applied. ') +
      'Each method needs at least five fit locations and three spatially separate validation locations.')
  }
  if (mode === 'calibration' && assessment?.calibration.sharedProfileCount) {
    const count = assessment.calibration.sharedProfileCount
    messages.push(count + (count === 1 ? ' matched reference estimate uses' : ' matched reference estimates use') +
      ' shared roughness. These estimates support local comparison.')
  }
  for (const [source, evidence] of Object.entries(assessment?.bySource ?? {})) {
    if (evidence.gain == null) continue
    messages.push(source.toUpperCase() + ' ×' + fixed(evidence.gain, 3) + ' from ' + evidence.trainingCount +
      ' fit locations. Held-out RMSE ' + fixed(evidence.baseline.rmse, 3) + ' → ' + fixed(evidence.validation.rmse, 3) + ' m/s.' +
      (evidence.validation.rmse > evidence.baseline.rmse ? ' The multiplier increased held-out error.' : ''))
  }
  $('calibration-summary').textContent = messages.join(' ')
}

function filterSettings(geometry) {
  const result = state.result
  const selectedMethod = result?.method ?? method()
  const metric = geometry?.units === 'm'
  $('limit-speed').disabled = !metric
  $('speed-limit').disabled = !metric || !$('limit-speed').checked
  $('piv-filter-row').hidden = selectedMethod === 'ptv'
  $('ptv-filter-row').hidden = selectedMethod === 'piv'
  $('piv-limit').disabled = !$('filter-piv').checked
  $('ptv-limit').disabled = !$('filter-ptv').checked
  const field = id => !$(id).disabled && $(id).validity.valid && $(id).value !== '' ? input(id) : null
  return {
    units: geometry?.units ?? 'px',
    maxSpeed: field('speed-limit'),
    pivThreshold: selectedMethod === 'ptv' ? null : field('piv-limit'),
    ptvMinTracks: selectedMethod === 'piv' ? null : field('ptv-limit'),
    cellSize: result?.cellSize ?? 32,
    pivGridStep: result?.pivGridStep,
    frameInterval: result ? result.step / state.meta.fps : null,
    include: result?.include ?? [],
    exclude: result?.exclude ?? [],
  }
}

function updateFilterStatus(geometry, options, counts) {
  $('show-rejected').disabled = !counts?.rejected
  if (!counts) {
    $('filter-status').textContent = geometry?.units === 'm'
      ? 'Change filters after a run to preview the effect.'
      : 'Select a metric scale to use the maximum speed filter.'
    return
  }
  const byReason = [
    [counts.speed, 'speed'], [counts.pivMedian, 'PIV median'], [counts.ptvSupport, 'PTV support'],
  ].filter(([count]) => count > 0).map(([count, name]) => count + ' ' + name)
  const messages = [counts.accepted + ' of ' + counts.total + ' vectors kept.' +
    (byReason.length ? ' Excluded by ' + byReason.join(', ') + '.' : '')]
  if (options.pivThreshold !== null && counts.pivSparse) messages.push(counts.pivSparse + ' PIV cells had too few suitable neighbours.')
  if (counts.ptvUnknown) messages.push(counts.ptvUnknown + ' PTV cells have unknown track support.')
  if (geometry?.units !== 'm') messages.push('A metric scale is needed for the speed limit.')
  if (['speed-limit', 'piv-limit', 'ptv-limit'].some(id => !$(id).disabled && (!$(id).validity.valid || $(id).value === ''))) {
    messages.push('Enter a valid threshold to apply its filter.')
  }
  $('filter-status').textContent = messages.join(' ')
}

function resultMetrics(geometry, visibleSurvey, filtered, assessment) {
  const result = state.result
  const projected = filtered?.accepted ?? []
  const preferTracers = projected.some(vector => vector.source === 'ptv')
  const selected = preferTracers ? projected.filter(vector => vector.source === 'ptv') : projected
  const video = summarizeSurvey(selected)
  const survey = visibleSurvey === null ? state.survey?.summary : summarizeSurvey(visibleSurvey)
  const matches = assessment?.applied ? assessment.validation : assessment?.comparison
  $('video-speed-label').textContent = preferTracers ? 'Tracer median' : 'Video median'
  $('video-speed').textContent = speedText(video.median, geometry?.units)
  $('video-detail').textContent = video.count
    ? video.count + ' cells · P10–P90 ' + fixed(video.p10) + '–' + fixed(video.p90) + ' ' + geometry.units + '/s'
    : filtered?.counts.rejected ? 'The current filters excluded every vector.'
      : result ? 'No vectors are available with the current scale and motion checks.' : 'Run an analysis to measure surface motion.'
  $('survey-scope').textContent = visibleSurvey === null ? 'Survey median' : 'Survey median in frame'
  $('survey-speed').textContent = speedText(survey?.median)
  $('survey-detail').textContent = survey?.count
    ? survey.count + ' verticals · P10–P90 ' + fixed(survey.p10) + '–' + fixed(survey.p90) + ' m/s'
    : state.survey ? 'No surface estimates in this footprint.' : 'Add ground truth through Import.'
  $('match-error').textContent = speedText(matches?.rmse)
  $('match-detail').textContent = matches?.count
    ? assessment?.applied
      ? 'Held-out RMSE · ' + matches.count + ' matches · before calibration ' + fixed(assessment.baseline.rmse) + ' m/s'
      : matches.count === 1
      ? 'Absolute difference · 1 of ' + matches.total + ' within ' + matches.radius + ' m'
      : 'RMSE · ' + matches.count + ' of ' + matches.total + ' within ' + matches.radius + ' m · bias ' + fixed(matches.bias) + ' m/s'
    : assessment?.status === 'none' ? 'Survey comparison is off.'
      : assessment?.status === 'no-overlap' ? 'No measured cells within ' + assessment.radius + ' m.'
      : 'Pairs nearby measured vectors with survey estimates.'
  const notes = []
  if (state.survey && (state.survey.diagnostics.localFits || state.survey.diagnostics.siteFits)) {
    const { roughness } = state.survey
    notes.push('Survey estimates use a logarithmic profile with measured depths below the surface.' +
      (roughness.estimate ? ' Shared roughness is ' + fixed(roughness.estimate * 1000, 1) + ' mm.' : ''))
  }
  if (state.example && geometry?.gsd && $('scale-mode').value === 'camera' && input('offset') === 0) {
    notes.push('Camera scale uses the SRT height and assumes takeoff was level with the water.')
  }
  if (matches?.count === 1) {
    notes.push('One overlap gives a point comparison. More spatial overlap is needed for validation.')
  }
  if (state.example && (result?.method ?? method()) !== 'ptv') {
    notes.push('The visible gravel bed can bias Isar PIV speeds low.')
  }
  if (state.example && !result) notes.push('The preset follows the injected tracer pulse.')
  $('result-note').textContent = notes.join(' ')
  $('result-note').hidden = !notes.length
  $('export-vectors').disabled = !projected.length
  $('export-xlsx').disabled = !filtered?.counts.total && !state.survey
  $('export-graph').disabled = !graphAvailable()
  const diag = result?.diagnostics
  $('diagnostics').hidden = !diag
  if (diag) {
    const items = [
      ['Decoded frames', diag.frameCount],
      ['Tracer detections', diag.detectionCount],
      ['Accepted tracks', diag.trackCount],
      ['Tracer displacement samples', diag.ptvSampleCount],
      ['PIV frame pairs', diag.pivPairCount],
      ['PIV static cells', diag.pivStaticCells],
      ['PIV rejected cells', diag.pivRejectedCells],
      ['Camera stabilization', diag.stabilizationMode === 'bank' ? 'Static bank patches' : 'Off'],
      ['Bank residual', Number.isFinite(diag.stabilizationResidualPx) ? fixed(diag.stabilizationResidualPx, 3) + ' px' : null],
      ['Bank alignment failures', diag.stabilizationFailures],
    ].filter(([, value]) => value !== null && value !== undefined)
    $('diagnostic-list').replaceChildren(...items.flatMap(([label, value]) => {
      const term = document.createElement('dt')
      term.textContent = label
      const detail = document.createElement('dd')
      detail.textContent = value
      return [term, detail]
    }))
  }
  return projected
}

function polygon(points, color, fill = false, close = true) {
  if (!points.length) return
  context.beginPath()
  context.moveTo(...points[0])
  for (const point of points.slice(1)) context.lineTo(...point)
  if (close) context.closePath()
  context.strokeStyle = color
  context.stroke()
  if (fill) {
    context.globalAlpha = 0.13
    context.fillStyle = color
    context.fill()
    context.globalAlpha = 1
  }
}

function drawOverlay(geometry, visibleSurvey, vectors, rejected) {
  context.clearRect(0, 0, canvas.width, canvas.height)
  if (state.image) context.drawImage(state.image, 0, 0)
  const line = Math.max(1, canvas.width / 720)
  context.lineWidth = line
  if ($('show-water').checked) {
    for (const mask of state.engineOptions.include) polygon(mask, '#b9ef9c', true)
    for (const mask of state.engineOptions.staticPolygons) polygon(mask, '#ffe1ac', true)
  }
  if (state.mask) {
    polygon(state.mask.points, '#fff5bb', false, false)
    for (const [x, y] of state.mask.points) context.fillRect(x - line, y - line, line * 2, line * 2)
  }
  if (state.result && $('show-tracks').checked) {
    context.lineWidth = line * 0.85
    for (const track of state.result.tracks) polygon(track.points.map(point => [point.x, point.y]), '#d6f9a9', false, false)
  }
  const scale = vectors.length ? summarizeSurvey(vectors).p90 || 1 : 1
  $('vector-scale').hidden = !vectors.length || !$('show-vectors').checked
  if (vectors.length && $('show-vectors').checked) {
    $('scale-end').textContent = speedText(scale, geometry.units)
    context.lineWidth = line * 1.35
    for (const vector of vectors) {
      const { x, y, u, v } = vector.pixel
      const length = Math.min(1.3, vector.speed / scale) * Math.max(15, canvas.width / 67)
      const angle = Math.atan2(v, u)
      const end = [x + Math.cos(angle) * length, y + Math.sin(angle) * length]
      const color = 'hsl(' + (179 - 127 * Math.min(1, vector.speed / scale)) + ' 86% 69%)'
      polygon([[x, y], end], color, false, false)
      const head = Math.min(line * 3, length * .45)
      polygon([-.55, 0, .55].map((turn, i) => i === 1 ? end :
        [end[0] - Math.cos(angle + turn) * head, end[1] - Math.sin(angle + turn) * head]), color, false, false)
    }
  }
  if ($('show-rejected').checked && !$('show-rejected').disabled) {
    context.lineWidth = line * 1.5
    const radius = line * 2
    for (const { pixel: { x, y } } of rejected) {
      polygon([[x - radius, y - radius], [x + radius, y + radius]], '#ffb195', false, false)
      polygon([[x + radius, y - radius], [x - radius, y + radius]], '#ffb195', false, false)
    }
  }
  if ($('show-survey').checked && visibleSurvey) {
    for (const point of visibleSurvey) {
      const [x, y] = geometry.toImage(point.x, point.y)
      const r = line * 4
      context.lineWidth = line * 1.5
      polygon([[x, y - r], [x + r, y], [x, y + r], [x - r, y]], '#301126')
      context.fillStyle = surveyColor
      context.fill()
    }
  }
}

function render() {
  const geometry = currentGeometry()
  updateScale(geometry)
  const alignment = updateSurvey(geometry)
  const visibleSurvey = pointsInFrame(geometry)
  const options = filterSettings(geometry)
  const filtered = state.result && geometry ? filterVectors(metricVectors(state.result.vectors, geometry), options) : null
  const assessment = surveyComparison(geometry, visibleSurvey, filtered, options)
  state.view = { geometry, options, filtered, assessment }
  showSurveyStatus(alignment, assessment, visibleSurvey)
  updateFilterStatus(geometry, options, filtered?.counts)
  const vectors = resultMetrics(geometry, visibleSurvey, filtered, assessment)
  drawOverlay(geometry, visibleSurvey, vectors, filtered?.rejected ?? [])
}

controls.addEventListener('submit', event => {
  event.preventDefault()
  if (!state.meta || state.busy) return
  const { frameCount } = state.meta
  const start = input('start')
  const stop = input('stop')
  const step = input('step')
  try {
    if (![start, stop, step].every(Number.isInteger) || start < 0 || stop > frameCount || step < 1 || stop - start <= step) {
      throw new Error('Choose at least two frames within the video.')
    }
    geometryFor(start)
    const options = {
      ...state.engineOptions, method: method(), threshold: input('threshold'), minArea: input('area-min'),
      maxArea: input('area-max'), maxDisplacement: input('travel') * step, cellSize: input('cell'),
      minTrackFrames: method() === 'piv' ? state.engineOptions.minTrackFrames : input('track-frames'),
      windowSize: Number($('window-size').value), pivSearchRadius: input('piv-radius'),
      pivPairStride: Math.max(1, Math.floor((stop - start) / step / 25)),
    }
    const frameCountSelected = Math.ceil((stop - start) / step)
    if (options.method === 'piv') options.minSamples = Math.min(options.minSamples, frameCountSelected - 1)
    if (options.method === 'ptv' && frameCountSelected < options.minTrackFrames) {
      throw new Error('Select at least ' + options.minTrackFrames + ' frames to confirm tracer tracks.')
    }
    if (!Object.values(options).filter(value => typeof value === 'number').every(Number.isFinite) ||
      (options.method !== 'piv' && options.minArea > options.maxArea)) {
      throw new Error('Check engine settings and tracer area bounds.')
    }
    const { method: runMethod, cellSize, minTrackFrames, include, exclude = [] } = options
    if ($('scale-mode').value === 'control' && start !== state.control.reference) {
      throw new Error('Use frame ' + state.control.reference + ' as the start for these ground control points.')
    }
    state.job = { start, stop, step, method: runMethod, cellSize, minTrackFrames, include, exclude }
    setMask(null)
    setBusy(true)
    $('progress').value = 0
    status('Decoding selected frames…')
    state.worker.postMessage({ type: 'analyze', start, stop, step, options })
  } catch (error) {
    status(error.message, true)
  }
})

function setMask(kind) {
  state.mask = kind ? { kind, points: [] } : null
  canvas.classList.toggle('drawing', Boolean(kind))
  $('draw-actions').hidden = !kind
  $('finish-mask').disabled = true
  $('mask-help').textContent = kind
    ? 'Click the image to add at least three polygon vertices.'
    : 'Static bank areas anchor camera drift. Water polygons limit measurements.'
  if (kind) $('show-water').checked = true
  render()
}

$('draw-water').addEventListener('click', () => setMask('include'))
$('draw-bank').addEventListener('click', () => setMask('staticPolygons'))
$('cancel-mask').addEventListener('click', () => setMask(null))
$('clear-masks').addEventListener('click', () => {
  state.engineOptions.include = []
  state.engineOptions.staticPolygons = []
  setMask(null)
})
$('finish-mask').addEventListener('click', () => {
  if (!state.mask || state.mask.points.length < 3) return
  state.engineOptions[state.mask.kind].push(state.mask.points)
  setMask(null)
})
canvas.addEventListener('pointerdown', event => {
  if (!state.mask || !state.image) return
  const bounds = canvas.getBoundingClientRect()
  state.mask.points.push([Math.round((event.clientX - bounds.left) * canvas.width / bounds.width),
    Math.round((event.clientY - bounds.top) * canvas.height / bounds.height)])
  $('finish-mask').disabled = state.mask.points.length < 3
  render()
})

$('stop-run').addEventListener('click', () => {
  state.job = null
  setBusy(false)
  $('run').disabled = true
  makeWorker(state.source)
  status('Analysis canceled.')
})
$('example').addEventListener('click', () => openSource('/example/video', true))
const wizard = setupImportWizard({
  snapshot: () => ({
    ...state.surveyImport,
    source: state.source, media: state.meta, example: state.example,
    name: state.example ? 'Isar RGB footage' : state.source?.name,
    reference: state.reference ?? 0, captions: state.captions, captionsName: state.captionsName,
    scaleMode: $('scale-mode').value, offset: input('offset'), metres: input('metres'),
    survey: state.surveyData, control: state.control,
    surveyMode: state.surveyData ? $('survey-mode').value : 'validation', radius: input('match-radius'),
    allowReprojection: state.allowReprojection,
  }),
  onApply(imported) {
    if (imported.source !== state.source) {
      openSource(imported.source, imported.example, imported)
      return
    }
    state.captions = imported.captions
    state.captionsName = imported.captionsName
    state.telemetry = null
    state.control = imported.georeference.control ?? null
    state.controlGeometry = state.control ? imported.georeference.geometry : null
    state.allowReprojection = imported.allowReprojection
    state.surveyData = null
    state.surveyCache = null
    state.surveyImport = imported.surveyInput ? { surveyInput: imported.surveyInput,
      file: imported.file, measurement: imported.measurement } : null
    if (imported.survey) setSurvey(imported.survey)
    $('control-option').hidden = !state.control
    $('scale-mode').value = imported.georeference.mode
    if (state.control) $('start').value = state.control.reference
    $('offset').value = imported.georeference.offset
    $('metres').value = imported.georeference.metres
    $('survey-mode').value = imported.surveyMode
    $('match-radius').value = imported.radius
    updateControls()
    render()
  },
})
$('open-import').addEventListener('click', () => wizard.open())
$('review-import').addEventListener('click', () => wizard.open(2))
$('edit-reference').addEventListener('click', () => wizard.open(1))
$('survey-mode').addEventListener('change', render)
$('match-radius').addEventListener('change', render)
controls.addEventListener('change', event => {
  if (event.target.id === 'scale-mode' && $('scale-mode').value === 'control' && state.control) {
    $('start').value = state.control.reference
    if (!state.result && state.meta && state.reference !== state.control.reference) {
      state.reference = state.control.reference
      state.worker.postMessage({ type: 'preview', frame: state.reference })
    }
  }
  updateControls()
  if (event.target.id === 'start' && !state.busy && !state.result && state.meta &&
      input('start') >= 0 && input('start') < state.meta.frameCount) {
    state.reference = input('start')
    state.worker.postMessage({ type: 'preview', frame: state.reference })
  }
  if (!state.busy) render()
})
for (const id of ['show-water', 'show-tracks', 'show-vectors', 'show-survey']) {
  $(id).addEventListener('change', render)
}

for (const id of ['limit-speed', 'speed-limit', 'filter-piv', 'piv-limit', 'filter-ptv', 'ptv-limit', 'show-rejected']) {
  $(id).addEventListener('change', render)
}

const help = $('filter-help')
const helpTitles = {
  speed: 'Maximum speed', piv: 'PIV local median', ptv: 'PTV tracks per cell',
  'track-length': 'Minimum PTV track length',
}
let helpAnchor
function positionHelp() {
  if (!helpAnchor || !help.matches(':popover-open')) return
  const anchor = helpAnchor.getBoundingClientRect()
  const { clientWidth: width, clientHeight: height } = document.documentElement
  help.style.left = Math.max(10, Math.min(anchor.right - help.offsetWidth, width - help.offsetWidth - 10)) + 'px'
  help.style.top = Math.max(10, Math.min(anchor.bottom + 6, height - help.offsetHeight - 10)) + 'px'
}
for (const button of document.querySelectorAll('[data-help]')) {
  button.addEventListener('click', () => {
    if (helpAnchor === button && help.matches(':popover-open')) {
      help.hidePopover()
      return
    }
    helpAnchor = button
    $('help-title').textContent = helpTitles[button.dataset.help]
    $('help-body').replaceChildren($('help-' + button.dataset.help).content.cloneNode(true))
    if (!help.matches(':popover-open')) help.showPopover({ source: button })
    positionHelp()
  })
}
$('close-help').addEventListener('click', () => {
  help.hidePopover()
  helpAnchor?.focus()
})
window.addEventListener('resize', positionHelp)
window.addEventListener('scroll', positionHelp, { passive: true })
const helpPreference = 'pxv-show-help'
try { $('show-help').checked = localStorage.getItem(helpPreference) !== 'false' } catch {}
function updateHelpPreference() {
  const enabled = $('show-help').checked
  document.body.classList.toggle('help-off', !enabled)
  if (!enabled && help.matches(':popover-open')) help.hidePopover()
}
$('show-help').addEventListener('change', () => {
  try { localStorage.setItem(helpPreference, String($('show-help').checked)) } catch {}
  updateHelpPreference()
})
updateHelpPreference()

function download(filename, blob) {
  const url = URL.createObjectURL(blob)
  const anchor = document.createElement('a')
  anchor.href = url
  anchor.download = filename
  anchor.click()
  setTimeout(() => URL.revokeObjectURL(url), 1000)
}

function graphAvailable() {
  return !state.busy && state.result && state.image && state.imageFrame === state.result.start &&
    state.view?.geometry && state.view.filtered?.counts.total > 0
}

const graphDialog = $('graph-dialog')
for (const [id, minimum] of [['graph-width', graphLimits.minWidth], ['graph-height', graphLimits.minHeight]]) {
  $(id).min = minimum
  $(id).max = graphLimits.maxDimension
}
$('export-graph').addEventListener('click', () => {
  if (!graphAvailable()) return
  $('graph-error').textContent = ''
  const geometry = state.view.geometry
  $('graph-units').textContent = geometry.epsg ? 'Includes a speed legend, north arrow and metric scale bar.'
    : 'Includes a speed legend and ' + (geometry.units === 'm' ? 'metric' : 'pixel') +
      ' scale bar. A north arrow requires a georeferenced image.'
  graphDialog.showModal()
})
$('close-graph').addEventListener('click', () => graphDialog.close())
$('graph-form').addEventListener('submit', async event => {
  event.preventDefault()
  if (!graphAvailable()) return
  const button = $('download-graph')
  if (button.disabled) return
  button.disabled = true
  $('graph-error').textContent = ''
  const generation = state.generation
  try {
    const width = input('graph-width')
    const height = input('graph-height')
    const format = $('graph-format').value
    validateGraphSize(width, height)
    const { geometry, filtered } = state.view
    const blob = await renderResultGraph({
      width, height, format, quality: input('graph-quality') / 100, geometry,
      image: state.image, vectors: filtered.accepted,
      title: $('project-title').textContent,
      tracks: $('show-tracks').checked ? state.result.tracks : [],
      survey: $('show-survey').checked ? pointsInFrame(geometry) ?? [] : [],
      rejected: $('show-rejected').checked ? filtered.rejected : [],
      masks: $('show-water').checked ? state.engineOptions : undefined,
    })
    if (!graphDialog.open || generation !== state.generation) return
    download('pxv-result-' + width + 'x' + height + '.' + format, blob)
    graphDialog.close()
  } catch (error) {
    $('graph-error').textContent = error.message
  } finally {
    button.disabled = false
  }
})

function projectStatus(message, error = false) {
  $('project-status').textContent = message
  $('project-status').classList.toggle('error', error)
}

function readProjectSettings() {
  const numbers = Object.fromEntries(Object.entries(projectNumbers).map(([key, id]) =>
    [key, id === 'window-size' ? Number($(id).value) : input(id)]))
  const checks = Object.fromEntries(Object.entries(projectChecks).map(([key, id]) => [key, $(id).checked]))
  return { ...numbers, ...checks, method: method(), scaleMode: $('scale-mode').value,
    surveyMode: $('survey-mode').value, allowReprojection: state.allowReprojection,
    graphFormat: $('graph-format').value }
}

function applyProjectSettings(project) {
  const settings = project.settings
  state.engineOptions = structuredClone(project.engineOptions)
  for (const [key, id] of Object.entries(projectNumbers)) $(id).value = settings[key]
  for (const [key, id] of Object.entries(projectChecks)) $(id).checked = settings[key]
  controls.elements.method.value = settings.method
  $('scale-mode').value = settings.scaleMode
  $('survey-mode').value = settings.surveyMode
  $('graph-format').value = settings.graphFormat
  state.allowReprojection = settings.allowReprojection
  updateHelpPreference()
}

$('save-project').addEventListener('click', () => {
  if (!state.meta || !state.sourceReady || state.busy) return
  try {
    const settings = readProjectSettings()
    const project = {
      video: videoDescriptor(state.source, state.example, { ...state.meta, variant: state.exampleVariant }), settings,
      engineOptions: state.engineOptions, control: state.control, survey: state.surveyData,
      captions: state.captions ? { name: state.captionsName, text: state.captions } : null,
    }
    const text = serializeProject(project)
    const name = $('project-title').textContent.trim().replace(/[^\p{L}\p{N}._-]+/gu, '-')
    download((name || 'project') + '.pxv', new Blob([text], { type: 'application/json;charset=utf-8' }))
    projectStatus('Project saved.')
  } catch (error) {
    projectStatus(error.message, true)
  }
})

const projectDialog = $('project-dialog')
let pendingProject
let pendingVideo
let projectLoadToken

function updateProjectDialog() {
  $('project-video-field').hidden = pendingProject?.video.kind !== 'local'
  $('apply-project').disabled = !pendingProject || !pendingVideo
}

$('load-project').addEventListener('click', () => {
  pendingProject = null
  pendingVideo = null
  $('project-file-info').textContent = 'Choose a saved pXv project to restore its analysis settings.'
  $('project-error').textContent = ''
  updateProjectDialog()
  projectDialog.showModal()
})
$('close-project').addEventListener('click', () => projectDialog.close())
projectDialog.addEventListener('close', () => { projectLoadToken = null })
$('project-file').addEventListener('change', async event => {
  const file = event.target.files[0]
  event.target.value = ''
  if (!file) return
  const token = {}
  projectLoadToken = token
  pendingProject = null
  pendingVideo = null
  $('project-error').textContent = ''
  $('project-file-info').textContent = 'Reading ' + file.name + '…'
  updateProjectDialog()
  try {
    if (file.size > projectFileMaxBytes) throw new Error('This .pxv file exceeds the project size limit.')
    const project = parseProject(await file.text())
    if (!projectDialog.open || projectLoadToken !== token) return
    pendingProject = project
    const { video } = project
    if (video.kind === 'isar') {
      pendingVideo = '/example/video?variant=' + encodeURIComponent(video.variant)
      $('project-file-info').textContent = file.name + ' uses the bundled Isar footage.'
    } else {
      pendingVideo = matchesProjectVideo(video, state.source) ? state.source : null
      $('project-file-info').textContent = file.name + ' refers to ' + video.name + '. ' +
        (pendingVideo ? 'The matching video is already open.' : 'Select that video to restore the project.')
    }
    updateProjectDialog()
  } catch (error) {
    if (!projectDialog.open || projectLoadToken !== token) return
    $('project-file-info').textContent = 'Choose a saved pXv project to restore its analysis settings.'
    $('project-error').textContent = error.message
  }
})
$('project-video').addEventListener('change', event => {
  const file = event.target.files[0]
  event.target.value = ''
  if (!file || pendingProject?.video.kind !== 'local') return
  pendingVideo = matchesProjectVideo(pendingProject.video, file) ? file : null
  $('project-error').textContent = pendingVideo ? '' :
    'Choose ' + pendingProject.video.name + ' with the original file size.'
  if (pendingVideo) $('project-file-info').textContent = pendingProject.video.name + ' selected.'
  updateProjectDialog()
})
$('project-form').addEventListener('submit', async event => {
  event.preventDefault()
  if (!pendingProject || !pendingVideo || $('apply-project').disabled) return
  const project = pendingProject
  const source = pendingVideo
  const token = {}
  projectLoadToken = token
  $('apply-project').disabled = true
  $('project-error').textContent = ''
  try {
    const movie = await openMp4(source)
    await verifyVideoDecoder(movie)
    const { width, height, frameCount, fps } = movie
    const { video, settings, control } = project
    if ([['width', width], ['height', height], ['frameCount', frameCount]].some(([key, value]) =>
      video[key] != null && video[key] !== value) || (video.fps != null && Math.abs(fps - video.fps) > 0.001)) {
      throw new Error('The footage dimensions or frame timing differ from the saved project.')
    }
    if (settings.stop > frameCount) throw new Error('The saved frame range exceeds the video length.')
    const geometry = control ? controlPointGeometry({ ...control, width, height }) : null
    if (settings.scaleMode === 'camera') {
      const telemetry = parseDjiSrt(project.captions.text, { startFrame: settings.start, endFrame: settings.start + 1 })
      cameraGeometry({ ...telemetry, width, height, waterOffset: settings.offset })
    }
    if (!projectDialog.open || projectLoadToken !== token) return
    const imported = {
      captions: project.captions?.text, captionsName: project.captions?.name, survey: project.survey,
      surveyMode: settings.surveyMode, radius: settings.matchRadius, allowReprojection: settings.allowReprojection,
      georeference: { mode: settings.scaleMode, offset: settings.offset, metres: settings.metres, control, geometry },
    }
    projectDialog.close()
    openSource(source, video.kind === 'isar', imported, project)
  } catch (error) {
    if (projectDialog.open && projectLoadToken === token) {
      $('project-error').textContent = error.message
      updateProjectDialog()
    }
  }
})

function downloadCsv(filename, { headers, rows }) {
  const escape = value => '"' + String(value ?? '').replaceAll('"', '""') + '"'
  const csv = [headers, ...rows].map(row => row.map(escape).join(',')).join('\r\n')
  download(filename, new Blob([csv], { type: 'text/csv;charset=utf-8' }))
}

function vectorTable(geometry, vectors) {
  const { units, epsg } = geometry
  const { start, stop, step } = state.result
  const headers = ['image_x_px', 'image_y_px', 'x_' + units, 'y_' + units,
    'u_' + units + '_s', 'v_' + units + '_s', 'speed_' + units + '_s', 'samples', 'source', 'epsg',
    'start_frame', 'stop_frame_exclusive', 'frame_step', 'distinct_ptv_tracks', 'raw_speed_' + units + '_s',
    'speed_multiplier', 'speed_error_m_s', 'signed_speed_residual_m_s', 'error_basis',
    'reference_speed_m_s', 'reference_working_uncertainty_m_s', 'reference_id', 'match_distance_m']
  const rows = vectors.map(vector => {
    const { x, y, u, v, speed, count, trackCount, source, pixel } = vector
    return [pixel.x, pixel.y, x, y, u, v, speed, count, source, epsg, start, stop, step, trackCount,
      vector.rawSpeed ?? speed, vector.gain ?? 1, vector.speedError, vector.speedResidual, vector.errorBasis,
      vector.referenceSpeed, vector.referenceSigma, vector.referenceId, vector.matchDistance]
  })
  return { headers, rows }
}

function surveyTable() {
  const units = describeCrs(state.survey.epsg)?.units
  const suffix = units === 'm' ? '_m' : units === 'degrees' ? '_deg' : ''
  const headers = ['id', 'name', 'x' + suffix, 'y' + suffix, 'epsg', 'depth_m', 'surface_m_s',
    'working_uncertainty_m_s', 'method', 'flags', 'source_x', 'source_y', 'source_epsg']
  const rows = state.survey.verticals.map(point =>
    [point.id, point.name, point.x, point.y, state.survey.epsg, point.totalDepth, point.speed,
      point.sigma, point.method, point.flags.join('|'), point.sourceX ?? point.x, point.sourceY ?? point.y,
      state.survey.sourceEpsg])
  return { headers, rows }
}

$('export-vectors').addEventListener('click', () => {
  const { geometry, filtered } = state.view
  if (filtered?.accepted.length) downloadCsv('pxv-vectors.csv', vectorTable(geometry, filtered.accepted))
})
$('export-survey').addEventListener('click', () => {
  if (state.survey) downloadCsv('pxv-survey-surface.csv', surveyTable())
})
$('export-xlsx').addEventListener('click', () => {
  const { geometry, options, filtered, assessment } = state.view
  const sheets = []
  if (filtered?.counts.total) {
    sheets.push({ name: 'Vectors', ...vectorTable(geometry, filtered.accepted) })
    if (filtered.rejected.length) {
      const { headers, rows } = vectorTable(geometry, filtered.rejected)
      sheets.push({ name: 'Excluded vectors', headers: [...headers, 'reason', 'normalized_residual'],
        rows: rows.map((row, index) => [...row, filtered.rejected[index].reason, filtered.rejected[index].score]) })
    }
  }
  if (state.survey) sheets.push({ name: 'Survey', ...surveyTable() })
  if (filtered?.counts.total) {
    sheets.push({ name: 'Analysis settings', filter: false, headers: ['Setting', 'Value'],
      rows: [
        ['Method', state.result.method.toUpperCase()],
        ['Frame range start', state.result.start], ['Frame range stop (excluded)', state.result.stop],
        ['Frame step', state.result.step], ['Seconds per nominal frame', 1 / state.meta.fps],
        ['Velocity units', geometry.units + '/s'], ['EPSG', geometry.epsg],
        ['Camera height above takeoff (m)', geometry.heightAboveWater == null ? null : state.telemetry?.data.heightAboveTakeoff],
        ['UAS takeoff above water surface (m)', geometry.heightAboveWater == null ? null : input('offset')],
        ['Camera height above water (m)', geometry.heightAboveWater],
        ['Scale at image centre (m/px)', geometry.gsd?.center],
        ['Georeference mode', $('scale-mode').value],
        ['Ground control input EPSG', geometry.controlPointCount ? geometry.inputEpsg : null],
        ['Ground control fit', geometry.model],
        ['Ground control points', geometry.controlPointCount],
        ['Ground control residual (m)', geometry.controlPointRmse],
        ['Maximum speed (m/s)', options.maxSpeed], ['PIV local median threshold', options.pivThreshold],
        ['Minimum distinct PTV tracks per cell', options.ptvMinTracks],
        ['Minimum PTV track length (frames)', state.result.method === 'piv' ? null : state.result.minTrackFrames],
        ['Velocity cells kept', filtered.counts.accepted], ['Velocity cells excluded', filtered.counts.rejected],
        ['Survey use', state.surveyData ? $('survey-mode').value : 'no ground truth'],
        ['Survey source EPSG', state.surveyData?.epsg], ['Survey CRS after conversion', state.survey?.epsg],
        ['Match radius (m)', assessment.status === 'none' ? null : assessment.radius],
        ['Survey comparison status', assessment.status],
        ['Validation pairs', assessment.validation.count],
        ['Validation RMSE (m/s)', assessment.validation.rmse],
        ['Baseline RMSE on same pairs (m/s)', assessment.baseline.rmse],
        ['Speed error definition', 'Absolute difference from a matched survey speed. Blank when unmatched.'],
        ['Calibration evaluation', 'Fit and holdout are spatially separate. Validation is evaluated before the final output speed cap.'],
        ...Object.entries(assessment.bySource).flatMap(([source, item]) => [
          [source.toUpperCase() + ' fitted multiplier', item.gain],
          [source.toUpperCase() + ' fit pairs', item.trainingCount],
          [source.toUpperCase() + ' validation pairs', item.validation.count],
        ]),
      ],
    })
  }
  if (!sheets.length) return
  try {
    download('pxv-results.xlsx', createWorkbook(sheets))
  } catch (error) {
    status('XLSX export failed. ' + error.message, true)
  }
})

openSource('/example/video', true)

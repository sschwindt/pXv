import { cameraGeometry, controlPointGeometry, linearGeometry, parseDjiSrt } from './geometry.mjs'
import { decodeWindow, openMp4, verifyVideoDecoder } from './mp4.mjs'
import { datumNote, describeCrs, parseEpsg, reprojectSurvey } from './spatial-reference.mjs'
import { inspectSurvey, parseSurvey } from './survey-import.mjs'

const $ = id => document.getElementById(id)
const steps = ['video', 'geo', 'survey', 'review']
const fields = {
  x: 'Map X / longitude', y: 'Map Y / latitude', speed: 'Speed (m/s)',
  u: 'X velocity (m/s)', v: 'Y velocity (m/s)', sigma: 'Speed uncertainty (m/s)',
  measurementDepth: 'Depth below surface (m)', totalDepth: 'Total water depth (m)',
  surface: 'Surface indicator', id: 'Point ID', name: 'Point name',
}
const srtHelp = $('import-srt-info').textContent
const surveyHelp = $('import-survey-info').textContent
const fileInput = (id, handler) => $(id).addEventListener('change', event => {
  const file = event.target.files[0]
  event.target.value = ''
  if (file) handler(file)
})

export function inspectAlignment(survey, geometry, media) {
  if (!survey) return { kind: 'missing', message: 'No ground truth. Video velocities cannot be validated or calibrated.' }
  if (!geometry?.epsg) {
    return { kind: 'unreferenced', message: 'The video has no map location. Use DJI GPS or enter ground control points to match the survey.' }
  }
  if (!describeCrs(survey.epsg)) {
    return { kind: 'unsupported', message: 'Enter the survey’s true EPSG code. For a CRS pXv does not support, re-export as WGS 84 or a supported UTM CRS.' }
  }
  try {
    const aligned = reprojectSurvey(survey, geometry.epsg)
    const count = aligned.rows.filter(point => {
      const [x, y] = geometry.toImage(point.x, point.y)
      return x >= 0 && y >= 0 && x < media.width && y < media.height
    }).length
    const different = survey.epsg !== geometry.epsg
    const note = datumNote(survey.epsg, geometry.epsg)
    const overlap = count
      ? count + ' of ' + aligned.rows.length + ' readings fall inside the estimated video footprint.'
      : 'No survey readings fall inside the estimated video footprint. Check the EPSG code, X/Y order, takeoff offset and control points.'
    return {
      kind: different ? 'reproject' : 'same', count, aligned,
      message: (different ? 'Reproject EPSG:' + survey.epsg + ' → EPSG:' + geometry.epsg + '. ' : 'Both datasets use EPSG:' + geometry.epsg + '. ') +
        overlap + (note ? ' ' + note : ''),
    }
  } catch (error) {
    return { kind: 'failed', message: error.message + ' Check the declared CRS, X/Y order and coordinate ranges.' }
  }
}

function controlPoints(text, media, epsg) {
  const image = []
  const world = []
  const rows = text.trim().split(/\r?\n/).filter(line => line.trim())
  if (/^\s*(?:image_x(?:_px)?|x_px)\s*[,;\t]/i.test(rows[0] ?? '')) rows.shift()
  if (rows.length > 100) throw new Error('Enter up to 100 ground control points.')
  for (const [index, line] of rows.entries()) {
    const parts = line.trim().split(/\s*[,;\t]\s*|\s+/)
    const values = parts.map(part => part === '' ? NaN : Number(part))
    if (values.length !== 4 || !values.every(Number.isFinite)) throw new Error('Check the four coordinates on control point line ' + (index + 1) + '.')
    const [x, y, easting, northing] = values
    if (x < 0 || y < 0 || x >= media.width || y >= media.height) {
      throw new Error('Control point ' + (index + 1) + ' lies outside the video image.')
    }
    image.push([x, y])
    world.push([easting, northing])
  }
  return { image, world, epsg }
}

function buildGeometry(draft) {
  const mode = $('import-scale').value
  const metres = $('import-metres').valueAsNumber
  const offset = $('import-offset').valueAsNumber
  let control
  let geometry
  if (mode === 'camera') {
    if (!draft.captions) throw new Error('Choose a DJI SRT file or select a different video reference.')
    const telemetry = parseDjiSrt(draft.captions, { startFrame: draft.reference, endFrame: draft.reference + 1 })
    geometry = cameraGeometry({ ...telemetry, width: draft.media.width, height: draft.media.height, waterOffset: offset })
  } else if (mode === 'control') {
    control = { ...controlPoints($('import-gcp').value, draft.media, parseEpsg($('import-gcp-epsg').value)),
      reference: draft.reference }
    geometry = controlPointGeometry({ ...control, width: draft.media.width, height: draft.media.height })
  } else if (mode === 'linear') {
    if (!Number.isFinite(metres) || metres <= 0) throw new Error('Enter a positive number of metres per pixel.')
    geometry = linearGeometry(metres)
  } else {
    geometry = linearGeometry()
  }
  return { mode, metres, offset, control, geometry }
}

async function inspectFile(file, layer, signal) {
  if (!/\.gpkg$/i.test(file.name)) return { inspection: inspectSurvey(await file.text(), { filename: file.name }) }
  const response = await fetch('/import/geopackage' + (layer ? '?layer=' + encodeURIComponent(layer) : ''), {
    method: 'POST', body: file, headers: { 'Content-Type': 'application/geopackage+sqlite3' }, signal,
  })
  if (!response.ok) throw new Error(await response.text())
  const result = await response.json()
  return { ...result, inspection: inspectSurvey({ ...result.data, epsg: result.epsg }) }
}

export function setupImportWizard({ snapshot, onApply }) {
  const dialog = $('import-dialog')
  let step = 0
  let draft
  let pending
  let surveyRead
  let previewToken

  function setError(message = '') { $('import-error').textContent = message }

  function showStep(next) {
    step = next
    steps.forEach((name, index) => { $('import-' + name + '-step').hidden = index !== step })
    $('import-progress').textContent = 'Step ' + (step + 1) + ' of ' + steps.length
    $('import-back').hidden = step === 0
    $('import-next').textContent = step === steps.length - 1 ? 'Apply import' : 'Next'
    $('import-next').disabled = Boolean(pending)
    setError()
    if (dialog.open && step === 1 && $('import-scale').value === 'control') showControlFrame()
  }

  function showGeoFields() {
    const mode = $('import-scale').value
    $('remove-srt').hidden = !draft.captions
    $('import-metres-field').hidden = mode !== 'linear'
    $('import-offset-field').hidden = mode !== 'camera'
    $('import-gcp-fields').hidden = mode !== 'control'
    if (dialog.open && step === 1 && mode === 'control') showControlFrame()
  }

  async function showControlFrame() {
    const project = draft
    const token = {}
    previewToken = token
    const canvas = $('import-gcp-preview')
    const note = $('import-gcp-position')
    canvas.hidden = true
    note.textContent = 'Loading frame ' + project.reference + '…'
    try {
      const movie = project.movie ?? await openMp4(project.source)
      const preview = new OffscreenCanvas(movie.width, movie.height)
      const context = preview.getContext('2d')
      await decodeWindow(movie, { start: project.reference, stop: project.reference + 1,
        onFrame: frame => context.drawImage(frame, 0, 0) })
      if (previewToken !== token || draft !== project || !dialog.open) return
      canvas.width = movie.width
      canvas.height = movie.height
      canvas.getContext('2d').drawImage(preview, 0, 0)
      canvas.hidden = false
      note.textContent = 'Reference frame ' + project.reference + '. Click a location to read its pixel coordinates.'
    } catch (error) {
      if (previewToken === token && dialog.open) note.textContent = 'Reference preview unavailable. ' + error.message
    }
  }

  function showSurveyFields(result) {
    const inspection = result?.inspection
    $('import-survey-options').hidden = !inspection
    $('import-layer-field').hidden = !(result?.layers?.length > 1)
    $('remove-survey').hidden = !draft.survey && !draft.file && !inspection
    $('import-survey-epsg').value = draft.survey?.epsg ?? inspection?.epsg ?? ''
    $('import-measurement').value = draft.measurement ?? 'auto'
    $('import-mapping').replaceChildren()
    if (result?.layers) {
      $('import-layer').replaceChildren(...result.layers.map(layer => new Option(layer.name, layer.name)))
      $('import-layer').value = result.layer
    }
    if (!inspection) return
    $('import-mapping').replaceChildren(...Object.entries(fields).filter(([key]) =>
      inspection.format !== 'geojson' || !['x', 'y'].includes(key)).map(([key, title]) => {
      const label = document.createElement('label')
      label.textContent = title
      const select = document.createElement('select')
      select.dataset.column = key
      select.append(new Option('—', ''), ...inspection.columns.map(column => new Option(column, column)))
      select.value = draft.survey?.mapping?.[key] ?? inspection.mapping[key] ?? ''
      label.append(select)
      return label
    }))
  }

  async function runImport(action) {
    const token = {}
    pending = token
    $('import-next').disabled = true
    $('import-back').disabled = true
    setError()
    try {
      const commit = await action()
      if (pending === token && dialog.open) commit()
    } catch (error) {
      if (pending === token && dialog.open) setError(error.message)
    } finally {
      if (pending === token) {
        pending = null
        $('import-next').disabled = false
        $('import-back').disabled = false
      }
    }
  }

  function open(start = 0) {
    if (dialog.open) return
    pending = null
    $('import-back').disabled = false
    draft = { ...snapshot() }
    if (!draft.media) start = 0
    $('import-video-info').textContent = draft.media
      ? draft.name + ' · ' + draft.media.width + ' × ' + draft.media.height + ' · ' + draft.media.fps.toFixed(2) + ' fps'
      : 'Choose a regular MP4, MOV or M4V that your browser can decode.'
    $('import-srt-info').textContent = draft.captions ? (draft.captionsName || 'DJI SRT attached') + '. ' + srtHelp : srtHelp
    $('import-scale').value = draft.scaleMode ?? 'pixel'
    $('import-metres').value = draft.metres ?? 0.025
    $('import-offset').value = draft.offset ?? 0
    $('import-gcp-epsg').value = draft.control?.epsg ?? ''
    $('import-gcp').value = draft.control?.image.map((point, index) => [...point, ...draft.control.world[index]].join(',')).join('\n') ?? ''
    $('import-survey-info').textContent = draft.survey ? (draft.survey.name ?? 'Current survey') + ' · ' + draft.survey.rows.length + ' readings.' : surveyHelp
    $('import-mode').value = draft.surveyMode ?? 'validation'
    $('import-radius').value = draft.radius ?? 1.5
    $('import-reproject').checked = Boolean(draft.allowReprojection)
    $('import-gcp-preview').hidden = true
    showGeoFields()
    showSurveyFields(draft.surveyInput)
    showStep(start)
    dialog.showModal()
    showGeoFields()
  }

  function chooseSurvey(file, layer) {
    surveyRead?.abort()
    surveyRead = new AbortController()
    draft.file = file
    draft.survey = null
    draft.surveyInput = null
    draft.measurement = 'auto'
    if (!layer) showSurveyFields()
    $('import-survey-info').textContent = 'Reading ' + file.name + '…'
    runImport(async () => {
      const result = await inspectFile(file, layer, surveyRead.signal)
      return () => {
        draft.surveyInput = result
        showSurveyFields(result)
        $('import-survey-info').textContent = file.name + ' · ' + result.inspection.records.length + ' point records' +
          (result.inspection.skipped ? ' · ' + result.inspection.skipped + ' non-point or empty records skipped.' : '.')
      }
    })
  }

  fileInput('import-video', file => {
    draft.source = null
    draft.media = null
    $('import-video-info').textContent = 'Checking ' + file.name + '…'
    runImport(async () => {
      const movie = await openMp4(file)
      await verifyVideoDecoder(movie)
      return () => {
        const { width, height, fps, frameCount, codecLabel, duration } = movie
        if (frameCount < 2) throw new Error('Choose a video with at least two frames.')
        Object.assign(draft, { source: file, movie, media: { width, height, fps, frameCount, codecLabel, duration },
          name: file.name, reference: 0, captions: null, captionsName: '', control: null, example: false,
          survey: null, surveyInput: null, file: null })
        $('import-survey-info').textContent = surveyHelp
        $('import-mode').value = 'validation'
        $('import-reproject').checked = false
        showSurveyFields()
        $('import-scale').value = 'pixel'
        $('import-gcp').value = ''
        $('import-gcp-epsg').value = ''
        $('import-offset').value = 0
        $('import-srt-info').textContent = srtHelp
        $('import-video-info').textContent = file.name + ' · ' + width + ' × ' + height + ' · ' + fps.toFixed(2) + ' fps · ' + codecLabel
        showGeoFields()
      }
    })
  })

  fileInput('import-srt', file => runImport(async () => {
    const captions = await file.text()
    const telemetry = parseDjiSrt(captions)
    return () => {
      draft.captions = captions
      draft.captionsName = file.name
      $('import-scale').value = 'camera'
      const mismatch = (telemetry.fps && Math.abs(telemetry.fps / draft.media.fps - 1) > 0.02) ||
        Math.abs(telemetry.frameCount - draft.media.frameCount) > 2
      $('import-srt-info').textContent = file.name + (mismatch
        ? ' · Timing differs from the video. Check that the caption file starts at the same frame before using its telemetry.'
        : ' · Caption timings loaded.')
      showGeoFields()
    }
  }))
  $('remove-srt').addEventListener('click', () => {
    pending = null
    draft.captions = null
    draft.captionsName = ''
    if ($('import-scale').value === 'camera') $('import-scale').value = 'pixel'
    $('import-srt-info').textContent = srtHelp
    $('import-next').disabled = false
    $('import-back').disabled = false
    showGeoFields()
    setError()
  })
  fileInput('import-survey', file => chooseSurvey(file))
  $('import-layer').addEventListener('change', event => chooseSurvey(draft.file, event.target.value))
  $('remove-survey').addEventListener('click', () => {
    surveyRead?.abort()
    pending = null
    draft.file = null
    draft.surveyInput = null
    draft.survey = null
    $('import-next').disabled = false
    $('import-back').disabled = false
    $('import-survey-info').textContent = surveyHelp
    showSurveyFields()
    setError()
  })
  $('import-scale').addEventListener('change', showGeoFields)
  $('import-gcp-preview').addEventListener('click', event => {
    const canvas = event.currentTarget
    const bounds = canvas.getBoundingClientRect()
    const x = Math.floor((event.clientX - bounds.left) * canvas.width / bounds.width)
    const y = Math.floor((event.clientY - bounds.top) * canvas.height / bounds.height)
    $('import-gcp-position').textContent = 'Frame ' + draft.reference + ' · Image X ' + x + ' px · Image Y ' + y + ' px'
  })
  $('cancel-import').addEventListener('click', () => dialog.close())
  dialog.addEventListener('close', () => { surveyRead?.abort(); pending = null })
  $('import-back').addEventListener('click', () => showStep(step - 1))
  $('import-form').addEventListener('submit', event => {
    event.preventDefault()
    if (pending) return
    setError()
    try {
      if (step === 0 && !draft.media) throw new Error('Choose a supported video file to continue.')
      if (step === 1) draft.georeference = buildGeometry(draft)
      if (step === 2) {
        if (draft.file && !draft.surveyInput) throw new Error('Select a readable survey file or remove it to continue.')
        if (draft.surveyInput) {
          const epsg = $('import-survey-epsg').value.trim()
          if (epsg && !parseEpsg(epsg)) throw new Error('Enter an EPSG code such as 4326 or 25832.')
          const mapping = Object.fromEntries([...$('import-mapping').querySelectorAll('select')].map(select =>
            [select.dataset.column, select.value]))
          draft.measurement = $('import-measurement').value
          const survey = parseSurvey(draft.surveyInput.inspection, { epsg: epsg || null,
            mapping, measurement: draft.measurement })
          draft.survey = { ...survey, name: draft.file.name, layer: draft.surveyInput.layer }
        }
        draft.georeference = buildGeometry(draft)
        const geometry = draft.georeference.geometry
        const alignment = inspectAlignment(draft.survey, geometry, draft.media)
        draft.alignment = alignment
        const rows = [['Video', draft.name], ['Video CRS', describeCrs(geometry.epsg)?.name ?? (geometry.units === 'm' ? 'Local metres' : 'Image pixels')]]
        if (draft.survey) rows.push(['Survey', draft.survey.name ?? 'Current survey'],
          ['Survey CRS', describeCrs(draft.survey.epsg)?.name ?? (draft.survey.epsg ? 'EPSG:' + draft.survey.epsg : 'Unspecified')])
        $('import-review').replaceChildren(...rows.flatMap(([label, value]) => {
          const term = document.createElement('dt')
          const detail = document.createElement('dd')
          term.textContent = label
          detail.textContent = value
          return [term, detail]
        }))
        const surveyWarnings = draft.survey?.warnings ?? []
        $('import-alignment').textContent = [alignment.message, ...surveyWarnings].join(' ')
        $('import-alignment').classList.toggle('warning', !alignment.count)
        $('import-reproject-field').hidden = alignment.kind !== 'reproject'
        $('import-purpose').hidden = !draft.survey
      }
      if (step < steps.length - 1) {
        showStep(step + 1)
        return
      }
      const surveyMode = draft.survey ? $('import-mode').value : 'none'
      const radius = $('import-radius').valueAsNumber
      const allowReprojection = $('import-reproject').checked
      if (draft.survey && (!Number.isFinite(radius) || radius < 0.01)) throw new Error('Enter a match radius of at least 0.01 m.')
      if (surveyMode !== 'none' && draft.alignment.kind === 'reproject' && !allowReprojection) {
        throw new Error('Enable reprojection to compare the survey with the video, or choose Reference only.')
      }
      onApply({ ...draft, surveyMode, radius: Number.isFinite(radius) ? radius : 1.5, allowReprojection })
      dialog.close()
    } catch (error) {
      setError(error.message)
    }
  })
  return { open }
}

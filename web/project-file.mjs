import { validateGraphSize } from './result-graph.mjs'
import { describeCrs } from './spatial-reference.mjs'

export const projectFileMaxBytes = 96 * 1024 * 1024
const captionMaxLength = 32 * 1024 * 1024
const methods = ['ptv', 'piv', 'combined']
const scaleModes = ['camera', 'control', 'linear', 'pixel']
const surveyModes = ['none', 'validation', 'calibration']

function invalid(label) {
  throw new Error('Invalid project field: ' + label + '.')
}

function record(value, label) {
  if (value === null || typeof value !== 'object' || Array.isArray(value)) invalid(label)
  return value
}

function array(value, label, min = 0, max = Infinity) {
  if (!Array.isArray(value) || value.length < min || value.length > max) invalid(label)
  return value
}

function string(value, label, min = 0, max = 4096) {
  if (typeof value !== 'string' || value.length < min || value.length > max) invalid(label)
  return value
}

function number(value, label, min = -Infinity, max = Infinity) {
  if (!Number.isFinite(value) || value < min || value > max) invalid(label)
  return value
}

function integer(value, label, min = 0, max = Number.MAX_SAFE_INTEGER) {
  if (!Number.isSafeInteger(value) || value < min || value > max) invalid(label)
  return value
}

function boolean(value, label) {
  if (typeof value !== 'boolean') invalid(label)
  return value
}

function oneOf(value, choices, label) {
  if (!choices.includes(value)) invalid(label)
  return value
}

function optional(value, convert) {
  return value == null ? value : convert(value)
}

function validVideo(value) {
  const video = record(value, 'video')
  const kind = oneOf(video.kind, ['isar', 'local'], 'video.kind')
  const name = string(video.name, 'video.name', 1, 1024)
  if (/[\/\\\u0000-\u001f]/.test(name) || ['.', '..'].includes(name)) invalid('video.name')
  return {
    kind, name,
    width: integer(video.width, 'video.width', 1, 65535),
    height: integer(video.height, 'video.height', 1, 65535),
    frameCount: integer(video.frameCount, 'video.frameCount', 2),
    fps: number(video.fps, 'video.fps', Number.MIN_VALUE),
    ...(kind === 'local' ? {
      size: integer(video.size, 'video.size', 1),
      lastModified: optional(video.lastModified, value => integer(value, 'video.lastModified', Number.MIN_SAFE_INTEGER)),
    } : {}),
  }
}

export function videoDescriptor(source, isExample, metadata) {
  return validVideo({
    kind: isExample ? 'isar' : 'local',
    name: isExample ? 'Isar RGB footage' : source?.name,
    size: source?.size,
    lastModified: source?.lastModified,
    width: metadata?.width,
    height: metadata?.height,
    frameCount: metadata?.frameCount,
    fps: metadata?.fps,
  })
}

/** Compare the stable parts of a local file descriptor. Copying can change its timestamp. */
export function matchesProjectVideo(video, file) {
  return video?.kind === 'local' && file?.name === video.name && file.size === video.size
}

function validSettings(value, video) {
  const settings = record(value, 'settings')
  const start = integer(settings.start, 'settings.start', 0, video.frameCount - 2)
  const stop = integer(settings.stop, 'settings.stop', start + 2, video.frameCount)
  const step = integer(settings.step, 'settings.step', 1, stop - start - 1)
  const graphWidth = settings.graphWidth
  const graphHeight = settings.graphHeight
  validateGraphSize(graphWidth, graphHeight)
  const minArea = integer(settings.minArea, 'settings.minArea', 1)
  return {
    start, stop, step, graphWidth, graphHeight, minArea,
    method: oneOf(settings.method, methods, 'settings.method'),
    scaleMode: oneOf(settings.scaleMode, scaleModes, 'settings.scaleMode'),
    offset: number(settings.offset, 'settings.offset'),
    metres: number(settings.metres, 'settings.metres', 0.000001),
    threshold: number(settings.threshold, 'settings.threshold', 0.05, 1),
    maxArea: integer(settings.maxArea, 'settings.maxArea', minArea),
    travel: integer(settings.travel, 'settings.travel', 1, 80),
    cellSize: integer(settings.cellSize, 'settings.cellSize', 8, 256),
    minTrackFrames: integer(settings.minTrackFrames, 'settings.minTrackFrames', 2),
    windowSize: oneOf(settings.windowSize, [32, 48, 64, 96], 'settings.windowSize'),
    pivRadius: integer(settings.pivRadius, 'settings.pivRadius', 1, 64),
    speedLimit: number(settings.speedLimit, 'settings.speedLimit', 0.01),
    pivLimit: number(settings.pivLimit, 'settings.pivLimit', 0.1),
    ptvLimit: integer(settings.ptvLimit, 'settings.ptvLimit', 1),
    surveyMode: oneOf(settings.surveyMode, surveyModes, 'settings.surveyMode'),
    matchRadius: number(settings.matchRadius, 'settings.matchRadius', 0.01),
    ...Object.fromEntries(['limitSpeed', 'filterPiv', 'filterPtv', 'allowReprojection', 'showWater', 'showTracks',
      'showVectors', 'showSurvey', 'showRejected', 'showHelp'].map(key => [key, boolean(settings[key], 'settings.' + key)])),
  }
}

function point(value, label, maxX = Infinity, maxY = Infinity) {
  const [x, y] = array(value, label, 2, 2)
  return [number(x, label + ' X', maxX === Infinity ? -Infinity : 0, maxX),
    number(y, label + ' Y', maxY === Infinity ? -Infinity : 0, maxY)]
}

function validEngine(value, video) {
  const options = record(value, 'engineOptions')
  const result = {}
  for (const name of ['include', 'exclude', 'staticPolygons']) {
    const label = 'engineOptions.' + name
    result[name] = array(options[name] ?? [], label, 0, 1000).map((polygon, index) =>
      array(polygon, label + ' polygon ' + (index + 1), 3, 10000)
        .map(vertex => point(vertex, label + ' vertex', video.width, video.height)))
  }
  if (options.method !== undefined) result.method = oneOf(options.method, methods, 'engineOptions.method')
  for (const name of ['threshold', 'overlap', 'correlationThreshold', 'maxZeroLagRatio']) {
    if (options[name] !== undefined) result[name] = number(options[name], 'engineOptions.' + name, 0, name === 'overlap' ? 1 - Number.EPSILON : 1)
  }
  for (const name of ['minArea', 'maxArea', 'minTrackFrames', 'maxGapFrames', 'cellSize', 'minSamples',
    'windowSize', 'pivPairStride', 'pivSearchRadius', 'stabilizationRadius']) {
    if (options[name] === undefined) continue
    const min = { maxGapFrames: 0, minTrackFrames: 2, windowSize: 8 }[name] ?? 1
    const max = ['pivSearchRadius', 'stabilizationRadius'].includes(name) ? 64 : Number.MAX_SAFE_INTEGER
    result[name] = integer(options[name], 'engineOptions.' + name, min, max)
  }
  if (options.maxDisplacement !== undefined) {
    result.maxDisplacement = number(options.maxDisplacement, 'engineOptions.maxDisplacement', Number.MIN_VALUE)
  }
  if (options.staticThreshold !== undefined) {
    result.staticThreshold = number(options.staticThreshold, 'engineOptions.staticThreshold', 0)
  }
  return result
}

function validCaptions(value) {
  if (value == null) return null
  const captions = record(value, 'captions')
  return {
    name: optional(captions.name, value => string(value, 'captions.name')),
    text: string(captions.text, 'captions.text', 1, captionMaxLength),
  }
}

function validControl(value, video) {
  if (value == null) return null
  const control = record(value, 'control')
  const image = array(control.image, 'control.image', 3, 100).map(vertex =>
    point(vertex, 'control image point', video.width - 1, video.height - 1))
  const world = array(control.world, 'control.world', image.length, image.length)
    .map(vertex => point(vertex, 'control map point'))
  const epsg = integer(control.epsg, 'control.epsg', 1)
  if (!describeCrs(epsg)) invalid('control.epsg')
  const reference = integer(control.reference, 'control.reference', 0, video.frameCount - 2)
  return { image, world, epsg, reference }
}

const surveyNumericFields = ['x', 'y', 'u', 'v', 'speed', 'sigma', 'measurementDepth', 'totalDepth', 'sourceX', 'sourceY']
const surveyMappingFields = ['x', 'y', 'u', 'v', 'speed', 'sigma', 'measurementDepth', 'totalDepth', 'surface', 'id', 'name']

function validSurvey(value) {
  if (value == null) return null
  const survey = record(value, 'survey')
  const rows = array(survey.rows, 'survey.rows', 1, 250000).map((row, index) => {
    const label = 'survey row ' + (index + 1)
    record(row, label)
    const result = {}
    for (const key of surveyNumericFields) {
      if (row[key] !== undefined) result[key] = optional(row[key], value => number(value, label + ' ' + key))
    }
    if (row.id !== undefined) {
      result.id = optional(row.id, value => typeof value === 'string' ? string(value, label + ' id') : number(value, label + ' id'))
    }
    if (row.name !== undefined) result.name = optional(row.name, value => string(value, label + ' name'))
    if (row.surface !== undefined) result.surface = optional(row.surface, value => boolean(value, label + ' surface'))
    return result
  })
  const result = {
    epsg: optional(survey.epsg, value => integer(value, 'survey.epsg', 1)) ?? null,
    rows,
  }
  for (const key of ['name', 'layer', 'format']) {
    if (survey[key] !== undefined) result[key] = optional(survey[key], value => string(value, 'survey.' + key))
  }
  if (survey.sourceEpsg !== undefined) result.sourceEpsg = optional(survey.sourceEpsg, value => integer(value, 'survey.sourceEpsg', 1))
  if (survey.skipped !== undefined) result.skipped = integer(survey.skipped, 'survey.skipped')
  for (const key of ['warnings', 'columns']) {
    if (survey[key] !== undefined) {
      result[key] = array(survey[key], 'survey.' + key, 0, 10000).map(value => string(value, 'survey ' + key))
    }
  }
  if (survey.mapping !== undefined) {
    const mapping = record(survey.mapping, 'survey.mapping')
    result.mapping = Object.fromEntries(surveyMappingFields.filter(key => Object.hasOwn(mapping, key))
      .map(key => [key, string(mapping[key], 'survey mapping ' + key)]))
  }
  return result
}

function validate(project) {
  const data = record(project, 'project')
  const video = validVideo(data.video)
  const settings = validSettings(data.settings, video)
  const engineOptions = validEngine(data.engineOptions, video)
  const captions = validCaptions(data.captions)
  const survey = validSurvey(data.survey)
  const control = validControl(data.control, video)
  if (settings.scaleMode === 'control' && (!control || control.reference !== settings.start)) {
    throw new Error('The project needs control points for its analysis start frame.')
  }
  if (settings.scaleMode === 'camera' && !captions) {
    throw new Error('The project needs DJI captions for its selected camera scale.')
  }
  return { format: 'pxv', version: 1, video, settings, engineOptions, captions, survey, control }
}

function checkFileSize(text) {
  if (text.length > projectFileMaxBytes || new TextEncoder().encode(text).byteLength > projectFileMaxBytes) {
    throw new Error('The project exceeds the 96 MiB file limit.')
  }
}

export function serializeProject(project) {
  const text = JSON.stringify(validate(project), null, 2)
  checkFileSize(text)
  return text + '\n'
}

export function parseProject(text) {
  if (typeof text !== 'string') throw new Error('Choose a pXv project file.')
  checkFileSize(text)
  let project
  try {
    project = JSON.parse(text)
  } catch {
    throw new Error('The project is not valid JSON.')
  }
  if (project?.format !== 'pxv' || project.version !== 1) {
    throw new Error('The project format or version is unsupported.')
  }
  return validate(project)
}

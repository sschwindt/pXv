const ascii = (view, offset) => String.fromCharCode(...new Uint8Array(view.buffer, view.byteOffset + offset, 4))
const u32 = (view, offset) => view.getUint32(offset)
const u64 = (view, offset) => {
  const number = Number(view.getBigUint64(offset))
  if (!Number.isSafeInteger(number)) throw new Error('MP4 offset exceeds browser limits.')
  return number
}

function boxHeader(view, start, limit) {
  const size = u32(view, start)
  const header = size === 1 ? 16 : 8
  const end = size === 0 ? limit : start + (size === 1 ? u64(view, start + 8) : size)
  if (end > limit || end < start + header) throw new Error('Invalid MP4 box length.')
  return { type: ascii(view, start + 4), data: start + header, end }
}

function boxes(view, start, end) {
  const list = []
  while (start + 8 <= end) {
    const box = boxHeader(view, start, end)
    list.push(box)
    start = box.end
  }
  return list
}

function child(view, parent, type) {
  const box = boxes(view, parent.data, parent.end).find(candidate => candidate.type === type)
  if (!box) throw new Error('MP4 is missing ' + type + '.')
  return box
}

async function sourceReader(source) {
  if (source instanceof Blob) {
    return { size: source.size, read: (start, end) => source.slice(start, end).arrayBuffer() }
  }
  const meta = await fetch(source, { method: 'HEAD' })
  const size = Number(meta.headers.get('Content-Length'))
  if (!meta.ok || !size) throw new Error('The video could not be opened. Check the example folder or choose a local file.')
  return {
    size,
    async read(start, end) {
      const response = await fetch(source, { headers: { Range: 'bytes=' + start + '-' + (end - 1) } })
      if (response.status !== 206) throw new Error('The server did not return the requested video range.')
      return response.arrayBuffer()
    },
  }
}

function readVideoTrack(view, moov) {
  for (const track of boxes(view, moov.data, moov.end).filter(box => box.type === 'trak')) {
    const media = child(view, track, 'mdia')
    if (ascii(view, child(view, media, 'hdlr').data + 8) === 'vide') return media
  }
  throw new Error('MP4 has no video track.')
}

function readSamples(view, table) {
  const sizeBox = child(view, table, 'stsz')
  const uniformSize = u32(view, sizeBox.data + 4)
  const count = u32(view, sizeBox.data + 8)
  if (!count || count > 1_000_000 || (!uniformSize && sizeBox.data + 12 + count * 4 > sizeBox.end)) {
    throw new Error('Unsupported MP4 sample table.')
  }
  const samples = Array.from({ length: count }, (_, index) => ({
    size: uniformSize || u32(view, sizeBox.data + 12 + index * 4),
  }))
  const mapping = child(view, table, 'stsc')
  const offsets = boxes(view, table.data, table.end).find(box => ['stco', 'co64'].includes(box.type))
  if (!offsets) throw new Error('MP4 is missing chunk offsets.')
  const mappingCount = u32(view, mapping.data + 4)
  const offsetCount = u32(view, offsets.data + 4)
  const wide = offsets.type === 'co64'
  let mapIndex = 0
  let index = 0
  for (let chunk = 1; chunk <= offsetCount; chunk++) {
    if (mapIndex + 1 < mappingCount && u32(view, mapping.data + 8 + (mapIndex + 1) * 12) === chunk) mapIndex++
    const perChunk = u32(view, mapping.data + 12 + mapIndex * 12)
    const description = u32(view, mapping.data + 16 + mapIndex * 12)
    if (description !== 1 || perChunk > count - index) throw new Error('Unsupported MP4 chunk layout.')
    let offset = wide ? u64(view, offsets.data + chunk * 8) : u32(view, offsets.data + 4 + chunk * 4)
    for (let j = 0; j < perChunk; j++) {
      samples[index].offset = offset
      offset += samples[index++].size
    }
  }
  if (index !== count) throw new Error('MP4 sample offsets are incomplete.')
  return samples
}

function assignTiming(view, table, samples, timescale) {
  const timing = child(view, table, 'stts')
  const timingCount = u32(view, timing.data + 4)
  let index = 0
  let ticks = 0
  for (let i = 0; i < timingCount; i++) {
    const entry = timing.data + 8 + i * 8
    const number = u32(view, entry)
    const duration = u32(view, entry + 4)
    if (!duration || number > samples.length - index) throw new Error('Invalid MP4 frame timing.')
    for (let n = 0; n < number; n++) {
      Object.assign(samples[index++], { pts: ticks, duration })
      ticks += duration
    }
  }
  if (index !== samples.length) throw new Error('MP4 frame timing is incomplete.')
  const composition = boxes(view, table.data, table.end).find(box => box.type === 'ctts')
  if (composition) {
    index = 0
    const signed = view.getUint8(composition.data) === 1
    const length = u32(view, composition.data + 4)
    for (let i = 0; i < length; i++) {
      const entry = composition.data + 8 + i * 8
      const count = u32(view, entry)
      const offset = signed ? view.getInt32(entry + 4) : u32(view, entry + 4)
      if (count > samples.length - index) throw new Error('Invalid MP4 presentation timing.')
      for (let n = 0; n < count; n++) samples[index++].pts += offset
    }
    if (index !== samples.length) throw new Error('MP4 presentation timing is incomplete.')
  }
  const order = samples.map((sample, decodeIndex) => ({ sample, decodeIndex }))
    .sort((a, b) => a.sample.pts - b.sample.pts)
  const firstPts = order[0].sample.pts
  order.forEach(({ sample }, frame) => {
    sample.frame = frame
    sample.timestamp = Math.round((sample.pts - firstPts) * 1e6 / timescale)
    sample.duration = Math.round(sample.duration * 1e6 / timescale)
  })
  return { order, fps: samples.length * timescale / ticks, duration: ticks / timescale }
}

function hevcCodec(type, description) {
  // ISO/IEC 14496-15 defines the string from the hvcC fields, with the
  // profile-compatibility bits reversed and trailing zero constraints omitted.
  const profile = description[1]
  const profileSpace = ['', 'A', 'B', 'C'][profile >> 6]
  const tier = profile & 0x20 ? 'H' : 'L'
  const compatibility = new DataView(description.buffer).getUint32(2)
  let reversed = 0
  for (let bit = 0; bit < 32; bit++) reversed = (reversed << 1) | ((compatibility >>> bit) & 1)
  const constraints = Array.from(description.slice(6, 12))
  while (constraints.at(-1) === 0) constraints.pop()
  return [type, profileSpace + (profile & 0x1f), (reversed >>> 0).toString(16).toUpperCase(),
    tier + description[12], ...constraints.map(byte => byte.toString(16).padStart(2, '0').toUpperCase())].join('.')
}

function readCodec(view, entry) {
  const avc = ['avc1', 'avc3'].includes(entry.type)
  const hevc = ['hvc1', 'hev1'].includes(entry.type)
  if (!avc && !hevc) {
    throw new Error('This video uses ' + entry.type + '. Export an H.264 MP4 and try again.')
  }
  const config = child(view, { data: entry.data + 78, end: entry.end }, avc ? 'avcC' : 'hvcC')
  const description = new Uint8Array(view.buffer.slice(config.data, config.end))
  const codecLabel = avc ? 'H.264' : 'H.265'
  if (description.length < (avc ? 4 : 23) || description[0] !== 1) {
    throw new Error('The ' + codecLabel + ' video configuration is invalid.')
  }
  const codec = avc ? entry.type + '.' + [...description.slice(1, 4)]
    .map(byte => byte.toString(16).padStart(2, '0')).join('') : hevcCodec(entry.type, description)
  return { description, codec, codecLabel }
}

/**
 * Read an MP4, MOV, or M4V video track and index its frames without decoding them.
 *
 * @param {Blob|string} source - Local footage or a URL that supports byte ranges.
 * @returns {Promise<object>} Video metadata, decoder configuration, and samples with presentation timing.
 */
export async function openMp4(source) {
  const reader = await sourceReader(source)
  let movie
  for (let start = 0; start + 8 <= reader.size;) {
    const header = new DataView(await reader.read(start, Math.min(start + 16, reader.size)))
    const box = boxHeader(header, 0, reader.size - start)
    if (box.type === 'moov') {
      if (box.end > 64 * 1024 * 1024) throw new Error('The MP4 metadata is too large.')
      movie = new DataView(await reader.read(start + box.data, start + box.end))
      break
    }
    start += box.end
  }
  if (!movie) throw new Error('Choose an MP4, MOV or M4V video with H.264 or H.265 encoding.')
  const media = readVideoTrack(movie, { data: 0, end: movie.byteLength })
  const mdhd = child(movie, media, 'mdhd')
  const timescale = u32(movie, mdhd.data + (movie.getUint8(mdhd.data) === 1 ? 20 : 12))
  if (!timescale) throw new Error('MP4 has an invalid video timescale.')
  const table = child(movie, child(movie, media, 'minf'), 'stbl')
  const descriptions = child(movie, table, 'stsd')
  const entry = boxHeader(movie, descriptions.data + 8, descriptions.end)
  const { description, codec, codecLabel } = readCodec(movie, entry)
  const width = movie.getUint16(entry.data + 24)
  const height = movie.getUint16(entry.data + 26)
  if (!width || !height) throw new Error('The video dimensions are invalid.')
  const samples = readSamples(movie, table)
  const sync = boxes(movie, table.data, table.end).find(box => box.type === 'stss')
  if (sync) {
    const count = u32(movie, sync.data + 4)
    for (let i = 0; i < count; i++) samples[u32(movie, sync.data + 8 + i * 4) - 1].key = true
  } else {
    samples.forEach(sample => { sample.key = true })
  }
  for (const sample of samples) {
    if (!sample.size || sample.offset + sample.size > reader.size) throw new Error('MP4 sample lies outside the file.')
  }
  const timing = assignTiming(movie, table, samples, timescale)
  return { reader, samples, codecLabel, config: { codec, codedWidth: width, codedHeight: height, description },
    width, height, frameCount: samples.length, ...timing }
}

export async function verifyVideoDecoder(movie) {
  if (typeof VideoDecoder === 'undefined') {
    throw new Error('Frame decoding requires WebCodecs. Open pXv in a current Edge or Chrome browser.')
  }
  const config = { ...movie.config, hardwareAcceleration: 'no-preference' }
  if (!(await VideoDecoder.isConfigSupported(config)).supported) {
    throw new Error('This browser cannot decode this ' + movie.codecLabel + ' profile. Export an H.264 MP4 or try another browser.')
  }
  return config
}

/**
 * Decode a frame window in presentation order using WebCodecs.
 * Each frame is closed after the callback returns.
 *
 * @param {object} movie - Video metadata returned by ``openMp4``.
 * @param {object} selection - Frame selection and synchronous callback.
 * @param {number} selection.start - First frame index, zero-based.
 * @param {number} selection.stop - Exclusive end frame index.
 * @param {number} [selection.step=1] - Interval between selected frames.
 * @param {function} selection.onFrame - Receives the VideoFrame and its index.
 * @returns {Promise<void>} Resolves after all selected frames have been delivered.
 */
export async function decodeWindow(movie, { start, stop, step = 1, onFrame }) {
  if (![start, stop, step].every(Number.isInteger) || start < 0 || step < 1 ||
      stop <= start || stop > movie.frameCount) throw new Error('Frame selection is outside the video.')
  const selected = new Map()
  let first = Infinity
  let last = -1
  for (let i = start; i < stop; i += step) {
    const { sample, decodeIndex } = movie.order[i]
    selected.set(sample.timestamp, i)
    first = Math.min(first, decodeIndex)
    last = Math.max(last, decodeIndex)
  }
  while (first > 0 && !movie.samples[first].key) first--
  const config = await verifyVideoDecoder(movie)
  let failure
  const decoder = new VideoDecoder({
    output(frame) {
      try {
        const index = selected.get(frame.timestamp)
        if (index !== undefined) {
          onFrame(frame, index)
          selected.delete(frame.timestamp)
        }
      } catch (error) {
        failure = error
      } finally {
        frame.close()
      }
    },
    error(error) { failure = error },
  })
  try {
    decoder.configure(config)
    let index = first
    while (index <= last && !failure) {
      const beginning = movie.samples[index].offset
      let end = beginning + movie.samples[index].size
      let limit = index + 1
      while (limit <= last && end === movie.samples[limit].offset &&
             end - beginning + movie.samples[limit].size < 4 * 1024 * 1024) {
        end += movie.samples[limit++].size
      }
      const bytes = new Uint8Array(await movie.reader.read(beginning, end))
      for (; index < limit && !failure; index++) {
        const sample = movie.samples[index]
        const offset = sample.offset - beginning
        decoder.decode(new EncodedVideoChunk({
          type: sample.key ? 'key' : 'delta',
          timestamp: sample.timestamp,
          duration: sample.duration,
          data: bytes.subarray(offset, offset + sample.size),
        }))
        if (decoder.decodeQueueSize > 16) {
          await new Promise(resolve => {
            const resume = () => {
              clearTimeout(timer)
              decoder.removeEventListener('dequeue', resume)
              resolve()
            }
            const timer = setTimeout(resume, 50)
            decoder.addEventListener('dequeue', resume, { once: true })
          })
        }
      }
    }
    if (!failure) await decoder.flush()
    if (failure) throw failure
    if (selected.size) throw new Error('The browser skipped one or more requested frames.')
  } finally {
    if (decoder.state !== 'closed') decoder.close()
  }
}

import { openMp4, verifyVideoDecoder, decodeWindow } from './mp4.mjs'

let movie

self.onmessage = async ({ data }) => {
  try {
    if (data.type === 'open') {
      const candidate = await openMp4(data.source)
      await verifyVideoDecoder(candidate)
      movie = candidate
      const { width, height, frameCount, fps, duration, codecLabel } = movie
      self.postMessage({ type: 'opened', width, height, frameCount, fps, duration, codecLabel })
      return
    }
    if (!movie) throw new Error('Open a video first.')
    const { width, height } = movie
    const canvas = new OffscreenCanvas(width, height)
    const context = canvas.getContext('2d', { willReadFrequently: true })
    if (data.type === 'preview') {
      await decodeWindow(movie, {
        start: data.frame, stop: data.frame + 1,
        onFrame(frame) { context.drawImage(frame, 0, 0, width, height) },
      })
      const preview = await canvas.convertToBlob({ type: 'image/webp', quality: 0.9 })
      self.postMessage({ type: 'preview', preview, frame: data.frame })
      return
    }
    const { createAnalyzer } = await import('./velocimetry.mjs')
    const { start, stop, step, options } = data
    const analyzer = createAnalyzer(options)
    const total = Math.ceil((stop - start) / step)
    let completed = 0
    let firstImage
    let previousTime = -Infinity
    await decodeWindow(movie, {
      start, stop, step,
      onFrame(frame, index) {
        const time = frame.timestamp / 1e6
        if (time <= previousTime) throw new Error('The decoder returned frames out of presentation order.')
        previousTime = time
        context.drawImage(frame, 0, 0, width, height)
        const rgba = context.getImageData(0, 0, width, height).data
        if (index === start) firstImage = new ImageData(rgba, width, height)
        const gray = new Uint8Array(width * height)
        for (let pixel = 0, offset = 0; pixel < gray.length; pixel++, offset += 4) {
          gray[pixel] = (77 * rgba[offset] + 150 * rgba[offset + 1] + 29 * rgba[offset + 2] + 128) >> 8
        }
        analyzer.push({ gray, width, height, time })
        if (++completed % 8 === 0 || completed === total) self.postMessage({ type: 'progress', completed, total })
      },
    })
    const result = analyzer.finish()
    context.putImageData(firstImage, 0, 0)
    const preview = await canvas.convertToBlob({ type: 'image/webp', quality: 0.9 })
    self.postMessage({ type: 'result', result, preview, frame: start })
  } catch (error) {
    self.postMessage({ type: 'error', message: error.message })
  }
}

import { createServer } from 'node:http'
import { createReadStream } from 'node:fs'
import { mkdtemp, rmdir, stat, unlink, writeFile } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { readPointLayers, readSurvey } from './geopackage.mjs'

const web = new URL('./', import.meta.url)
const example = new URL('../examples/isar-20260804/', web)
const isarVideo = 'DJI_20260804150234_0002_D.MP4'
const isarCaptions = isarVideo.replace(/\.MP4$/, '.SRT')
const port = Number(process.env.PXV_PORT ?? 8040)
const staticFiles = new Map([
  ['/', ['index.html', 'text/html']],
  ['/app.css', ['app.css', 'text/css']],
  ...['app', 'mp4', 'analysis-worker', 'isar', 'geometry', 'survey', 'velocimetry', 'vector-filters', 'xlsx',
    'spatial-reference', 'survey-import', 'calibration', 'import-wizard', 'result-graph', 'project-file']
    .map(name => ['/' + name + '.mjs', [name + '.mjs', 'text/javascript']]),
])

async function sendFile(req, res, file, type, ranges = false) {
  const { size } = await stat(file)
  const headers = { 'Content-Type': type, 'Cache-Control': 'no-cache' }
  if (ranges) headers['Accept-Ranges'] = 'bytes'
  let start = 0
  let end = size - 1
  let status = 200
  if (ranges && req.headers.range) {
    const match = /^bytes=(\d*)-(\d*)$/.exec(req.headers.range)
    if (match?.[1]) {
      start = Number(match[1])
      if (match[2]) end = Math.min(end, Number(match[2]))
    } else if (match?.[2] && Number(match[2]) > 0) {
      start = Math.max(0, size - Number(match[2]))
    } else {
      start = size
    }
    if (!Number.isSafeInteger(start) || start >= size || end < start) {
      res.writeHead(416, { 'Content-Range': `bytes */${size}` }).end()
      return
    }
    headers['Content-Range'] = `bytes ${start}-${end}/${size}`
    status = 206
  }
  headers['Content-Length'] = Math.max(0, end - start + 1)
  res.writeHead(status, headers)
  if (req.method === 'HEAD' || !size) return res.end()
  const stream = createReadStream(file, { start, end })
  res.on('close', () => stream.destroy())
  stream.on('error', () => res.destroy())
  stream.pipe(res)
}

async function importGeoPackage(req, res, url) {
  if (req.headers.origin !== 'http://' + req.headers.host ||
      req.headers['content-type'] !== 'application/geopackage+sqlite3') {
    res.writeHead(403).end('Open GeoPackages from the local pXv page.')
    return
  }
  const maxBytes = 64 * 1024 * 1024
  if (Number(req.headers['content-length']) > maxBytes) {
    res.writeHead(413).end('Choose a GeoPackage no larger than 64 MiB.')
    return
  }
  let size = 0
  const chunks = []
  for await (const chunk of req.iterator({ destroyOnReturn: false })) {
    size += chunk.length
    if (size > maxBytes) {
      res.writeHead(413).end('Choose a GeoPackage no larger than 64 MiB.')
      req.resume()
      return
    }
    chunks.push(chunk)
  }
  const bytes = Buffer.concat(chunks)
  if (bytes.subarray(0, 16).toString('latin1') !== 'SQLite format 3\0') {
    res.writeHead(400).end('This file does not have a GeoPackage database header.')
    return
  }
  const directory = await mkdtemp(join(tmpdir(), 'pxv-survey-'))
  const file = join(directory, 'survey.gpkg')
  try {
    await writeFile(file, bytes, { flag: 'wx' })
    const survey = readPointLayers(file, url.searchParams.get('layer'))
    res.writeHead(200, { 'Content-Type': 'application/json', 'Cache-Control': 'no-store' })
    res.end(JSON.stringify(survey))
  } catch (error) {
    res.writeHead(400, { 'Content-Type': 'text/plain; charset=utf-8' }).end('GeoPackage: ' + error.message)
  } finally {
    await unlink(file).catch(error => { if (error.code !== 'ENOENT') console.error(error) })
    await rmdir(directory).catch(error => console.error(error))
  }
}

const server = createServer(async (req, res) => {
  const host = req.headers.host?.replace(/:\d+$/, '')
  if (!['127.0.0.1', 'localhost'].includes(host) || req.headers['sec-fetch-site'] === 'cross-site') {
    res.writeHead(403).end()
    return
  }
  try {
    const url = new URL(req.url, 'http://localhost')
    const path = url.pathname
    if (req.method === 'POST' && path === '/import/geopackage') {
      await importGeoPackage(req, res, url)
      return
    }
    if (!['GET', 'HEAD'].includes(req.method)) {
      res.writeHead(405, { Allow: 'GET, HEAD' }).end()
      return
    }
    if (path === '/example/survey') {
      const survey = readSurvey(fileURLToPath(new URL('IsarAug2026.gpkg', example)))
      res.writeHead(200, { 'Content-Type': 'application/json', 'Cache-Control': 'no-cache' })
      res.end(req.method === 'HEAD' ? undefined : JSON.stringify(survey))
    } else if (path === '/example/video') {
      await sendFile(req, res, new URL(isarVideo, example), 'video/mp4', true)
    } else if (path === '/example/captions') {
      await sendFile(req, res, new URL(isarCaptions, example), 'text/plain')
    } else if (staticFiles.has(path)) {
      const [file, type] = staticFiles.get(path)
      await sendFile(req, res, new URL(file, web), type + '; charset=utf-8')
    } else {
      res.writeHead(404).end('File not found.')
    }
  } catch (error) {
    if (error.code !== 'ENOENT') console.error(error)
    if (res.headersSent) return res.destroy()
    const status = error.code === 'ENOENT' ? 404 : 500
    res.writeHead(status, { 'Content-Type': 'text/plain; charset=utf-8' })
    res.end(status === 404 ? 'The example file is missing.' : 'Could not open the local file.')
  }
})

server.listen(port, '127.0.0.1', () => {
  console.log(`pXv running at http://127.0.0.1:${server.address().port}`)
})

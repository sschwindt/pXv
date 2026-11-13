import assert from 'node:assert/strict'
import { spawn } from 'node:child_process'
import { once } from 'node:events'
import { mkdir, readFile, stat } from 'node:fs/promises'
import { join } from 'node:path'
import { createInterface } from 'node:readline'
import { fileURLToPath } from 'node:url'
import { createRequire } from 'node:module'
import { openMp4 } from '../../web/mp4.mjs'
import { parseProject } from '../../web/project-file.mjs'

const root = fileURLToPath(new URL('../../', import.meta.url))
const fixture = join(root, 'examples/isar-ci/isar-ci.mp4')
const output = join(root, 'examples/isar-ci-out')
const require = createRequire(import.meta.url)
const { chromium } = require('playwright')

async function startServer() {
  const server = spawn(process.execPath, ['web/server.mjs'], {
    cwd: root,
    env: { ...process.env, PXV_PORT: '0', PXV_ISAR_FIXTURE: '1' },
    stdio: ['ignore', 'pipe', 'inherit'],
  })
  const lines = createInterface({ input: server.stdout })
  const exit = once(server, 'exit')
  try {
    const line = await Promise.race([
      once(lines, 'line').then(([message]) => message),
      exit.then(([code]) => { throw new Error('pXv server exited with code ' + code + '.') }),
    ])
    const url = line.match(/http:\/\/127\.0\.0\.1:\d+/)?.[0]
    assert.ok(url, 'The app must report its local URL.')
    return { server, lines, exit, url }
  } catch (error) {
    server.kill()
    lines.close()
    throw error
  }
}

async function saveDownload(page, name, click) {
  const download = page.waitForEvent('download')
  await click()
  const path = join(output, name)
  await (await download).saveAs(path)
  return path
}

const { size } = await stat(fixture)
assert.ok(size > 0 && size < 10 * 1024 * 1024, 'The example MP4 must stay below 10 MiB.')
const fixtureMovie = await openMp4(new Blob([await readFile(fixture)]))
const stop = fixtureMovie.frameCount
assert.ok(stop > 10 && stop <= 150, 'The example should run in a short frame window.')
await mkdir(output, { recursive: true })
const { server, lines, exit, url } = await startServer()
let browser
try {
  const browserPath = process.env.PXV_CHROME
  browser = await chromium.launch(browserPath ? { executablePath: browserPath } : { channel: 'chrome' })
  const page = await browser.newPage()
  page.setDefaultTimeout(30_000)
  const uncaught = []
  page.on('pageerror', error => uncaught.push(error))
  const config = await (await fetch(url + '/example/config')).json()
  assert.equal(config.fixture, true)
  assert.equal(config.start, 0)

  await page.goto(url)
  await page.getByText('Reference frame 0', { exact: true }).waitFor()
  assert.equal(Number(await page.locator('#stop').inputValue()), stop)
  assert.equal(await page.locator('#survey-speed').textContent(), '1.24 m/s')
  assert.match(await page.locator('#survey-alignment').textContent(), /EPSG:25832/)
  await page.getByRole('button', { name: 'Run analysis' }).click()
  await page.getByText('Analysis complete.', { exact: true }).waitFor({ timeout: 60_000 })
  const median = parseFloat(await page.locator('#video-speed').textContent())
  assert.ok(median > 1.2 && median < 1.9, 'The seeded pulse should yield plausible nonzero tracer speeds.')
  const csv = await saveDownload(page, 'isar-vectors.csv', () => page.getByRole('button', { name: 'Vectors CSV' }).click())
  const [header, ...rows] = (await readFile(csv, 'utf8')).trim().split(/\r?\n/)
  assert.match(header, /"speed_m_s".*"speed_error_m_s"/)
  assert.ok(rows.length >= 5, 'The seeded pulse should produce multiple measured cells.')
  assert.ok(rows.every(row => row.includes('"ptv","25832"')), 'The fixture should produce projected PTV vectors.')

  await page.getByRole('button', { name: 'Export result graph' }).click()
  await page.getByLabel('Width (px)').fill('1200')
  await page.getByLabel('Height (px)').fill('800')
  const graph = await saveDownload(page, 'isar-result.png', () => page.getByRole('button', { name: 'Download PNG' }).click())
  const png = await readFile(graph)
  assert.equal(png.toString('hex', 0, 8), '89504e470d0a1a0a')
  assert.equal(png.readUInt32BE(16), 1200)
  assert.equal(png.readUInt32BE(20), 800)

  const project = await saveDownload(page, 'isar.pxv', () => page.getByRole('button', { name: 'Save project' }).click())
  const saved = parseProject(await readFile(project, 'utf8'))
  assert.equal(saved.video.variant, 'fixture')
  assert.equal(saved.settings.graphWidth, 1200)
  assert.equal(saved.settings.graphHeight, 800)
  assert.equal(saved.survey.epsg, 25832)
  assert.ok(saved.captions.text.includes('FrameCnt:'))
  await page.locator('#speed-limit').fill('1')
  await page.getByRole('button', { name: 'Load project' }).click()
  await page.getByLabel('Project file (.pxv)').setInputFiles(project)
  await page.getByRole('button', { name: 'Load settings' }).click()
  await page.getByText('Project loaded. Run analysis to regenerate the results.').waitFor()
  await page.getByText('Reference frame 0', { exact: true }).waitFor()
  assert.equal(await page.locator('#speed-limit').inputValue(), '3')
  assert.equal(Number(await page.locator('#stop').inputValue()), stop)
  assert.deepEqual(uncaught, [])
  console.log('Isar fixture: ' + stop + ' frames, ' + rows.length + ' measured cells, ' + median + ' m/s tracer median.')
} finally {
  await browser?.close()
  server.kill()
  lines.close()
  await exit
}

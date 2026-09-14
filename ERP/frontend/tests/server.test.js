/**
 * The production server (server.js), run for real as a child process.
 *
 * server.js serves the dist/ next to it, so each instance runs from a temporary
 * copy with a tiny fake dist - no build needed - in front of a fake ERP API.
 */

import assert from 'node:assert/strict'
import { spawn } from 'node:child_process'
import { randomUUID } from 'node:crypto'
import fs from 'node:fs'
import http from 'node:http'
import net from 'node:net'
import os from 'node:os'
import path from 'node:path'
import { after, before, test } from 'node:test'
import { fileURLToPath } from 'node:url'

const SERVER_JS = fileURLToPath(new URL('../server.js', import.meta.url))
const SECRET = `server-test-key-${randomUUID()}`

const cleanups = []

function freePort() {
  return new Promise((resolve, reject) => {
    const probe = net.createServer()
    probe.on('error', reject)
    probe.listen(0, '127.0.0.1', () => {
      const { port } = probe.address()
      probe.close(() => resolve(port))
    })
  })
}

function appDirectory() {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'orialis-erp-server-'))
  fs.copyFileSync(SERVER_JS, path.join(dir, 'server.js'))
  fs.writeFileSync(path.join(dir, 'package.json'), '{"type":"module"}')
  fs.mkdirSync(path.join(dir, 'dist', 'assets'), { recursive: true })
  fs.writeFileSync(path.join(dir, 'dist', 'index.html'), '<!doctype html><div id="root"></div>')
  fs.writeFileSync(path.join(dir, 'dist', 'assets', 'index-abc123.js'), 'console.log("app")')
  cleanups.push(() => fs.rmSync(dir, { recursive: true, force: true }))
  return dir
}

async function startServer(env) {
  const port = await freePort()
  const child = spawn(process.execPath, [path.join(appDirectory(), 'server.js')], {
    env: { ...process.env, ERP_API_URL: '', ERP_API_KEY: '', ...env, PORT: String(port) },
    stdio: ['ignore', 'pipe', 'pipe'],
  })
  cleanups.push(() => child.kill())

  let logs = ''
  child.stdout.on('data', (chunk) => { logs += chunk })
  child.stderr.on('data', (chunk) => { logs += chunk })

  await new Promise((resolve, reject) => {
    const timer = setTimeout(() => reject(new Error(`server did not start:\n${logs}`)), 10_000)
    child.stdout.on('data', () => {
      if (logs.includes('ERP_API_KEY is')) { clearTimeout(timer); resolve() }
    })
    child.on('exit', (code) => { clearTimeout(timer); reject(new Error(`server exited (${code}):\n${logs}`)) })
  })

  return { base: `http://127.0.0.1:${port}`, logs: () => logs }
}

let upstream
const upstreamRequests = []
let withKey
let withoutKey
let apiDown
let unroutable

before(async () => {
  upstream = http.createServer((req, res) => {
    upstreamRequests.push({ url: req.url, headers: req.headers })
    if (req.url.startsWith('/api/v1/very-slow')) {
      // Slower than Node's default HTTP agent socket timeout (5 s).
      setTimeout(() => { res.writeHead(200, { 'Content-Type': 'application/json' }); res.end('{"slow":true}') }, 6500)
      return
    }
    if (req.url.startsWith('/api/v1/slow')) {
      // Answers late, so a client can cancel while the request is in flight.
      setTimeout(() => { res.writeHead(200, { 'Content-Type': 'application/json' }); res.end('{}') }, 1500)
      return
    }
    const known = req.url.startsWith('/api/v1/suppliers')
    res.writeHead(known ? 200 : 404, { 'Content-Type': 'application/json' })
    res.end(known ? '{"data":[]}' : '{"detail":"Not Found"}')
  })
  await new Promise((resolve) => upstream.listen(0, '127.0.0.1', resolve))
  const upstreamUrl = `http://127.0.0.1:${upstream.address().port}/`

  withKey = await startServer({ ERP_API_URL: upstreamUrl, ERP_API_KEY: SECRET })
  withoutKey = await startServer({ ERP_API_URL: upstreamUrl })
  apiDown = await startServer({ ERP_API_URL: `http://127.0.0.1:${await freePort()}`, ERP_API_KEY: SECRET })
  // A non-routable address: the TCP connection neither succeeds nor is refused.
  unroutable = await startServer({ ERP_API_URL: 'http://10.255.255.1:8000', ERP_API_KEY: SECRET })
})

test('a slow API is waited for, not cut off by the HTTP agent default of 5 s', { timeout: 20_000 }, async () => {
  const started = Date.now()
  const response = await fetch(`${withKey.base}/api/v1/very-slow`)
  const body = await response.text()
  const elapsed = Date.now() - started
  assert.equal(response.status, 200, `${response.status} after ${elapsed} ms: ${body}`)
  assert.equal(body, '{"slow":true}')
})

test('an API that cannot be connected to fails fast with a JSON 502', { timeout: 20_000 }, async () => {
  const started = Date.now()
  const response = await fetch(`${unroutable.base}/api/v1/stats/overview`)
  const body = await response.text()
  const elapsed = Date.now() - started
  assert.equal(response.status, 502, `${response.status} after ${elapsed} ms: ${body}`)
  assert.deepEqual(JSON.parse(body), { detail: 'The ERP API could not be reached.' })
  assert.ok(elapsed < 8000, `took ${elapsed} ms`)
})

after(async () => {
  for (const cleanup of cleanups.reverse()) cleanup()
  await new Promise((resolve) => upstream?.close(resolve))
})

test('proxies /api with the server-side key and replaces a key forged by the client', async () => {
  upstreamRequests.length = 0
  const response = await fetch(`${withKey.base}/api/v1/suppliers?page_size=1`, {
    headers: { 'X-API-Key': 'forged-by-client' },
  })
  const body = await response.text()

  assert.equal(response.status, 200)
  assert.equal(upstreamRequests.length, 1)
  assert.equal(upstreamRequests[0].url, '/api/v1/suppliers?page_size=1')
  assert.equal(upstreamRequests[0].headers['x-api-key'], SECRET)
  assert.equal(body.includes(SECRET), false)
  for (const [, value] of response.headers) assert.equal(value.includes(SECRET), false)
})

test('without ERP_API_KEY, a client key is still stripped, never forwarded', async () => {
  upstreamRequests.length = 0
  await fetch(`${withoutKey.base}/api/v1/suppliers`, { headers: { 'X-API-Key': SECRET } })
  assert.equal(upstreamRequests[0].headers['x-api-key'], undefined)
  assert.match(withoutKey.logs(), /ERP_API_KEY is NOT SET/)
})

test('backend errors stay JSON: an unknown API route is the API 404, not index.html', async () => {
  for (const url of ['/api/v1/expenses', '/api']) {
    const response = await fetch(withKey.base + url)
    assert.equal(response.status, 404, url)
    assert.match(response.headers.get('content-type'), /application\/json/, url)
    assert.deepEqual(await response.json(), { detail: 'Not Found' }, url)
  }
})

test('an unreachable API answers a JSON 502', async () => {
  const response = await fetch(`${apiDown.base}/api/v1/stats/overview`)
  assert.equal(response.status, 502)
  assert.match(response.headers.get('content-type'), /application\/json/)
  assert.deepEqual(await response.json(), { detail: 'The ERP API could not be reached.' })
})

test('SPA fallback for routes, 404 for missing assets, 405 for writes', async () => {
  const root = await fetch(`${withKey.base}/`)
  assert.equal(root.status, 200)
  assert.equal(root.headers.get('cache-control'), 'no-cache')
  const html = await root.text()

  const route = await fetch(`${withKey.base}/invoices`)
  assert.equal(route.status, 200)
  assert.equal(await route.text(), html)

  const asset = await fetch(`${withKey.base}/assets/index-abc123.js`)
  assert.equal(asset.status, 200)
  assert.match(asset.headers.get('cache-control'), /immutable/)

  assert.equal((await fetch(`${withKey.base}/assets/index-OLDHASH.js`)).status, 404)
  assert.equal((await fetch(`${withKey.base}/`, { method: 'POST' })).status, 405)
})

test('a request cancelled by the browser is not reported as an API failure', async () => {
  const controller = new AbortController()
  const pending = fetch(`${withKey.base}/api/v1/slow`, { signal: controller.signal })
  await new Promise((resolve) => setTimeout(resolve, 300))
  controller.abort()
  await assert.rejects(pending, { name: 'AbortError' })

  // Let the upstream answer arrive after the cancellation, then check.
  await new Promise((resolve) => setTimeout(resolve, 1600))
  assert.doesNotMatch(withKey.logs(), /upstream request failed/)
  assert.equal((await fetch(`${withKey.base}/api/v1/suppliers`)).status, 200, 'server still healthy')
})

test('the key never appears in the logs, which carry paths only', async () => {
  const logs = withKey.logs() + apiDown.logs()
  assert.equal(logs.includes(SECRET), false)
  assert.equal(logs.includes('forged-by-client'), false)
  assert.equal(logs.includes('page_size'), false)
  assert.match(withKey.logs(), /GET \/api\/v1\/suppliers 200/)
})

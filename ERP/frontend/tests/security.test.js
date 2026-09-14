/**
 * ERP_API_KEY must reach the ERP API and nothing else.
 *
 * These tests run the REAL Vite dev server and a real production build, with a
 * fake upstream standing in for the API, and look for the key everywhere the
 * browser could see it.
 */

import assert from 'node:assert/strict'
import { randomUUID } from 'node:crypto'
import fs from 'node:fs'
import http from 'node:http'
import os from 'node:os'
import path from 'node:path'
import { after, before, test } from 'node:test'
import { fileURLToPath } from 'node:url'

import { build, createServer } from 'vite'

import { assertNoPublicSecrets, createApiProxy, DEFAULT_API_URL, resolveApiConfig } from '../apiProxy.js'

const FRONTEND = fileURLToPath(new URL('..', import.meta.url))
const CONFIG_FILE = path.join(FRONTEND, 'vite.config.js')
const SECRET = `test-key-${randomUUID()}`

function listFiles(directory) {
  return fs.readdirSync(directory, { withFileTypes: true }).flatMap((entry) => {
    const full = path.join(directory, entry.name)
    return entry.isDirectory() ? listFiles(full) : [full]
  })
}

// ---------------------------------------------------------------------------
// Configuration, without a server
// ---------------------------------------------------------------------------

test('the API URL defaults to port 8001 and tolerates a trailing slash', () => {
  assert.equal(DEFAULT_API_URL, 'http://127.0.0.1:8001')
  assert.deepEqual(resolveApiConfig({}), { apiUrl: 'http://127.0.0.1:8001', apiKey: '' })
  assert.equal(resolveApiConfig({ ERP_API_URL: 'http://127.0.0.1:8011/' }).apiUrl, 'http://127.0.0.1:8011')
})

test('a VITE_ variable holding a secret stops the configuration', () => {
  assert.throws(() => assertNoPublicSecrets({ VITE_ERP_API_KEY: 'x' }), /VITE_ERP_API_KEY/)
  assert.throws(() => assertNoPublicSecrets({ ERP_API_KEY: 'abc', VITE_BACKEND: 'abc' }), /VITE_BACKEND/)
  assert.doesNotThrow(() => assertNoPublicSecrets({ ERP_API_KEY: 'abc', VITE_TITLE: 'ERP' }))
})

test('without a configured key, a browser-supplied key is still removed', () => {
  const handlers = {}
  createApiProxy({ apiUrl: 'http://api', apiKey: '' })['/api'].configure({
    on: (event, handler) => { handlers[event] = handler },
  })
  const headers = new Map([['x-api-key', 'forged-by-browser']])
  handlers.proxyReq({
    setHeader: (name, value) => headers.set(name.toLowerCase(), value),
    removeHeader: (name) => headers.delete(name.toLowerCase()),
  })
  assert.equal(headers.has('x-api-key'), false)
})

test('no source file reads an environment variable or sets the key header', () => {
  // Naming ERP_API_KEY in an error message is fine; READING it is not.
  for (const file of listFiles(path.join(FRONTEND, 'src'))) {
    const source = fs.readFileSync(file, 'utf8')
    assert.doesNotMatch(source, /import\.meta\.env|process\.env|x-api-key/i, path.relative(FRONTEND, file))
  }
})

// ---------------------------------------------------------------------------
// The real dev server, in front of a fake ERP API
// ---------------------------------------------------------------------------

let upstream
let upstreamRequests = []
let vite
let baseUrl

before(async () => {
  upstream = http.createServer((req, res) => {
    upstreamRequests.push({ url: req.url, headers: req.headers })
    const body = req.url.startsWith('/api/v1/suppliers')
      ? { data: [], pagination: { total_records: 0 } }
      : { detail: 'Not Found' }
    res.writeHead(req.url.startsWith('/api/v1/suppliers') ? 200 : 404, { 'Content-Type': 'application/json' })
    res.end(JSON.stringify(body))
  })
  await new Promise((resolve) => upstream.listen(0, '127.0.0.1', resolve))

  process.env.ERP_API_URL = `http://127.0.0.1:${upstream.address().port}`
  process.env.ERP_API_KEY = SECRET

  vite = await createServer({
    configFile: CONFIG_FILE,
    logLevel: 'silent',
    server: { port: 0, strictPort: false },
  })
  await vite.listen()
  baseUrl = `http://127.0.0.1:${vite.httpServer.address().port}`
})

after(async () => {
  await vite?.close()
  await new Promise((resolve) => upstream?.close(resolve))
  delete process.env.ERP_API_URL
  delete process.env.ERP_API_KEY
})

test('the proxy injects ERP_API_KEY in Node and overrides a key forged by the browser', async () => {
  upstreamRequests = []
  const response = await fetch(`${baseUrl}/api/v1/suppliers?page_size=1`, {
    headers: { 'X-API-Key': 'forged-by-browser' },
  })
  const text = await response.text()

  assert.equal(response.status, 200)
  assert.equal(upstreamRequests.length, 1)
  assert.equal(upstreamRequests[0].headers['x-api-key'], SECRET)
  assert.equal(upstreamRequests[0].url, '/api/v1/suppliers?page_size=1')

  assert.equal(text.includes(SECRET), false)
  for (const [, value] of response.headers) assert.equal(value.includes(SECRET), false)
})

test('an unknown API path keeps the upstream 404 instead of the SPA page', async () => {
  const response = await fetch(`${baseUrl}/api/v1/expenses`)
  assert.equal(response.status, 404)
  assert.deepEqual(await response.json(), { detail: 'Not Found' })
})

test('nothing the dev server sends to the browser contains the key', async () => {
  for (const url of ['/', '/src/main.jsx', '/src/api.js', '/@vite/client', '/@vite/env']) {
    const response = await fetch(baseUrl + url)
    assert.equal(response.ok, true, url)
    assert.equal((await response.text()).includes(SECRET), false, url)
  }
})

test('the production bundle does not contain the key', async () => {
  const outDir = fs.mkdtempSync(path.join(os.tmpdir(), 'orialis-erp-build-'))
  try {
    await build({ configFile: CONFIG_FILE, logLevel: 'silent', build: { outDir, emptyOutDir: true } })
    const files = listFiles(outDir)
    assert.ok(files.some((file) => file.endsWith('.js')), 'the build produced JavaScript')
    for (const file of files) {
      assert.equal(fs.readFileSync(file, 'utf8').includes(SECRET), false, path.basename(file))
    }
  } finally {
    fs.rmSync(outDir, { recursive: true, force: true })
  }
})

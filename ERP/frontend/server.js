/**
 * Orialis ERP frontend - production server.
 *
 * The production counterpart of vite.config.js + apiProxy.js. Same contract,
 * different moment:
 *
 *   npm run dev   vite.config.js   developer machine, port 5174
 *   npm start     server.js        inside the container, on $PORT
 *
 *   Browser --GET /api/v1/...--> this server (Node) --X-API-Key--> ERP API
 *     (same origin, no key)
 *
 * Nothing under src/ knows either layer exists: the React code only requests
 * the relative path /api/v1/..., which is why this file could be added without
 * touching a component.
 *
 * WHY A SERVER AND NOT A STATIC HOST: a static host cannot hold a secret, and
 * the ERP API requires ERP_API_KEY. The key lives in this process's memory,
 * is attached to the outbound request, and never travels to the browser.
 *
 * WHY NO FRAMEWORK: the Node standard library does the whole job, so the
 * runtime image carries no node_modules and no third-party code.
 *
 * REQUEST PIPELINE - the order is the design:
 *
 *   1. /api, /api/*               -> proxied to ERP_API_URL, key attached
 *   2. any method but GET/HEAD    -> 405
 *   3. a file from dist/          -> served
 *   4. a path with an extension   -> 404 (a MISSING asset, not a route)
 *   5. anything else              -> dist/index.html (the SPA fallback)
 *
 * Step 1 comes first and always returns: an unknown API path is the API's own
 * JSON 404, a broken upstream is a JSON 502 from here, and nothing under /api
 * ever receives index.html - which would otherwise reach the frontend as a
 * "successful" response that fails to parse.
 */

import fs from 'node:fs'
import http from 'node:http'
import https from 'node:https'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

// ---------------------------------------------------------------------------
// Configuration
// ---------------------------------------------------------------------------

const HERE = path.dirname(fileURLToPath(import.meta.url))
const DIST = path.join(HERE, 'dist')

// The platform injects $PORT; 8080 is the fallback for a local `docker run`.
const PORT = Number(process.env.PORT) || 8080

// 0.0.0.0, never 127.0.0.1: inside a container, loopback is unreachable from
// the host and from the platform's router.
const HOST = '0.0.0.0'

// Same default as the dev proxy (apiProxy.js). A trailing slash is stripped so
// `https://api.example/` does not build `//api/v1/...`.
const API_URL = (process.env.ERP_API_URL || 'http://127.0.0.1:8001').trim().replace(/\/+$/, '')

// Read once. NEVER logged, never put in a response, never in an error message.
// Used in exactly one place: buildUpstreamHeaders().
const API_KEY = (process.env.ERP_API_KEY || '').trim()

// Two different failures, two different answers:
//   the TCP connection never completes  -> 502 "could not be reached", after 4 s
//   connected, but no answer in time    -> 504 "did not respond in time", after 30 s
// The connect limit is explicit on purpose. Without it, a connection that
// hangs (an address that silently drops packets) is cut by Node's default
// HTTP agent socket timeout (5 s) and reported as a slow API instead.
const CONNECT_TIMEOUT_MS = 4_000
const UPSTREAM_TIMEOUT_MS = 30_000

// Parsed at boot, so a malformed URL fails immediately rather than as an
// opaque 502 on the first request. The value itself is not printed.
let API_ORIGIN
try {
  API_ORIGIN = new URL(API_URL)
  if (API_ORIGIN.protocol !== 'http:' && API_ORIGIN.protocol !== 'https:') {
    throw new Error(`unsupported protocol "${API_ORIGIN.protocol}"`)
  }
} catch (error) {
  console.error(`[orialis-erp] ERP_API_URL is not a usable URL: ${error.message}`)
  process.exit(1)
}

const transport = API_ORIGIN.protocol === 'https:' ? https : http

// ---------------------------------------------------------------------------
// Static assets
// ---------------------------------------------------------------------------

const CONTENT_TYPES = {
  '.html': 'text/html; charset=utf-8',
  '.js': 'text/javascript; charset=utf-8',
  '.mjs': 'text/javascript; charset=utf-8',
  '.css': 'text/css; charset=utf-8',
  '.json': 'application/json; charset=utf-8',
  '.map': 'application/json; charset=utf-8',
  '.svg': 'image/svg+xml',
  '.png': 'image/png',
  '.ico': 'image/x-icon',
  '.woff2': 'font/woff2',
  '.txt': 'text/plain; charset=utf-8',
}

/**
 * dist/ walked ONCE, at boot, into an exact URL-path -> file map. A lookup in
 * a map has no path-traversal surface: `/../../etc/passwd` is simply not a key.
 */
function collectAssets(directory, prefix = '') {
  const assets = new Map()
  for (const entry of fs.readdirSync(directory, { withFileTypes: true })) {
    const absolute = path.join(directory, entry.name)
    const urlPath = `${prefix}/${entry.name}`
    if (entry.isDirectory()) {
      for (const [key, value] of collectAssets(absolute, urlPath)) assets.set(key, value)
    } else if (entry.isFile()) {
      assets.set(urlPath, {
        file: absolute,
        size: fs.statSync(absolute).size,
        type: CONTENT_TYPES[path.extname(entry.name).toLowerCase()] || 'application/octet-stream',
      })
    }
  }
  return assets
}

if (!fs.existsSync(path.join(DIST, 'index.html'))) {
  console.error('[orialis-erp] dist/index.html is missing. Run `npm run build` before `npm start`.')
  process.exit(1)
}

const ASSETS = collectAssets(DIST)
const INDEX = ASSETS.get('/index.html')

/**
 * Fingerprinted Vite assets (/assets/index-AbC123.js) never change content
 * under the same name: cache them for a year. index.html points at the current
 * fingerprints, so it must always be revalidated.
 */
function cacheControlFor(urlPath) {
  if (urlPath === '/index.html') return 'no-cache'
  return urlPath.startsWith('/assets/') ? 'public, max-age=31536000, immutable' : 'public, max-age=3600'
}

function sendAsset(request, response, urlPath, asset) {
  response.writeHead(200, {
    'Content-Type': asset.type,
    'Content-Length': asset.size,
    'Cache-Control': cacheControlFor(urlPath),
    'X-Content-Type-Options': 'nosniff',
  })
  if (request.method === 'HEAD') return response.end()
  fs.createReadStream(asset.file).pipe(response)
}

// ---------------------------------------------------------------------------
// The API proxy
// ---------------------------------------------------------------------------

// Headers describing THIS connection, which must not be copied onto another.
const HOP_BY_HOP = new Set([
  'connection', 'keep-alive', 'proxy-authenticate', 'proxy-authorization',
  'te', 'trailer', 'transfer-encoding', 'upgrade',
])

function buildUpstreamHeaders(request) {
  const headers = {}
  for (const [name, value] of Object.entries(request.headers)) {
    if (HOP_BY_HOP.has(name)) continue
    // Rewritten below to the upstream host.
    if (name === 'host') continue
    // DROPPED, never forwarded: a caller must not be able to substitute its
    // own credential for ours - nor probe the API with a guessed one.
    if (name === 'x-api-key') continue
    headers[name] = value
  }
  headers.host = API_ORIGIN.host

  // THE ONE PLACE THE CREDENTIAL IS ATTACHED, on the outbound request, in Node.
  // Without a key the header is simply absent and the API answers 401, which
  // the frontend renders as a readable message.
  if (API_KEY) headers['x-api-key'] = API_KEY
  return headers
}

/** Every failure reachable from /api answers JSON in FastAPI's `detail` shape. */
function sendApiError(response, status, detail) {
  if (response.headersSent) return response.destroy()
  const body = JSON.stringify({ detail })
  response.writeHead(status, {
    'Content-Type': 'application/json; charset=utf-8',
    'Content-Length': Buffer.byteLength(body),
    'Cache-Control': 'no-store',
  })
  response.end(body)
}

function proxyToApi(request, response) {
  const base = API_ORIGIN.pathname.replace(/\/+$/, '')
  const target = new URL(base + request.url, API_ORIGIN)
  let settled = false
  // Set when the BROWSER cancels (a screen change aborts its pending fetches).
  // Tearing down the upstream request then raises ECONNRESET, which is not an
  // API failure and must neither be logged as one nor answered.
  let clientGone = false

  const upstream = transport.request(
    target,
    { method: request.method, headers: buildUpstreamHeaders(request) },
    (upstreamResponse) => {
      settled = true
      const headers = {}
      for (const [name, value] of Object.entries(upstreamResponse.headers)) {
        if (!HOP_BY_HOP.has(name)) headers[name] = value
      }
      headers['cache-control'] = headers['cache-control'] || 'no-store'
      // The upstream status passes through UNCHANGED: a 404 stays a 404, a 401
      // stays a 401, a 422 keeps its validation detail.
      response.writeHead(upstreamResponse.statusCode, headers)
      // A body cut short (either side going away) must end this response,
      // not surface as an unhandled stream error.
      upstreamResponse.on('error', () => response.destroy())
      upstreamResponse.pipe(response)
    },
  )

  const unreachable = (reason) => {
    if (settled || clientGone) return
    settled = true
    // `reason` is an error code (ECONNREFUSED, ENOTFOUND...) or "connect
    // timeout": it describes the connection, never the credential.
    console.error(`[orialis-erp] upstream request failed: ${reason}`)
    upstream.destroy()
    sendApiError(response, 502, 'The ERP API could not be reached.')
  }

  upstream.on('socket', (socket) => {
    // A reused keep-alive socket is already connected: nothing to time.
    if (!socket.connecting) return
    const timer = setTimeout(() => unreachable('connect timeout'), CONNECT_TIMEOUT_MS)
    socket.once('connect', () => clearTimeout(timer))
    socket.once('close', () => clearTimeout(timer))
  })

  upstream.setTimeout(UPSTREAM_TIMEOUT_MS, () => {
    // Still connecting means the API was never reached, whatever timer fired.
    if (upstream.socket?.connecting) return unreachable('connect timeout')
    if (settled || clientGone) return
    settled = true
    console.error('[orialis-erp] upstream request failed: response timeout')
    upstream.destroy()
    sendApiError(response, 504, 'The ERP API did not respond in time.')
  })

  upstream.on('error', (error) => {
    if (clientGone) return
    // Already answered (timeout) or already streaming: just end this response.
    if (settled) return response.destroy()
    unreachable(error.code || error.message)
  })

  // The browser went away before the answer was complete: stop holding the
  // upstream socket.
  response.on('close', () => {
    if (response.writableFinished) return
    clientGone = true
    if (!upstream.destroyed) upstream.destroy()
  })

  request.pipe(upstream)
}

// ---------------------------------------------------------------------------
// The request pipeline
// ---------------------------------------------------------------------------

const server = http.createServer((request, response) => {
  const started = Date.now()

  let pathname
  try {
    pathname = decodeURI(new URL(request.url, 'http://localhost').pathname)
  } catch {
    return sendApiError(response, 400, 'Malformed request path.')
  }

  // The PATH only: never headers, never the query string, never the key.
  response.on('finish', () => {
    console.log(`${request.method} ${pathname} ${response.statusCode} ${Date.now() - started}ms`)
  })

  // 1. The API proxy, first and unconditional.
  if (pathname === '/api' || pathname.startsWith('/api/')) {
    return proxyToApi(request, response)
  }

  // 2. Everything below serves documents.
  if (request.method !== 'GET' && request.method !== 'HEAD') {
    response.writeHead(405, { Allow: 'GET, HEAD', 'Content-Length': 0 })
    return response.end()
  }

  // 3. A real file.
  const asset = ASSETS.get(pathname === '/' ? '/index.html' : pathname)
  if (asset) return sendAsset(request, response, pathname === '/' ? '/index.html' : pathname, asset)

  // 4. A missing file is a 404, not a route (e.g. a stale fingerprinted asset).
  if (path.extname(pathname)) {
    response.writeHead(404, { 'Content-Type': 'text/plain; charset=utf-8' })
    return response.end('Not found\n')
  }

  // 5. The SPA fallback. The screens live in the URL hash, so any other path
  // is the same single document.
  return sendAsset(request, response, '/index.html', INDEX)
})

server.listen(PORT, HOST, () => {
  console.log(`[orialis-erp] frontend listening on http://${HOST}:${PORT}`)
  console.log(`[orialis-erp] proxying /api/* -> ${API_ORIGIN.origin}`)
  // Presence reported, value never.
  console.log(`[orialis-erp] ERP_API_KEY is ${API_KEY ? 'set' : 'NOT SET - the API will answer 401'}`)
})

// ---------------------------------------------------------------------------
// Graceful shutdown
// ---------------------------------------------------------------------------
// Node runs as PID 1 in the container (exec-form CMD), and PID 1 ignores any
// signal it has no handler for - so these handlers are what make `docker stop`
// and a platform redeploy a clean handover instead of a SIGKILL.

let shuttingDown = false

function shutdown(signal) {
  if (shuttingDown) return
  shuttingDown = true
  console.log(`[orialis-erp] ${signal} received, shutting down`)

  server.close(() => {
    console.log('[orialis-erp] closed cleanly')
    process.exit(0)
  })
  // Idle keep-alive sockets would otherwise hold close() open until they time out.
  server.closeIdleConnections()

  setTimeout(() => {
    console.error('[orialis-erp] forced exit after 10s')
    process.exit(1)
  }, 10_000).unref()
}

process.on('SIGTERM', () => shutdown('SIGTERM'))
process.on('SIGINT', () => shutdown('SIGINT'))

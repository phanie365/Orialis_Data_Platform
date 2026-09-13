/**
 * Orialis CRM frontend - production server.
 *
 * This file is the production counterpart of `vite.config.js`. The two do the
 * same two jobs, at two different moments:
 *
 *   vite.config.js   runs on a developer machine, `npm run dev`
 *   server.js        runs inside the container, on Render
 *
 * Everything under `src/` is unaware that either exists. The React code asks
 * for the relative path `/api/v1/...` and nothing else - see
 * src/api/client.js - which is precisely what allowed this layer to be
 * written afterwards without touching a single component.
 *
 * ---------------------------------------------------------------------------
 * WHY A NODE SERVER AND NOT A STATIC HOST
 * ---------------------------------------------------------------------------
 *
 * A static host (Netlify, S3, Render Static Site) can serve `dist/` and can
 * even do an SPA fallback. What it cannot do is HOLD A SECRET. The CRM API
 * requires an `X-API-Key` header, and there are only two places that header
 * can be attached:
 *
 *   - in the browser, which means shipping the key in the bundle, which means
 *     publishing it; or
 *   - on a server the user never sees.
 *
 * This is that server. The key is read from the environment at runtime, lives
 * only in this process memory, and is written onto the request as it leaves
 * Node for FastAPI - after the browser has already been served. The browser
 * never sends it, never receives it, and cannot observe it.
 *
 * ---------------------------------------------------------------------------
 * WHY NO FRAMEWORK
 * ---------------------------------------------------------------------------
 *
 * `express` + `http-proxy-middleware` would be ~70 transitive packages to do
 * what the Node standard library already does in the lines below. This
 * project has refused a chart library and a UI kit for the same reason, and
 * the trade is even better here: with zero runtime dependencies the runtime
 * image needs no `node_modules` at all, which removes both the install step
 * and the whole supply-chain surface from the deployed artefact.
 *
 * ---------------------------------------------------------------------------
 * THE REQUEST PIPELINE - ORDER IS THE WHOLE DESIGN
 * ---------------------------------------------------------------------------
 *
 *   1. /api/*                    -> proxied to CRM_API_URL, key attached
 *   2. a known file              -> served from dist/
 *   3. a path with an extension  -> 404 (a MISSING asset, not a route)
 *   4. anything else             -> dist/index.html, 200  (the SPA fallback)
 *
 * Step 1 must come before step 4, and step 3 must exist. The reason is the
 * single most common way to break an SPA that talks to an API:
 *
 *   A naive fallback - "anything I do not recognise gets index.html" - turns
 *   `GET /api/v1/nonexistent` into `200 OK` with an HTML body. The frontend
 *   then sees `response.ok === true`, calls `response.json()` on a page of
 *   HTML, and reports an unintelligible parse error instead of the 404 the
 *   API actually returned. Errors stop being readable exactly when they
 *   matter most.
 *
 * So `/api` is handled first and returns unconditionally: an unknown API path
 * is the FastAPI 404, a broken upstream is a JSON 502 from here, and under no
 * circumstance does anything below `/api` ever receive `index.html`.
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

// Render injects the port to listen on as $PORT and routes external traffic
// to it. The fallback is for local `docker run` and `npm start`, where nobody
// sets it.
const PORT = Number(process.env.PORT) || 8080

// 0.0.0.0, never 127.0.0.1. Inside a container, 127.0.0.1 is the container
// own loopback: the process would start, report itself healthy, and be
// unreachable from the host and from the Render router alike. Binding to all
// interfaces is what makes the published port mean anything.
const HOST = '0.0.0.0'

// Trailing slashes are stripped so that both of these behave identically:
//
//   CRM_API_URL=https://orialis-data-platform.onrender.com
//   CRM_API_URL=https://orialis-data-platform.onrender.com/
//
// Without this, the second form would build `//api/v1/clients` - a path the
// API does not serve, producing a 404 that looks like a code bug.
const API_URL = (process.env.CRM_API_URL || 'http://127.0.0.1:8000').replace(/\/+$/, '')

// Read once, kept in memory, and NEVER written to a log, a header sent to the
// browser, an error message, or a response body. The only place it is used is
// `buildUpstreamHeaders()`.
const API_KEY = process.env.CRM_API_KEY || ''

// Parsed at boot so a malformed URL fails immediately and loudly, rather than
// on the first user request as an opaque 502.
let API_ORIGIN
try {
  API_ORIGIN = new URL(API_URL)
  if (API_ORIGIN.protocol !== 'http:' && API_ORIGIN.protocol !== 'https:') {
    throw new Error(`unsupported protocol "${API_ORIGIN.protocol}"`)
  }
} catch (error) {
  console.error(`[orialis] CRM_API_URL is not a usable URL: ${error.message}`)
  process.exit(1)
}

// http vs https chosen from the target, not hardcoded. The Render public API
// URL is https; a local backend or an internal Render service address is http.
const transport = API_ORIGIN.protocol === 'https:' ? https : http

// Upstream ceiling. Without it a hung backend would hold browser connections
// open until the platform own timeout, and the dashboard would spin with no
// explanation. 30s is generous for an API whose slowest call is a paginated
// query.
const UPSTREAM_TIMEOUT_MS = 30_000

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
  '.jpg': 'image/jpeg',
  '.jpeg': 'image/jpeg',
  '.webp': 'image/webp',
  '.avif': 'image/avif',
  '.gif': 'image/gif',
  '.ico': 'image/x-icon',
  '.woff': 'font/woff',
  '.woff2': 'font/woff2',
  '.ttf': 'font/ttf',
  '.txt': 'text/plain; charset=utf-8',
}

/**
 * Walk dist/ once, at boot, into an exact URL-path -> file map.
 *
 * Building a manifest rather than resolving paths per request is a security
 * decision as much as a performance one. A server that turns a request path
 * into a filesystem path has to defend against traversal - `..`, encoded
 * separators, symlinks, Windows-vs-POSIX quirks - and those defences are
 * famously easy to get subtly wrong.
 *
 * A map lookup has no such attack surface. `/../../etc/passwd` is not a key,
 * so it is not a file, so it is a 404. The set is tiny and immutable inside
 * the image, so there is nothing to invalidate.
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
  console.error(
    '[orialis] dist/index.html is missing. Run `npm run build` before `npm start`.\n' +
      '          Inside Docker this cannot happen: the builder stage produces dist/.',
  )
  process.exit(1)
}

const ASSETS = collectAssets(DIST)
const INDEX = ASSETS.get('/index.html')

/**
 * Cache-Control, decided by how the filename was produced.
 *
 * Vite fingerprints every asset - `index-BDDpUBAl.js`. A fingerprinted name
 * refers to exactly one byte sequence forever: a new build produces a new
 * name, never new content under the old name. Such a file can be cached
 * immutably for a year with no staleness risk.
 *
 * index.html is the opposite. It is the document that POINTS at the current
 * fingerprints, so a cached copy is how a browser ends up requesting last
 * week bundle after a deploy. It must always be revalidated.
 */
function cacheControlFor(urlPath) {
  if (urlPath === '/index.html') return 'no-cache'
  return urlPath.startsWith('/assets/')
    ? 'public, max-age=31536000, immutable'
    : 'public, max-age=3600'
}

function sendAsset(request, response, urlPath, asset, status = 200) {
  response.writeHead(status, {
    'Content-Type': asset.type,
    'Content-Length': asset.size,
    'Cache-Control': cacheControlFor(urlPath),
    'X-Content-Type-Options': 'nosniff',
  })
  // HEAD must produce the same headers as GET, and no body.
  if (request.method === 'HEAD') return response.end()
  fs.createReadStream(asset.file).pipe(response)
}

// ---------------------------------------------------------------------------
// The API proxy
// ---------------------------------------------------------------------------

/**
 * Headers that describe THIS connection rather than the message, and must not
 * be copied onto another one. Forwarding `connection` or `transfer-encoding`
 * to the upstream - or back to the browser - produces framing bugs that look
 * like random truncation.
 */
const HOP_BY_HOP = new Set([
  'connection',
  'keep-alive',
  'proxy-authenticate',
  'proxy-authorization',
  'te',
  'trailer',
  'transfer-encoding',
  'upgrade',
])

function buildUpstreamHeaders(request) {
  const headers = {}

  for (const [name, value] of Object.entries(request.headers)) {
    // Node lowercases incoming header names, so these comparisons are total.
    if (HOP_BY_HOP.has(name)) continue

    // `host` is rewritten below to the upstream host - the equivalent of the
    // Vite `changeOrigin: true`. Forwarding the browser host would make the
    // request look like it was addressed to the frontend.
    if (name === 'host') continue

    // Deliberately dropped, not forwarded. The browser has no business
    // supplying this header, and accepting one would let a caller substitute
    // its own credential for ours. The key is set below, by us, always.
    if (name === 'x-api-key') continue

    headers[name] = value
  }

  headers.host = API_ORIGIN.host

  // THE ONE PLACE THE CREDENTIAL IS ATTACHED.
  //
  // It happens here, on the outbound request, inside Node. The browser
  // response has nothing to do with this object; there is no code path that
  // copies it back. If API_KEY is empty the header is simply absent and
  // FastAPI answers 401 - which src/api/client.js already renders as a
  // readable "CRM_API_KEY is missing or wrong" message.
  if (API_KEY) headers['x-api-key'] = API_KEY

  return headers
}

/**
 * Every failure reachable from `/api` answers JSON, never HTML.
 *
 * src/api/client.js reads `body.detail` - the FastAPI error shape - to turn a
 * failure into a sentence for the user. Matching that shape here means an
 * unreachable backend is reported as precisely as a rejected one, instead of
 * surfacing as a JSON parse error three layers away from the cause.
 */
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
  // `request.url` already begins with `/api/...`, and API_ORIGIN.pathname is
  // usually "/" - so this yields https://host/api/v1/clients?page=2 and
  // preserves a base path if CRM_API_URL happens to carry one.
  const base = API_ORIGIN.pathname.replace(/\/+$/, '')
  const target = new URL(base + request.url, API_ORIGIN)

  const upstream = transport.request(
    target,
    { method: request.method, headers: buildUpstreamHeaders(request) },
    (upstreamResponse) => {
      const headers = {}
      for (const [name, value] of Object.entries(upstreamResponse.headers)) {
        if (HOP_BY_HOP.has(name)) continue
        headers[name] = value
      }
      // API answers are per-request data, never cacheable by a shared cache.
      headers['cache-control'] = headers['cache-control'] || 'no-store'

      // The upstream status is passed through UNCHANGED. The FastAPI 404 stays
      // a 404, its 401 stays a 401. This is the line that makes
      // `/api/v1/nonexistent` a real API error.
      response.writeHead(upstreamResponse.statusCode, headers)
      upstreamResponse.pipe(response)
    },
  )

  upstream.setTimeout(UPSTREAM_TIMEOUT_MS, () => {
    upstream.destroy()
    sendApiError(response, 504, 'The CRM API did not respond in time.')
  })

  upstream.on('error', (error) => {
    // `error.code` - ECONNREFUSED, ENOTFOUND, ECONNRESET - describes the
    // connection, never the credential. Nothing derived from API_KEY is
    // logged or returned.
    console.error(`[orialis] upstream request failed: ${error.code || error.message}`)
    sendApiError(response, 502, 'The CRM API could not be reached.')
  })

  // If the browser disappears mid-flight, stop holding the upstream socket.
  response.on('close', () => {
    if (!upstream.destroyed && !response.writableFinished) upstream.destroy()
  })

  // Streamed rather than buffered: correct for any method, and the CRM API is
  // read-only so in practice this is an empty body.
  request.pipe(upstream)
}

// ---------------------------------------------------------------------------
// The request pipeline
// ---------------------------------------------------------------------------

const server = http.createServer((request, response) => {
  const started = Date.now()

  // Parsed against a dummy origin purely to split path from query safely -
  // the origin is discarded.
  let pathname
  try {
    pathname = decodeURI(new URL(request.url, 'http://localhost').pathname)
  } catch {
    return sendApiError(response, 400, 'Malformed request path.')
  }

  // Only the path is logged. Never headers, never the key, never a token that
  // could have travelled in a query string.
  response.on('finish', () => {
    console.log(`${request.method} ${pathname} ${response.statusCode} ${Date.now() - started}ms`)
  })

  // -- 1. The API proxy, FIRST and unconditional --------------------------
  //
  // Anything under /api is answered by the API or by a JSON error from this
  // function. It never falls through to the SPA below - that fall-through is
  // the bug this ordering exists to prevent.
  if (pathname === '/api' || pathname.startsWith('/api/')) {
    return proxyToApi(request, response)
  }

  // Everything past this point serves documents. A write method aimed at the
  // SPA is a mistake, not a route.
  if (request.method !== 'GET' && request.method !== 'HEAD') {
    response.writeHead(405, { Allow: 'GET, HEAD', 'Content-Length': 0 })
    return response.end()
  }

  // -- 2. A real file from dist/ ------------------------------------------
  const asset = ASSETS.get(pathname === '/' ? '/index.html' : pathname)
  if (asset) return sendAsset(request, response, pathname, asset)

  // -- 3. A MISSING file is a 404, not a route ----------------------------
  //
  // `/assets/index-OLDHASH.js` after a deploy is a stale asset request, not a
  // page. Falling it back to index.html would answer 200 with HTML, and the
  // browser would try to execute a document as JavaScript. The presence of an
  // extension in the last segment is what separates "asset" from "route" -
  // no route in this app has one.
  if (path.extname(pathname)) {
    response.writeHead(404, { 'Content-Type': 'text/plain; charset=utf-8' })
    return response.end('Not found\n')
  }

  // -- 4. The SPA fallback -------------------------------------------------
  //
  // /clients, /clients/CLT0001, /advisors, /branches, /interactions and any
  // future route are all the same document. The router in src/router.jsx
  // reads window.location and renders the right page - including its own
  // NotFound for a path it does not know, which is why an unrecognised route
  // is still 200: the SPA, not this server, decides it is unknown.
  return sendAsset(request, response, '/index.html', INDEX)
})

// ---------------------------------------------------------------------------
// Boot
// ---------------------------------------------------------------------------

server.listen(PORT, HOST, () => {
  console.log(`[orialis] CRM frontend listening on http://${HOST}:${PORT}`)
  console.log(`[orialis] proxying /api/* -> ${API_ORIGIN.origin}`)
  // The PRESENCE of the key is reported; its value never is. "set" and "NOT
  // SET" are the only two things this process will ever say about it.
  console.log(`[orialis] CRM_API_KEY is ${API_KEY ? 'set' : 'NOT SET - the API will answer 401'}`)
})

// ---------------------------------------------------------------------------
// Graceful shutdown
// ---------------------------------------------------------------------------

/**
 * Render sends SIGTERM before replacing or stopping a container, then SIGKILL
 * roughly 30 seconds later. Handling the first signal is what turns a deploy
 * into a handover rather than a cut: in-flight responses finish, and the next
 * instance takes over without a visible error.
 *
 * Two details make this actually work in a container:
 *
 *   - Node must BE pid 1, which the exec-form CMD in the Dockerfile
 *     guarantees. A shell wrapper would receive the signal and not forward it.
 *   - pid 1 ignores any signal it has no handler for. Registering these
 *     handlers is therefore not an optimisation: without them the container
 *     would ignore SIGTERM entirely and hang until SIGKILL.
 */
let shuttingDown = false

function shutdown(signal) {
  if (shuttingDown) return
  shuttingDown = true
  console.log(`[orialis] ${signal} received, shutting down`)

  // Stops accepting new connections; waits for in-flight requests to finish.
  server.close(() => {
    console.log('[orialis] closed cleanly')
    process.exit(0)
  })

  // Keep-alive sockets sitting idle between requests would otherwise hold
  // server.close() open for their full timeout. Idle ones are dropped
  // immediately; sockets mid-request are untouched.
  server.closeIdleConnections()

  // A backstop well inside the Render SIGKILL window, so the exit is ours and
  // logged rather than the platform and silent.
  setTimeout(() => {
    console.error('[orialis] forced exit after 10s')
    process.exit(1)
  }, 10_000).unref()
}

process.on('SIGTERM', () => shutdown('SIGTERM'))
process.on('SIGINT', () => shutdown('SIGINT'))

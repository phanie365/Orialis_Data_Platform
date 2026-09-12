/**
 * Vite configuration for the Orialis CRM frontend.
 *
 * This file is the security boundary of the whole frontend, so it is worth
 * reading slowly.
 *
 * It runs in **Node**, on the developer's machine, at config time. Nothing
 * in it is bundled and nothing in it is served. That is what makes it the
 * only place where the CRM API key may appear.
 *
 * Two problems are solved here at once:
 *
 *  1. THE API KEY MUST NOT REACH THE BROWSER.
 *     Any variable named `VITE_*` is substituted into the bundle literally
 *     at build time - `import.meta.env.VITE_X` becomes its value in a .js
 *     file anyone can read. A key shipped that way is a published key, and
 *     keeping the .env out of Git changes nothing about it.
 *
 *     So `CRM_API_KEY` is deliberately NOT prefixed. Vite refuses to expose
 *     unprefixed variables to client code, which means the protection is
 *     enforced by the tool rather than by our own discipline. The key is
 *     read here, attached to the request by the dev server, and never
 *     travels to the browser in either direction.
 *
 *  2. THE API SENDS NO CORS HEADERS.
 *     CRM/app/main.py declares no CORSMiddleware, so a browser on :5173
 *     calling :8000 directly would have its response blocked. Proxying
 *     means the browser only ever calls its own origin - `/api/...` on
 *     :5173 - and Node does the cross-origin hop server-side, where CORS
 *     does not apply. No backend change was needed.
 *
 * The production proxy is a separate decision, left for the deployment
 * step. Because the React code only knows the relative path `/api`, that
 * decision will change this layer and nothing else.
 */

import { defineConfig, loadEnv } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig(({ mode }) => {
  // The third argument is the prefix filter. Passing '' disables it, so
  // unprefixed variables are readable HERE, in Node. It does not expose
  // them to the client: only `VITE_*` ever reaches `import.meta.env`.
  const env = loadEnv(mode, process.cwd(), '')

  const apiUrl = env.CRM_API_URL || 'http://127.0.0.1:8000'
  const apiKey = env.CRM_API_KEY || ''

  if (!apiKey) {
    // A warning rather than a crash: the shell and the design system are
    // worth looking at even without a backend, and the dashboard reports
    // the 401 itself. Silence would be the wrong choice - the failure would
    // surface later as an unexplained empty page.
    console.warn(
      '\n  [orialis] CRM_API_KEY is not set.\n' +
      '  Requests will be proxied without an API key and the CRM API will\n' +
      '  answer 401. Copy frontend/.env.example to frontend/.env and fill it in.\n'
    )
  }

  const proxy = {
    '/api': {
      target: apiUrl,
      changeOrigin: true,
      configure: (instance) => {
        instance.on('proxyReq', (proxyRequest) => {
          // The single place the credential is attached. It is added to the
          // request as it leaves Node for FastAPI - after the browser has
          // already been served, so it is never part of anything the
          // browser can observe.
          if (apiKey) {
            proxyRequest.setHeader('X-API-Key', apiKey)
          }
        })
      },
    },
  }

  return {
    plugins: [react()],
    // `vite dev`
    server: { port: 5173, proxy },
    // `vite preview`, which serves the real production build. Sharing the
    // same proxy makes the built bundle testable locally - without it the
    // only way to exercise a production build would be to deploy it.
    //
    // This is still a DEVELOPMENT convenience: `vite preview` is not a
    // production server, and the production proxy remains an open decision
    // for the deployment step.
    preview: { port: 4173, proxy },
  }
})

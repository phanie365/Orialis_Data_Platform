/**
 * The security boundary of the ERP frontend. Runs in Node only (imported by
 * vite.config.js), never bundled, never served.
 *
 * The browser calls the RELATIVE path `/api/v1/...` on its own origin. The
 * Vite dev server forwards the call to the ERP API and attaches ERP_API_KEY
 * on the way out. The key therefore never travels to the browser, in either
 * direction - and because the browser only ever makes same-origin requests,
 * the API needs no CORS headers either.
 */

export const DEFAULT_API_URL = 'http://127.0.0.1:8001'

export function resolveApiConfig(env) {
  return {
    apiUrl: (env.ERP_API_URL || DEFAULT_API_URL).trim().replace(/\/+$/, ''),
    apiKey: (env.ERP_API_KEY || '').trim(),
  }
}

/**
 * Refuse to start when a secret would end up in the bundle.
 *
 * Vite inlines every `VITE_*` variable into the client code at build time.
 * A variable named like a credential, or carrying the API key's value, would
 * be published - so this fails loudly instead.
 */
export function assertNoPublicSecrets(env) {
  const apiKey = (env.ERP_API_KEY || '').trim()
  const exposed = Object.entries(env)
    .filter(([name]) => name.startsWith('VITE_'))
    .filter(([name, value]) =>
      /KEY|SECRET|TOKEN|PASSWORD/i.test(name) || (apiKey && String(value).trim() === apiKey))
    .map(([name]) => name)

  if (exposed.length > 0) {
    throw new Error(
      `[orialis-erp] Refusing to start: ${exposed.join(', ')} would be inlined into the ` +
        'browser bundle. Put the key in ERP_API_KEY, without the VITE_ prefix.',
    )
  }
}

/** The `server.proxy` / `preview.proxy` block for Vite. */
export function createApiProxy({ apiUrl, apiKey }) {
  return {
    '/api': {
      target: apiUrl,
      changeOrigin: true,
      configure(proxy) {
        proxy.on('proxyReq', (proxyRequest) => {
          // A key sent by the browser is dropped, never forwarded: the only
          // credential that reaches the API is the one configured here.
          proxyRequest.removeHeader('x-api-key')
          if (apiKey) proxyRequest.setHeader('X-API-Key', apiKey)
        })
      },
    },
  }
}

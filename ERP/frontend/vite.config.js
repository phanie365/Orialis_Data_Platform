/**
 * Vite configuration for the Orialis ERP frontend.
 *
 * Runs in Node. ERP_API_URL and ERP_API_KEY are read here (from
 * ERP/frontend/.env or the process environment) and handed to the proxy in
 * apiProxy.js. Nothing under src/ can see them: only VITE_* variables reach
 * client code, and none is used.
 *
 * Ports: 5174 for `vite dev`, 4174 for `vite preview` - so the ERP frontend
 * runs beside the CRM frontend (5173 / 4173) without a clash.
 */

import { fileURLToPath } from 'node:url'

import react from '@vitejs/plugin-react'
import { defineConfig, loadEnv } from 'vite'

import { assertNoPublicSecrets, createApiProxy, resolveApiConfig } from './apiProxy.js'

const HERE = fileURLToPath(new URL('.', import.meta.url))

export default defineConfig(({ command, mode }) => {
  // Prefix '' makes unprefixed variables readable HERE, in Node. It does not
  // expose them to the client.
  const env = loadEnv(mode, HERE, '')
  assertNoPublicSecrets(env)

  const api = resolveApiConfig(env)
  // Only a running server proxies requests; a build never needs the key.
  if (command === 'serve' && !api.apiKey) {
    console.warn(
      '\n  [orialis-erp] ERP_API_KEY is not set: every API call will answer 401.\n' +
        '  Copy ERP/frontend/.env.example to ERP/frontend/.env and fill it in.\n',
    )
  }

  const proxy = createApiProxy(api)

  return {
    root: HERE,
    envDir: HERE,
    plugins: [react()],
    server: { host: '127.0.0.1', port: 5174, strictPort: true, proxy },
    preview: { host: '127.0.0.1', port: 4174, strictPort: true, proxy },
  }
})

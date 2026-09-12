/**
 * Application entry point.
 *
 * StrictMode is on: in development it mounts every component twice, which is
 * precisely how a fetch effect that forgets to clean up gets caught. The
 * useApi hook aborts on unmount, so the double mount is harmless here - and
 * that is a property worth keeping verified rather than assumed.
 */

import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'

import { App } from './App'
import { RouterProvider } from './router'
import './styles/global.css'

createRoot(document.getElementById('root')).render(
  <StrictMode>
    <RouterProvider>
      <App />
    </RouterProvider>
  </StrictMode>,
)

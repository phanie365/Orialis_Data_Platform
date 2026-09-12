/**
 * Route table.
 *
 * Six routes. `/clients/:clientId` is declared after `/clients` so the
 * static path wins - the matcher takes the first match, and a dynamic
 * segment would otherwise swallow nothing here anyway, but the order makes
 * the intent explicit rather than accidental.
 */

import { Advisors } from './pages/Advisors'
import { Branches } from './pages/Branches'
import { ClientDetail } from './pages/ClientDetail'
import { Clients } from './pages/Clients'
import { Dashboard } from './pages/Dashboard'
import { Interactions } from './pages/Interactions'
import { NotFound } from './pages/NotFound'
import { useRoutes } from './router'

const ROUTES = [
  { path: '/', element: () => <Dashboard /> },
  { path: '/clients', element: () => <Clients /> },
  { path: '/clients/:clientId', element: ({ clientId }) => <ClientDetail clientId={clientId} /> },
  { path: '/advisors', element: () => <Advisors /> },
  { path: '/interactions', element: () => <Interactions /> },
  { path: '/branches', element: () => <Branches /> },
]

export function App() {
  return useRoutes(ROUTES, <NotFound />)
}

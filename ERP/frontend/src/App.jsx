import { useMemo } from 'react'

import { loadReferenceData } from './api.js'
import { ErrorBox, Loading } from './components.jsx'
import { ReferenceContext, useFetch, useHashRoute } from './hooks.js'
import { Dashboard } from './pages/Dashboard.jsx'
import { Invoices } from './pages/Invoices.jsx'
import { Payments } from './pages/Payments.jsx'
import { Suppliers } from './pages/Suppliers.jsx'

const SCREENS = {
  dashboard: { label: 'Dashboard', Page: Dashboard },
  invoices: { label: 'Invoices', Page: Invoices },
  payments: { label: 'Payments', Page: Payments },
  suppliers: { label: 'Suppliers', Page: Suppliers },
}

export function App() {
  const { screen, params, navigate } = useHashRoute()
  const reference = useFetch((signal) => loadReferenceData({ signal }), [])

  const context = useMemo(
    () => (reference.data ? { ...reference.data, reload: reference.reload } : null),
    [reference.data, reference.reload],
  )

  const current = screen ? SCREENS[screen] : null

  return (
    <>
      <header className="topbar">
        <strong>Orialis ERP</strong>
        <span className="muted">Finance &amp; Operations · internal, read-only</span>
        <nav>
          {Object.entries(SCREENS).map(([key, { label }]) => (
            <a key={key} href={`#/${key}`} className={key === screen ? 'active' : undefined}>
              {label}
            </a>
          ))}
        </nav>
      </header>

      <main>
        {!current ? (
          <div className="state">
            Unknown screen. <a href="#/dashboard">Back to the dashboard</a>
          </div>
        ) : reference.error ? (
          <ErrorBox error={reference.error} onRetry={reference.reload} />
        ) : !context ? (
          <Loading>Loading reference data…</Loading>
        ) : (
          <ReferenceContext.Provider value={context}>
            <current.Page params={params} navigate={navigate} />
          </ReferenceContext.Provider>
        )}
      </main>
    </>
  )
}

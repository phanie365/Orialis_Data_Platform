/**
 * The application shell: navigation rail, header, content well.
 *
 * Every CRM section is now reachable. The current one is marked with the
 * champagne rule already used for the dashboard - the same visual language,
 * extended rather than replaced.
 *
 * A section counts as current when the pathname is that section or lives
 * under it, so a client detail page keeps "Clients" lit in the rail instead
 * of leaving the user with no indication of where they are.
 */

import { Link, useRouter } from '../router'
import { Wordmark } from './Wordmark'
import './Shell.css'

const NAVIGATION = [
  { to: '/', label: 'Overview' },
  { to: '/clients', label: 'Clients' },
  { to: '/advisors', label: 'Advisors' },
  { to: '/interactions', label: 'Interactions' },
  { to: '/branches', label: 'Branches' },
]

function isCurrent(pathname, to) {
  if (to === '/') return pathname === '/'
  return pathname === to || pathname.startsWith(`${to}/`)
}

function formatToday() {
  return new Date().toLocaleDateString('en-GB', {
    weekday: 'long',
    day: 'numeric',
    month: 'long',
    year: 'numeric',
  })
}

export function Shell({ title, subtitle, breadcrumb, actions, children }) {
  const { pathname } = useRouter()

  return (
    <div className="shell">
      <nav className="rail" aria-label="Main">
        <div className="rail__brand">
          <Link to="/" className="rail__brandLink">
            <Wordmark tone="light" />
          </Link>
        </div>

        <div className="rail__section label">Management</div>

        <ul className="rail__nav">
          {NAVIGATION.map((item) => {
            const current = isCurrent(pathname, item.to)
            return (
              <li key={item.to}>
                <Link
                  to={item.to}
                  className={'rail__item' + (current ? ' rail__item--current' : '')}
                  aria-current={current ? 'page' : undefined}
                >
                  {item.label}
                </Link>
              </li>
            )
          })}
        </ul>

        {/* The three verbs from the brand line, set as they are on the
            moodboard: small capitals separated by thin rules. */}
        <div className="rail__footer">
          <div className="rail__motto">
            <span>Conseiller</span>
            <span>Planifier</span>
            <span>Transmettre</span>
          </div>
        </div>
      </nav>

      <div className="main">
        <header className="topbar">
          <div className="topbar__heading">
            {breadcrumb ? (
              <div className="topbar__crumb">{breadcrumb}</div>
            ) : (
              <div className="topbar__eyebrow label">Client Relationship Management</div>
            )}
            <h1 className="topbar__title">{title}</h1>
            {subtitle && <p className="topbar__subtitle">{subtitle}</p>}
          </div>

          <div className="topbar__aside">
            {actions ?? (
              <div className="topbar__date">
                <span className="label">Today</span>
                <div className="topbar__dateValue">{formatToday()}</div>
              </div>
            )}
          </div>
        </header>

        <main className="content">{children}</main>
      </div>
    </div>
  )
}

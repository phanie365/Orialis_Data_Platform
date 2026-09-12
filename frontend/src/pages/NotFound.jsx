/**
 * An unknown URL.
 *
 * Kept inside the shell rather than shown as a bare message: the navigation
 * stays available, so a mistyped address is one click from being fixed.
 */

import { Shell } from '../components/Shell'
import { Link } from '../router'

export function NotFound() {
  return (
    <Shell title="Page not found">
      <div className="panel">
        <div className="state">
          This address does not match any section of the CRM.
          <div className="state__detail">
            <Link to="/">Return to the overview</Link>
          </div>
        </div>
      </div>
    </Shell>
  )
}

/**
 * The branch network.
 *
 * Seven records, returned whole by an endpoint that does not paginate. So no
 * pager and no filter bar: controls for seven rows would be furniture.
 *
 * Presented as cards rather than as a table. A table is the right shape for
 * five thousand clients you scan and compare; seven offices are closer to a
 * page in a company brochure, and the moodboard's own network page is set
 * that way - a deep green header, letterspaced capitals, thin rules between
 * the facts.
 *
 * Each card carries the advisors attached to that branch, counted from the
 * advisor list - 100 rows in one request, not a call per branch.
 */

import { useMemo } from 'react'

import { fetchAllAdvisors, fetchBranches } from '../api/client'
import { useApi } from '../api/useApi'
import { Shell } from '../components/Shell'
import { Link } from '../router'
import './Branches.css'

export function Branches() {
  const branches = useApi(fetchBranches)
  const advisors = useApi(fetchAllAdvisors)

  // branch_id -> number of advisors. One pass over the 100 advisors.
  const advisorCounts = useMemo(() => {
    const counts = new Map()
    for (const advisor of advisors.data?.data ?? []) {
      counts.set(advisor.branch_id, (counts.get(advisor.branch_id) ?? 0) + 1)
    }
    return counts
  }, [advisors.data])

  const rows = branches.data?.data ?? []

  return (
    <Shell
      title="Branches"
      subtitle="The seven Orialis offices across France, Belgium, Switzerland and Italy."
    >
      {branches.loading && (
        <div className="branches">
          {Array.from({ length: 7 }, (_, index) => (
            <div className="branch branch--loading" key={index}>
              <span className="skeleton branch__skeletonHead" />
              <span className="skeleton branch__skeletonLine" />
              <span className="skeleton branch__skeletonLine" />
            </div>
          ))}
        </div>
      )}

      {!branches.loading && branches.error && (
        <div className="panel">
          <div className="state state--error">
            {branches.error.message}
            {branches.error.status === 401 && (
              <div className="state__detail">
                Set CRM_API_KEY in frontend/.env, then restart the dev server.
              </div>
            )}
          </div>
        </div>
      )}

      {!branches.loading && !branches.error && rows.length === 0 && (
        <div className="panel">
          <div className="state">No branch is recorded.</div>
        </div>
      )}

      {!branches.loading && !branches.error && rows.length > 0 && (
        <div className="branches">
          {rows.map((branch) => {
            const count = advisorCounts.get(branch.branch_id)
            return (
              <article className="branch" key={branch.branch_id}>
                {/* The forest header, as on the moodboard's signage. */}
                <header className="branch__head">
                  <span className="branch__id">{branch.branch_id}</span>
                  <h2 className="branch__name">{branch.branch_name}</h2>
                  <span className="branch__city">
                    {branch.city} · {branch.country}
                  </span>
                </header>

                <dl className="branch__facts">
                  <div className="branch__fact">
                    <dt className="label">Region</dt>
                    <dd>{branch.region || '—'}</dd>
                  </div>
                  <div className="branch__fact">
                    <dt className="label">Time zone</dt>
                    {/* The IANA zone as stored. An earlier version showed
                        only its last segment as a city, which read as
                        "Zurich" on the GENEVA card - correct as a zone
                        name, but it contradicted the city printed above it.
                        A derived value that misstates the record is worse
                        than the slightly technical one. */}
                    <dd className="branch__tz">{branch.timezone || '—'}</dd>
                  </div>
                  <div className="branch__fact">
                    <dt className="label">Status</dt>
                    <dd>
                      <span
                        className={`status status--${(branch.branch_status || '').toLowerCase()}`}
                      >
                        {branch.branch_status}
                      </span>
                    </dd>
                  </div>
                </dl>

                <footer className="branch__foot">
                  {/* Only rendered once the advisor list has arrived: a
                      count shown as 0 while loading would be a wrong fact,
                      not a pending one. */}
                  {count !== undefined ? (
                    <Link className="branch__link" to={`/advisors?branch_id=${branch.branch_id}`}>
                      <span className="branch__count tnum">{count}</span>
                      <span className="branch__countUnit">
                        {count === 1 ? 'advisor' : 'advisors'}
                      </span>
                    </Link>
                  ) : (
                    <span className="skeleton branch__skeletonCount" />
                  )}
                </footer>
              </article>
            )
          })}
        </div>
      )}
    </Shell>
  )
}

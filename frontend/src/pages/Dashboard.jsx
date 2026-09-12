/**
 * Dashboard V1.
 *
 * Two API calls, no more:
 *
 *   GET /api/v1/stats/overview          -> the four KPIs, the three client
 *                                          breakdowns, the advisor ranking
 *   GET /api/v1/interactions?page_size=8 -> the activity feed
 *
 * They are issued independently and render independently, so a slow or
 * failing activity feed does not hold the aggregates hostage, and vice
 * versa. That separation is the whole reason the two endpoints are two
 * endpoints.
 *
 * Every figure on this page comes from the API. Nothing is computed from a
 * hardcoded list of segments, countries or risk profiles: those labels
 * arrive inside the payload, because the CRM's reference data belongs in the
 * database.
 */

import { fetchOverview, fetchRecentInteractions } from '../api/client'
import { useApi } from '../api/useApi'
import { BreakdownBars } from '../components/BreakdownBars'
import { KpiTile } from '../components/KpiTile'
import { RecentInteractions } from '../components/RecentInteractions'
import { TopAdvisors } from '../components/TopAdvisors'
import { Shell } from '../components/Shell'
import './Dashboard.css'

/** Loading / error / empty, so no panel has to repeat the three branches. */
function Panel({ title, note, loading, error, isEmpty, children }) {
  return (
    <section className="panel">
      <div className="panel__head">
        <h2 className="panel__title">{title}</h2>
        {note && <span className="panel__note">{note}</span>}
      </div>
      <div className="panel__body">
        {loading && <div className="state">Loading…</div>}
        {!loading && error && (
          <div className="state state--error">
            {error.message}
            {error.status === 401 && (
              <div className="state__detail">
                The dev server proxies requests with the key from frontend/.env.
              </div>
            )}
          </div>
        )}
        {!loading && !error && isEmpty && (
          <div className="state">No data to display.</div>
        )}
        {!loading && !error && !isEmpty && children}
      </div>
    </section>
  )
}

export function Dashboard() {
  const overview = useApi(fetchOverview)
  const recent = useApi(fetchRecentInteractions)

  const totals = overview.data?.totals
  const interactions = recent.data?.data ?? []

  // Active clients as a share of the book. Derived from two figures the API
  // returned, not from a third the API does not have.
  const activeShare =
    totals && totals.total_clients
      ? ((totals.active_clients / totals.total_clients) * 100).toFixed(1)
      : null

  return (
    <Shell
      title="Dashboard"
      subtitle="Position of the client book across the Orialis network."
    >
      {/* -- Headline figures ------------------------------------------- */}
      {overview.error ? (
        <div className="panel dash__alert">
          <div className="state state--error">
            {overview.error.message}
            {overview.error.status === 401 && (
              <div className="state__detail">
                Set CRM_API_KEY in frontend/.env, then restart the dev server.
              </div>
            )}
          </div>
        </div>
      ) : (
        <div className="dash__kpis">
          <KpiTile
            label="Total Clients"
            value={totals?.total_clients ?? 0}
            context="Across all branches"
            loading={overview.loading}
          />
          <KpiTile
            label="Active Clients"
            value={totals?.active_clients ?? 0}
            context={activeShare ? `${activeShare}% of the book` : ' '}
            loading={overview.loading}
          />
          <KpiTile
            label="Advisors"
            value={totals?.total_advisors ?? 0}
            context="Across seven branches"
            loading={overview.loading}
          />
          <KpiTile
            label="Interactions"
            value={totals?.total_interactions ?? 0}
            context="Recorded to date"
            loading={overview.loading}
          />
        </div>
      )}

      {/* -- The editorial line from the brand ---------------------------- */}
      <div className="dash__quote">
        <span className="dash__quoteRule" />
        <p className="dash__quoteText">
          L’humain au cœur de la <em>performance durable.</em>
        </p>
      </div>

      {/* -- Client breakdowns ------------------------------------------- */}
      <div className="dash__row dash__row--three">
        <Panel
          title="Clients by Segment"
          note="Share of total"
          loading={overview.loading}
          error={overview.error}
          isEmpty={!overview.data?.clients_by_segment?.length}
        >
          <BreakdownBars
            rows={overview.data?.clients_by_segment ?? []}
            labelKey="segment"
            total={totals?.total_clients}
          />
        </Panel>

        <Panel
          title="Clients by Country"
          note="Country of residence"
          loading={overview.loading}
          error={overview.error}
          isEmpty={!overview.data?.clients_by_country?.length}
        >
          <BreakdownBars
            rows={overview.data?.clients_by_country ?? []}
            labelKey="country"
            total={totals?.total_clients}
          />
        </Panel>

        <Panel
          title="Client Risk Overview"
          note="Risk profile"
          loading={overview.loading}
          error={overview.error}
          isEmpty={!overview.data?.clients_by_risk_profile?.length}
        >
          <BreakdownBars
            rows={overview.data?.clients_by_risk_profile ?? []}
            labelKey="risk_profile"
            total={totals?.total_clients}
          />
        </Panel>
      </div>

      {/* -- Ranking and activity ---------------------------------------- */}
      <div className="dash__row dash__row--split">
        <Panel
          title="Top Advisors"
          note="By assigned clients"
          loading={overview.loading}
          error={overview.error}
          isEmpty={!overview.data?.top_advisors?.length}
        >
          <TopAdvisors advisors={overview.data?.top_advisors ?? []} />
        </Panel>

        <Panel
          title="Recent Interactions"
          note="Latest eight"
          loading={recent.loading}
          error={recent.error}
          isEmpty={interactions.length === 0}
        >
          <RecentInteractions interactions={interactions} />
        </Panel>
      </div>
    </Shell>
  )
}

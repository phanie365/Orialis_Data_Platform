/**
 * The interaction log.
 *
 * 30,130 records, so nothing but the current page is ever fetched. The
 * endpoint sorts by `interaction_date DESC, interaction_id DESC`; the id
 * tiebreaker is what keeps paging stable when several interactions share a
 * timestamp - without it a row could appear on two pages, or on none.
 *
 * A note on the two identifier filters. `advisor_id` is a picker, because
 * fetching all 100 advisors is one small request. `client_id` is a text field,
 * because the equivalent for clients would mean downloading 5,000 rows to
 * populate a dropdown - the exact thing this architecture avoids. A clean
 * identifier input is the honest answer; the usable path to a client's
 * history is the client detail page, which shows it in context.
 */

import { useMemo } from 'react'

import { fetchAllAdvisors, fetchFilterValues, fetchInteractions } from '../api/client'
import { useApi } from '../api/useApi'
import { useListPage } from '../api/useListPage'
import { DataTable } from '../components/DataTable'
import { FilterBar } from '../components/FilterBar'
import { Pagination } from '../components/Pagination'
import { Shell } from '../components/Shell'
import { formatTimestamp, outcomeTone } from '../format'
import { Link, useSearchParams } from '../router'
import './List.css'

const EMPTY_FILTERS = {
  client_id: '',
  advisor_id: '',
  interaction_type: '',
  channel: '',
  date_from: '',
  date_to: '',
}

export function Interactions() {
  const searchParams = useSearchParams()

  const initialFilters = useMemo(() => {
    const seeded = { ...EMPTY_FILTERS }
    for (const name of Object.keys(EMPTY_FILTERS)) {
      const value = searchParams.get(name)
      if (value) seeded[name] = value
    }
    return seeded
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const list = useListPage(initialFilters)

  const interactions = useApi(
    (options) => fetchInteractions(list.query, options),
    [list.queryKey],
  )
  const filterValues = useApi(fetchFilterValues)
  const advisors = useApi(fetchAllAdvisors)

  const advisorNames = useMemo(() => {
    const map = new Map()
    for (const advisor of advisors.data?.data ?? []) {
      map.set(advisor.advisor_id, `${advisor.first_name} ${advisor.last_name}`)
    }
    return map
  }, [advisors.data])

  const advisorOptions = useMemo(
    () => (advisors.data?.data ?? []).map((advisor) => advisor.advisor_id),
    [advisors.data],
  )

  const filters = [
    { name: 'interaction_type', label: 'Type', type: 'select', options: filterValues.data?.interaction_type },
    { name: 'channel', label: 'Channel', type: 'select', options: filterValues.data?.interaction_channel },
    { name: 'advisor_id', label: 'Advisor', type: 'select', options: advisorOptions },
    { name: 'client_id', label: 'Client id', type: 'text', placeholder: 'e.g. CLT0001' },
    { name: 'date_from', label: 'From', type: 'date' },
    { name: 'date_to', label: 'To', type: 'date' },
  ]

  const columns = [
    {
      key: 'date',
      header: 'When',
      render: (interaction) => (
        <span className="cell-muted tnum">
          {formatTimestamp(interaction.interaction_date)}
        </span>
      ),
    },
    {
      key: 'client',
      header: 'Client',
      render: (interaction) => (
        <Link className="cellLink" to={`/clients/${interaction.client_id}`}>
          {interaction.client_id}
        </Link>
      ),
    },
    {
      key: 'advisor',
      header: 'Advisor',
      render: (interaction) => (
        <>
          <span className="cell-muted">
            {advisorNames.get(interaction.advisor_id) ?? interaction.advisor_id}
          </span>
          {advisorNames.has(interaction.advisor_id) && (
            <span className="cell-sub">{interaction.advisor_id}</span>
          )}
        </>
      ),
    },
    {
      key: 'subject',
      header: 'Subject',
      render: (interaction) => (
        <>
          <span className="cell-name">{interaction.subject}</span>
          <span className="cell-sub">{interaction.interaction_type}</span>
        </>
      ),
    },
    {
      key: 'channel',
      header: 'Channel',
      render: (interaction) => <span className="cell-muted">{interaction.channel}</span>,
    },
    {
      key: 'outcome',
      header: 'Outcome',
      render: (interaction) => (
        <span className="cell-muted">
          <span className={`dot dot--${outcomeTone(interaction.outcome)}`} />
          {interaction.outcome}
        </span>
      ),
    },
  ]

  return (
    <Shell
      title="Interactions"
      subtitle="Every recorded exchange between an advisor and a client, most recent first."
    >
      <div className="list">
        <FilterBar
          filters={filters}
          values={list.filters}
          onChange={list.setFilter}
          onReset={list.resetFilters}
          resultCount={interactions.data?.pagination?.total_records ?? null}
          loading={interactions.loading}
        />

        <DataTable
          columns={columns}
          rows={interactions.data?.data ?? []}
          rowKey={(interaction) => interaction.interaction_id}
          loading={interactions.loading}
          error={interactions.error}
          emptyMessage="No interaction matches these filters."
        />

        <Pagination
          pagination={interactions.data?.pagination}
          onPageChange={list.setPage}
          onPageSizeChange={list.changePageSize}
        />
      </div>
    </Shell>
  )
}

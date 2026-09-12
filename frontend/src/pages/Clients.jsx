/**
 * The client list.
 *
 * Filtering and paging are done entirely by the API. The browser never holds
 * more than one page of clients: with 5,000 records at 25 per page that is
 * 25 rows, not 5,000, and the `total_records` in the response comes from a
 * server-side COUNT over the same filters.
 *
 * Three requests on mount, and only the first repeats as you filter or page:
 *
 *   /clients?…            the page itself, refetched on every change
 *   /stats/filters        the pickers' options, fetched once
 *   /advisors?page_size=200  100 rows, to turn advisor_id into a name
 *
 * That last one is the single bounded exception to "never load a whole
 * table". A hundred advisors is about 20 kB and it is what lets the list show
 * "Sophie Vidal" instead of "ADV012" - and lets the advisor filter be a
 * picker. The same approach applied to clients would be indefensible.
 */

import { useMemo } from 'react'

import { fetchAllAdvisors, fetchClients, fetchFilterValues } from '../api/client'
import { useApi } from '../api/useApi'
import { useListPage } from '../api/useListPage'
import { DataTable } from '../components/DataTable'
import { FilterBar } from '../components/FilterBar'
import { Pagination } from '../components/Pagination'
import { Shell } from '../components/Shell'
import { formatDate } from '../format'
import { useRouter, useSearchParams } from '../router'
import './List.css'

const EMPTY_FILTERS = {
  country: '',
  segment: '',
  risk_profile: '',
  client_status: '',
  advisor_id: '',
}

export function Clients() {
  const { navigate } = useRouter()
  const searchParams = useSearchParams()

  // The URL seeds the filters, which is what makes /clients?advisor_id=ADV012
  // a working link - the advisor list points here to answer "who are this
  // advisor's clients". Only keys the page actually filters on are read, so a
  // stray query parameter cannot inject an unsupported filter into the API
  // request.
  const initialFilters = useMemo(() => {
    const seeded = { ...EMPTY_FILTERS }
    for (const name of Object.keys(EMPTY_FILTERS)) {
      const value = searchParams.get(name)
      if (value) seeded[name] = value
    }
    return seeded
    // Read once, when the page mounts: after that the filters are the user's.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const list = useListPage(initialFilters)

  const clients = useApi(
    (options) => fetchClients(list.query, options),
    [list.queryKey],
  )
  const filterValues = useApi(fetchFilterValues)
  const advisors = useApi(fetchAllAdvisors)

  // advisor_id -> "First Last", so the table can show a person rather than a
  // key. Built once per advisor payload rather than on every render.
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
    { name: 'country', label: 'Country', type: 'select', options: filterValues.data?.client_country },
    { name: 'segment', label: 'Segment', type: 'select', options: filterValues.data?.client_segment },
    { name: 'risk_profile', label: 'Risk profile', type: 'select', options: filterValues.data?.client_risk_profile },
    { name: 'client_status', label: 'Status', type: 'select', options: filterValues.data?.client_status },
    { name: 'advisor_id', label: 'Advisor', type: 'select', options: advisorOptions },
  ]

  const columns = [
    {
      key: 'name',
      header: 'Client',
      render: (client) => (
        <>
          <span className="cell-name">
            {client.first_name} {client.last_name}
          </span>
          <span className="cell-sub">{client.client_id}</span>
        </>
      ),
    },
    {
      key: 'residence',
      header: 'Residence',
      render: (client) => (
        <>
          <span className="cell-muted">{client.city_of_residence}</span>
          <span className="cell-sub">{client.country_of_residence}</span>
        </>
      ),
    },
    {
      key: 'segment',
      header: 'Segment',
      render: (client) => <span className="cell-tag">{client.client_segment}</span>,
    },
    {
      key: 'risk',
      header: 'Risk profile',
      render: (client) => <span className="cell-muted">{client.risk_profile}</span>,
    },
    {
      key: 'advisor',
      header: 'Advisor',
      render: (client) => (
        <>
          <span className="cell-muted">
            {advisorNames.get(client.advisor_id) ?? client.advisor_id}
          </span>
          {advisorNames.has(client.advisor_id) && (
            <span className="cell-sub">{client.advisor_id}</span>
          )}
        </>
      ),
    },
    {
      key: 'created',
      header: 'Client since',
      render: (client) => (
        <span className="cell-muted tnum">{formatDate(client.created_at)}</span>
      ),
    },
    {
      key: 'status',
      header: 'Status',
      render: (client) => (
        <span className={`status status--${(client.client_status || '').toLowerCase()}`}>
          {client.client_status}
        </span>
      ),
    },
  ]

  return (
    <Shell
      title="Clients"
      subtitle="Every client held across the Orialis network, filtered and paged by the API."
    >
      <div className="list">
        <FilterBar
          filters={filters}
          values={list.filters}
          onChange={list.setFilter}
          onReset={list.resetFilters}
          resultCount={clients.data?.pagination?.total_records ?? null}
          loading={clients.loading}
        />

        <DataTable
          columns={columns}
          rows={clients.data?.data ?? []}
          rowKey={(client) => client.client_id}
          loading={clients.loading}
          error={clients.error}
          emptyMessage="No client matches these filters."
          onRowClick={(client) => navigate(`/clients/${client.client_id}`)}
        />

        <Pagination
          pagination={clients.data?.pagination}
          onPageChange={list.setPage}
          onPageSizeChange={list.changePageSize}
        />
      </div>
    </Shell>
  )
}

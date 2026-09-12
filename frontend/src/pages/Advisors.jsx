/**
 * The advisor list.
 *
 * Same shape as the client list, and for the same reason: the API filters and
 * pages, the browser displays. There are only 100 advisors, so paging is not
 * strictly forced here - but using the server's pagination keeps one
 * behaviour across the CRM rather than two, and the page stays correct if the
 * network ever grows.
 *
 * No detail page. The list already shows every column `/advisors/{id}`
 * returns, so a detail route would be the same six fields on a page of their
 * own. The one thing that would justify it - an advisor's client book - is
 * reachable already: each row links to the client list filtered by that
 * advisor, which is a real query against real data rather than an empty
 * shell built to have a detail page.
 */

import { useMemo } from 'react'

import { fetchAdvisors, fetchBranches, fetchFilterValues } from '../api/client'
import { useApi } from '../api/useApi'
import { useListPage } from '../api/useListPage'
import { DataTable } from '../components/DataTable'
import { FilterBar } from '../components/FilterBar'
import { Pagination } from '../components/Pagination'
import { Shell } from '../components/Shell'
import { useRouter, useSearchParams } from '../router'
import './List.css'

const EMPTY_FILTERS = {
  branch_id: '',
  specialization: '',
  spoken_language: '',
  advisor_status: '',
}

export function Advisors() {
  const { navigate } = useRouter()
  const searchParams = useSearchParams()

  // Seeded from the URL so /advisors?branch_id=BR003 works - the branch cards
  // link here to answer "who works at this office".
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

  const advisors = useApi(
    (options) => fetchAdvisors(list.query, options),
    [list.queryKey],
  )
  const filterValues = useApi(fetchFilterValues)
  const branches = useApi(fetchBranches)

  // branch_id -> "Orialis Geneva". Seven rows, one call.
  const branchNames = useMemo(() => {
    const map = new Map()
    for (const branch of branches.data?.data ?? []) {
      map.set(branch.branch_id, branch)
    }
    return map
  }, [branches.data])

  const branchOptions = useMemo(
    () => (branches.data?.data ?? []).map((branch) => branch.branch_id),
    [branches.data],
  )

  const filters = [
    { name: 'branch_id', label: 'Branch', type: 'select', options: branchOptions },
    { name: 'specialization', label: 'Specialization', type: 'select', options: filterValues.data?.advisor_specialization },
    { name: 'spoken_language', label: 'Language', type: 'select', options: filterValues.data?.advisor_spoken_language },
    { name: 'advisor_status', label: 'Status', type: 'select', options: filterValues.data?.advisor_status },
  ]

  const columns = [
    {
      key: 'name',
      header: 'Advisor',
      render: (advisor) => (
        <>
          <span className="cell-name">
            {advisor.first_name} {advisor.last_name}
          </span>
          <span className="cell-sub">{advisor.advisor_id}</span>
        </>
      ),
    },
    {
      key: 'branch',
      header: 'Branch',
      render: (advisor) => {
        const branch = branchNames.get(advisor.branch_id)
        return (
          <>
            <span className="cell-muted">{branch?.branch_name ?? advisor.branch_id}</span>
            {branch && <span className="cell-sub">{branch.city}</span>}
          </>
        )
      },
    },
    {
      key: 'job_title',
      header: 'Job title',
      render: (advisor) => <span className="cell-muted">{advisor.job_title}</span>,
    },
    {
      key: 'specialization',
      header: 'Specialization',
      render: (advisor) => <span className="cell-tag">{advisor.specialization}</span>,
    },
    {
      key: 'languages',
      header: 'Languages',
      render: (advisor) => (
        // The column holds "FR,EN,IT". Split for display so the languages
        // read as a list rather than as one run-on token.
        <span className="advisorLangs">
          {(advisor.spoken_languages || '')
            .split(',')
            .map((language) => language.trim())
            .filter(Boolean)
            .map((language) => (
              <span className="advisorLangs__item" key={language}>
                {language}
              </span>
            ))}
        </span>
      ),
    },
    {
      key: 'status',
      header: 'Status',
      render: (advisor) => (
        <span className={`status status--${(advisor.advisor_status || '').toLowerCase()}`}>
          {advisor.advisor_status}
        </span>
      ),
    },
  ]

  return (
    <Shell
      title="Advisors"
      subtitle="The Orialis advisory team. Select a row to see that advisor's client book."
    >
      <div className="list">
        <FilterBar
          filters={filters}
          values={list.filters}
          onChange={list.setFilter}
          onReset={list.resetFilters}
          resultCount={advisors.data?.pagination?.total_records ?? null}
          loading={advisors.loading}
        />

        <DataTable
          columns={columns}
          rows={advisors.data?.data ?? []}
          rowKey={(advisor) => advisor.advisor_id}
          loading={advisors.loading}
          error={advisors.error}
          emptyMessage="No advisor matches these filters."
          // Straight to that advisor's clients - the question a row actually
          // raises.
          onRowClick={(advisor) => navigate(`/clients?advisor_id=${advisor.advisor_id}`)}
        />

        <Pagination
          pagination={advisors.data?.pagination}
          onPageChange={list.setPage}
          onPageSizeChange={list.changePageSize}
        />
      </div>
    </Shell>
  )
}

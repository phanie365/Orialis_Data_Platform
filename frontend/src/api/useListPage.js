/**
 * Filter and pagination state for a list page.
 *
 * The three list pages share the same behaviour, and getting one detail wrong
 * in one of them would be a bug nobody notices for a while: changing a filter
 * must send you back to page 1. Without that, filtering while on page 7 asks
 * the API for page 7 of a result set that now has two pages, and the table
 * goes blank on what looks like a working filter.
 *
 * So the rule lives here, once, rather than in each page.
 */

import { useCallback, useMemo, useState } from 'react'

export function useListPage(initialFilters, initialPageSize = 25) {
  const [filters, setFilters] = useState(initialFilters)
  const [page, setPage] = useState(1)
  const [pageSize, setPageSize] = useState(initialPageSize)

  const setFilter = useCallback((name, value) => {
    setFilters((current) => ({ ...current, [name]: value }))
    setPage(1)
  }, [])

  const resetFilters = useCallback(() => {
    setFilters(initialFilters)
    setPage(1)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const changePageSize = useCallback((size) => {
    setPageSize(size)
    // Row 400 of the old paging is not row 400 of the new one; returning to
    // the first page is the only honest answer.
    setPage(1)
  }, [])

  // The exact object handed to the API. Serialised into the effect's
  // dependency list so a request fires when - and only when - something
  // actually changed.
  const query = useMemo(
    () => ({ ...filters, page, page_size: pageSize }),
    [filters, page, pageSize],
  )

  const queryKey = JSON.stringify(query)

  return {
    filters,
    setFilter,
    resetFilters,
    page,
    setPage,
    pageSize,
    changePageSize,
    query,
    queryKey,
  }
}

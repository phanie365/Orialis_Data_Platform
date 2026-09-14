import { createContext, useCallback, useContext, useEffect, useMemo, useState } from 'react'

import { buildHash, listParams, parseHash, pick, readListState } from './lib.js'

/**
 * Run `load(signal)` whenever `deps` change. Pass `null` instead of a loader
 * to stay idle (no request). The previous data is kept while a new request is
 * in flight, so a list can stay visible - dimmed - while its next page loads.
 */
export function useFetch(load, deps) {
  const [state, setState] = useState({ data: null, error: null, loading: Boolean(load) })
  const [token, setToken] = useState(0)
  const reload = useCallback(() => setToken((value) => value + 1), [])
  const enabled = Boolean(load)

  useEffect(() => {
    if (!load) {
      setState({ data: null, error: null, loading: false })
      return undefined
    }
    const controller = new AbortController()
    setState((previous) => ({ data: previous.data, error: null, loading: true }))
    load(controller.signal).then(
      (data) => {
        if (!controller.signal.aborted) setState({ data, error: null, loading: false })
      },
      (error) => {
        if (controller.signal.aborted || error?.name === 'AbortError') return
        setState({ data: null, error, loading: false })
      },
    )
    return () => controller.abort()
    // `load` is a new function on every render; `deps` say when it matters.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [...deps, token, enabled])

  return { ...state, reload }
}

/** The current screen and its parameters, read from `location.hash`. */
export function useHashRoute() {
  const [hash, setHash] = useState(() => window.location.hash)

  useEffect(() => {
    const onChange = () => setHash(window.location.hash)
    window.addEventListener('hashchange', onChange)
    return () => window.removeEventListener('hashchange', onChange)
  }, [])

  const navigate = useCallback((screen, params = {}, { replace = false } = {}) => {
    const next = buildHash(screen, params)
    if (next === window.location.hash) return
    if (replace) {
      window.history.replaceState(null, '', next)
      setHash(next)
    } else {
      window.location.hash = next // fires hashchange
    }
  }, [])

  const route = useMemo(() => parseHash(hash), [hash])
  return { ...route, navigate }
}

/** Filters, page, page size and open detail of a list screen, kept in the hash. */
export function useListState(screen, params, navigate, filterNames) {
  const state = readListState(params, filterNames)
  const update = (changes, options) => navigate(screen, listParams({ ...state, ...changes }), options)

  return {
    ...state,
    update,
    applyFilters: (filters) => update({ filters: pick(filters, filterNames), page: 1, selected: null }),
    resetFilters: () => update({ filters: {}, page: 1, selected: null }),
    setPage: (page) => update({ page }),
    setPageSize: (pageSize) => update({ pageSize, page: 1 }),
    select: (id) => update({ selected: id }),
    close: () => update({ selected: null }),
  }
}

export const ReferenceContext = createContext(null)

export function useReference() {
  const reference = useContext(ReferenceContext)
  if (!reference) throw new Error('useReference must be used below ReferenceContext.Provider')
  return reference
}

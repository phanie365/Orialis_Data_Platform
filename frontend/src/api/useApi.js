/**
 * A minimal data-fetching hook.
 *
 * No React Query, no SWR, no Zustand. The dashboard makes two requests on
 * mount and does not refetch, paginate, mutate or cache across routes. A
 * caching library here would be more configuration than code.
 *
 * What it does handle is the part that actually bites: a component that
 * unmounts before its request resolves. Without the abort and the `cancelled`
 * guard, React would warn about a state update on an unmounted component,
 * and in StrictMode - which mounts everything twice in development - the
 * effect would race with itself.
 */

import { useCallback, useEffect, useState } from 'react'

export function useApi(fetcher, dependencies = []) {
  const [data, setData] = useState(null)
  const [error, setError] = useState(null)
  const [loading, setLoading] = useState(true)
  const [reloadToken, setReloadToken] = useState(0)

  const reload = useCallback(() => setReloadToken((n) => n + 1), [])

  useEffect(() => {
    const controller = new AbortController()
    let cancelled = false

    setLoading(true)
    setError(null)

    fetcher({ signal: controller.signal })
      .then((result) => {
        if (!cancelled) {
          setData(result)
          setLoading(false)
        }
      })
      .catch((caught) => {
        // An aborted request is this effect cleaning up after itself, not a
        // failure to report.
        if (cancelled || caught.name === 'AbortError') return
        setError(caught)
        setLoading(false)
      })

    return () => {
      cancelled = true
      controller.abort()
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [...dependencies, reloadToken])

  return { data, error, loading, reload }
}

/**
 * A minimal History-API router.
 *
 * Six routes and one dynamic segment. React Router would do this too, and do
 * it well - but it would be the third dependency in a project that has so far
 * refused a chart library and a UI kit for the same reason. What is needed
 * here is: match a path, render a component, navigate without reloading, and
 * honour the browser's back button. That is the file below.
 *
 * Deep links work in development and under `vite preview` because Vite serves
 * index.html for unknown paths. A static host will need the same SPA fallback
 * rule - that belongs to the deployment step, and is noted rather than
 * pre-empted here.
 */

import { createContext, useCallback, useContext, useEffect, useMemo, useState } from 'react'

const RouterContext = createContext(null)

/** Turn "/clients/:clientId" into a matcher for "/clients/CLT0001". */
function matchPath(pattern, pathname) {
  const patternParts = pattern.split('/').filter(Boolean)
  const pathParts = pathname.split('/').filter(Boolean)

  if (patternParts.length !== pathParts.length) return null

  const params = {}
  for (let i = 0; i < patternParts.length; i += 1) {
    const patternPart = patternParts[i]
    if (patternPart.startsWith(':')) {
      // An id may legitimately contain characters that were encoded in the
      // URL, so it is decoded on the way out.
      params[patternPart.slice(1)] = decodeURIComponent(pathParts[i])
    } else if (patternPart !== pathParts[i]) {
      return null
    }
  }
  return params
}

/** The current location, as one comparable string. */
function currentLocation() {
  return window.location.pathname + window.location.search
}

export function RouterProvider({ children }) {
  // Pathname AND search. Tracking only the pathname would make
  // /clients?advisor_id=ADV012 indistinguishable from /clients, and a link
  // that carries a filter would arrive with the filter silently dropped.
  const [location, setLocation] = useState(currentLocation)

  useEffect(() => {
    // Back and forward buttons. Without this the URL would change and the
    // view would not.
    const onPopState = () => setLocation(currentLocation())
    window.addEventListener('popstate', onPopState)
    return () => window.removeEventListener('popstate', onPopState)
  }, [])

  const navigate = useCallback((to, { replace = false } = {}) => {
    if (to === currentLocation()) return
    window.history[replace ? 'replaceState' : 'pushState']({}, '', to)
    setLocation(to)
    // A new page starts at the top. The browser does this on a real
    // navigation; a pushState does not, and landing halfway down a client
    // list after clicking into a detail page is disorienting.
    window.scrollTo(0, 0)
  }, [])

  const value = useMemo(() => {
    const [pathname, search = ''] = location.split('?')
    return { location, pathname, search, navigate }
  }, [location, navigate])

  return <RouterContext.Provider value={value}>{children}</RouterContext.Provider>
}

/**
 * The current query string, parsed.
 *
 * Used to seed a list page's filters from the URL, which is what makes
 * /clients?advisor_id=ADV012 a shareable link rather than a decoration.
 */
export function useSearchParams() {
  const { search } = useRouter()
  return useMemo(() => new URLSearchParams(search), [search])
}

export function useRouter() {
  const context = useContext(RouterContext)
  if (!context) throw new Error('useRouter must be used inside a RouterProvider')
  return context
}

/**
 * Pick the first matching route.
 *
 * `routes` is an array of { path, element }, where `element` is a function
 * receiving the matched params.
 */
export function useRoutes(routes, fallback) {
  const { pathname } = useRouter()

  for (const route of routes) {
    const params = matchPath(route.path, pathname)
    if (params) return route.element(params)
  }
  return fallback
}

/**
 * An anchor that navigates without a full page load.
 *
 * It stays a real `<a href>`, so middle-click, ctrl-click and "open in new
 * tab" keep working, and a screen reader still announces a link. Only the
 * plain left-click is intercepted.
 */
export function Link({ to, className, children, ...rest }) {
  const { navigate } = useRouter()

  const onClick = (event) => {
    if (
      event.defaultPrevented ||
      event.button !== 0 ||
      event.metaKey || event.ctrlKey || event.shiftKey || event.altKey
    ) {
      return
    }
    event.preventDefault()
    navigate(to)
  }

  return (
    <a href={to} className={className} onClick={onClick} {...rest}>
      {children}
    </a>
  )
}

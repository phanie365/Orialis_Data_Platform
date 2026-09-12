/**
 * The only place the frontend talks to the network.
 *
 * Note what is absent: there is no API key here, no `import.meta.env`, no
 * host, no port. Requests go to the RELATIVE path `/api/...`, which means
 * they go back to the origin that served the page. The dev server proxies
 * them onward and attaches the credential server-side (see vite.config.js).
 *
 * That is not a stylistic choice. A key referenced anywhere under `src/`
 * would be compiled into the bundle and served to every visitor. Keeping
 * this module credential-free is what makes the guarantee hold.
 *
 * It also means the production proxy - a decision left for the deployment
 * step - can be anything at all, and no file in this directory will change.
 */

const BASE = '/api/v1'

/**
 * Raised when the API answers, but with a failure status. Carries the status
 * so the UI can say something useful rather than "an error occurred" - a 401
 * means the key is missing, which is a different problem from a 500.
 */
export class ApiError extends Error {
  constructor(message, status) {
    super(message)
    this.name = 'ApiError'
    this.status = status
  }
}

async function request(path, { signal } = {}) {
  let response
  try {
    response = await fetch(BASE + path, {
      signal,
      headers: { Accept: 'application/json' },
    })
  } catch (error) {
    // AbortError is a cancelled request, not a failure - let the caller
    // recognise it instead of reporting a phantom outage.
    if (error.name === 'AbortError') throw error
    throw new ApiError(
      'The CRM API could not be reached. Is it running on port 8000?',
      0,
    )
  }

  if (!response.ok) {
    if (response.status === 401) {
      throw new ApiError(
        'The CRM API rejected the request. CRM_API_KEY is missing or wrong in frontend/.env.',
        401,
      )
    }
    // FastAPI puts the reason in `detail`; fall back to the status line when
    // the body is not the JSON we expect.
    let detail = `${response.status} ${response.statusText}`
    try {
      const body = await response.json()
      if (body?.detail) detail = body.detail
    } catch {
      /* keep the status line */
    }
    throw new ApiError(detail, response.status)
  }

  return response.json()
}

/**
 * Build a query string from a filter object, dropping everything empty.
 *
 * The distinction matters: an absent filter and a filter set to "" are the
 * same intention - no filter - but sending `?country=` would ask the API for
 * clients whose country is the empty string, and get none back.
 */
export function buildQuery(parameters) {
  const search = new URLSearchParams()
  for (const [key, value] of Object.entries(parameters)) {
    if (value === undefined || value === null || value === '') continue
    search.set(key, value)
  }
  const query = search.toString()
  return query ? `?${query}` : ''
}

/**
 * Every aggregate the dashboard needs, in one call.
 *
 * GET /api/v1/stats/overview
 */
export function fetchOverview(options) {
  return request('/stats/overview', options)
}

/**
 * The most recent interactions, for the activity panel.
 *
 * Deliberately a SECOND call rather than a field inside the overview: the
 * overview returns aggregates, this returns CRM records. They are different
 * kinds of data with different lifetimes, and the endpoint that serves one
 * has no business serving the other.
 *
 * The list endpoint already sorts by `interaction_date DESC, interaction_id
 * DESC`, so no sort parameter is needed - and the id tiebreaker means the
 * eight rows are stable rather than reshuffling between calls.
 *
 * GET /api/v1/interactions?page_size=8
 */
export function fetchRecentInteractions(options, pageSize = 8) {
  return request(`/interactions?page_size=${pageSize}`, options)
}


/**
 * The distinct values of every filterable dimension.
 *
 * Fetched once per list page so the filters can be pickers rather than
 * free-text boxes, without the frontend holding its own copy of the CRM's
 * reference values.
 *
 * GET /api/v1/stats/filters
 */
export function fetchFilterValues(options) {
  return request('/stats/filters', options)
}

/**
 * One page of clients, server-filtered and server-paginated.
 *
 * Nothing is filtered or sorted in the browser: the 5,000 clients stay in
 * PostgreSQL and only the requested page crosses the network.
 *
 * GET /api/v1/clients
 */
export function fetchClients(parameters, options) {
  return request(`/clients${buildQuery(parameters)}`, options)
}

/** GET /api/v1/clients/{client_id} */
export function fetchClient(clientId, options) {
  return request(`/clients/${encodeURIComponent(clientId)}`, options)
}

/** GET /api/v1/advisors */
export function fetchAdvisors(parameters, options) {
  return request(`/advisors${buildQuery(parameters)}`, options)
}

/** GET /api/v1/advisors/{advisor_id} */
export function fetchAdvisor(advisorId, options) {
  return request(`/advisors/${encodeURIComponent(advisorId)}`, options)
}

/**
 * Every branch. The endpoint is deliberately unpaginated - there are seven -
 * so this is one call returning the whole reference table.
 *
 * GET /api/v1/branches
 */
export function fetchBranches(options) {
  return request('/branches', options)
}

/** GET /api/v1/interactions */
export function fetchInteractions(parameters, options) {
  return request(`/interactions${buildQuery(parameters)}`, options)
}

/**
 * Every advisor, in one page.
 *
 * There are 100 and the endpoint allows `page_size` up to 200, so this is a
 * single request returning about 100 rows - roughly 20 kB. It is used to
 * resolve `advisor_id` into a readable name on the client and interaction
 * lists, and to populate the advisor picker.
 *
 * This is the one place the frontend holds a whole table in memory, and it
 * is a deliberate, bounded exception: 100 rows is not 5,000. The same trick
 * would be indefensible for clients.
 */
export function fetchAllAdvisors(options) {
  return request('/advisors?page_size=200', options)
}

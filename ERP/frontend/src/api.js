/**
 * The only place the frontend talks to the network.
 *
 * No key, no host, no port, no environment variable: every request goes to the
 * relative path `/api/v1/...`, and the dev-server proxy (apiProxy.js) attaches
 * the credential in Node. Only endpoints that already exist are called.
 */

import { buildQuery } from './lib.js'

const BASE = '/api/v1'

export class ApiError extends Error {
  constructor(message, status) {
    super(message)
    this.name = 'ApiError'
    this.status = status
  }
}

/** FastAPI errors: `detail` is a string (404) or a list of problems (422). */
function describeDetail(detail) {
  if (typeof detail === 'string') return detail
  if (Array.isArray(detail)) {
    return detail
      .map((problem) => {
        const field = Array.isArray(problem?.loc) ? problem.loc[problem.loc.length - 1] : null
        return field ? `${field}: ${problem.msg}` : problem?.msg
      })
      .filter(Boolean)
      .join('; ')
  }
  return null
}

export async function request(path, { signal } = {}) {
  let response
  try {
    response = await fetch(BASE + path, { signal, headers: { Accept: 'application/json' } })
  } catch (error) {
    if (error?.name === 'AbortError') throw error
    throw new ApiError('The ERP API could not be reached through the frontend server.', 0)
  }

  const text = await response.text()
  let body
  try {
    body = text ? JSON.parse(text) : null
  } catch {
    body = undefined // not JSON
  }

  if (!response.ok) {
    if (response.status === 401) {
      throw new ApiError(
        'The ERP API rejected the request: ERP_API_KEY is missing or wrong on the frontend ' +
          'server (ERP/frontend/.env).',
        401,
      )
    }
    const detail = describeDetail(body?.detail)
    if (detail) throw new ApiError(detail, response.status)
    if (response.status >= 500) {
      throw new ApiError(
        `The ERP API did not answer (HTTP ${response.status}). Is it running at ERP_API_URL ` +
          '(default http://127.0.0.1:8001)?',
        response.status,
      )
    }
    throw new ApiError(`HTTP ${response.status}`, response.status)
  }

  if (body === undefined || body === null) {
    throw new ApiError('The ERP API returned an unexpected, non-JSON response.', response.status)
  }
  return body
}

const byId = (resource, id) => `/${resource}/${encodeURIComponent(id)}`

export const fetchOverview = (options) => request('/stats/overview', options)
export const fetchFilterValues = (options) => request('/stats/filters', options)

export const fetchSuppliers = (params, options) => request(`/suppliers${buildQuery(params)}`, options)
export const fetchSupplier = (id, options) => request(byId('suppliers', id), options)

export const fetchCostCenters = (params, options) =>
  request(`/cost-centers${buildQuery(params)}`, options)

export const fetchInvoices = (params, options) => request(`/invoices${buildQuery(params)}`, options)
export const fetchInvoice = (id, options) => request(byId('invoices', id), options)
export const fetchInvoiceAllocations = (id, options) =>
  request(`${byId('invoices', id)}/allocations`, options)

export const fetchPayments = (params, options) => request(`/payments${buildQuery(params)}`, options)
export const fetchPayment = (id, options) => request(byId('payments', id), options)
export const fetchPaymentAllocations = (id, options) =>
  request(`${byId('payments', id)}/allocations`, options)

/** A count, read from `total_records` of a one-row page. No row is used. */
export async function countInvoices(filters, options) {
  const page = await fetchInvoices({ ...filters, page: 1, page_size: 1 }, options)
  return page.pagination.total_records
}

export async function countPayments(filters, options) {
  const page = await fetchPayments({ ...filters, page: 1, page_size: 1 }, options)
  return page.pagination.total_records
}

/**
 * Reference data loaded once at start-up: filter values, every supplier
 * (180 rows, one page of 200) and every cost centre (25 rows). It turns ids
 * into names on the lists and feeds the pickers and the supplier search.
 */
export async function loadReferenceData(options) {
  const [filters, suppliers, costCenters] = await Promise.all([
    fetchFilterValues(options),
    fetchSuppliers({ page_size: 200 }, options),
    fetchCostCenters({ page_size: 200 }, options),
  ])

  const supplierList = [...suppliers.data].sort(
    (a, b) =>
      a.supplier_name.localeCompare(b.supplier_name) || a.supplier_id.localeCompare(b.supplier_id),
  )
  const costCenterList = [...costCenters.data].sort((a, b) =>
    a.cost_center_id.localeCompare(b.cost_center_id),
  )

  return {
    filters,
    suppliers: supplierList,
    suppliersById: new Map(supplierList.map((supplier) => [supplier.supplier_id, supplier])),
    // True if the supplier table ever outgrows one page: names and search
    // would then be incomplete, and the Suppliers screen says so.
    suppliersTruncated: suppliers.pagination.total_records > suppliers.data.length,
    costCenters: costCenterList,
    costCentersById: new Map(costCenterList.map((costCenter) => [costCenter.cost_center_id, costCenter])),
  }
}

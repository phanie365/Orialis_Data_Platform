/**
 * Pure helpers: no React, no DOM, no network. Kept apart so that tests/ can
 * run them under plain `node --test`.
 */

// The four screens, and nothing else. Expenses and commissions are published
// as file extracts, not through the API, so they have no screen here.
export const SCREENS = ['dashboard', 'invoices', 'payments', 'suppliers']

export const PAGE_SIZES = [25, 50, 100]
export const DEFAULT_PAGE_SIZE = 25

/** `{a: 'x', b: ''}` -> `?a=x`. Empty values mean "no filter" and are dropped. */
export function buildQuery(parameters = {}) {
  const search = new URLSearchParams()
  for (const [key, value] of Object.entries(parameters)) {
    if (value === undefined || value === null || value === '') continue
    search.set(key, String(value))
  }
  const query = search.toString()
  return query ? `?${query}` : ''
}

// ---------------------------------------------------------------------------
// Hash routing: `#/invoices?supplier_id=SUP-00002&page=3&selected=INV-...`
// ---------------------------------------------------------------------------
// Screen, filters, page and open detail all live in the hash, so a filtered
// view can be linked to from another screen, reloaded, or bookmarked.

export function parseHash(hash) {
  const raw = String(hash ?? '').replace(/^#\/?/, '')
  const cut = raw.indexOf('?')
  const path = cut === -1 ? raw : raw.slice(0, cut)
  const search = cut === -1 ? '' : raw.slice(cut + 1)
  const screen = path === '' ? 'dashboard' : SCREENS.includes(path) ? path : null
  return { screen, params: Object.fromEntries(new URLSearchParams(search)) }
}

export function buildHash(screen, params = {}) {
  return `#/${screen}${buildQuery(params)}`
}

export function pick(params, names) {
  const picked = {}
  for (const name of names) {
    if (params[name] !== undefined && params[name] !== null && params[name] !== '') {
      picked[name] = String(params[name])
    }
  }
  return picked
}

function positiveInteger(value, fallback) {
  const number = Number(value)
  return Number.isInteger(number) && number >= 1 ? number : fallback
}

/** Read the list state of a screen from its hash parameters, with defaults. */
export function readListState(params, filterNames) {
  const size = Number(params.page_size)
  return {
    filters: pick(params, filterNames),
    page: positiveInteger(params.page, 1),
    pageSize: PAGE_SIZES.includes(size) ? size : DEFAULT_PAGE_SIZE,
    selected: params.selected || null,
  }
}

/** The inverse: hash parameters for a list state, defaults omitted. */
export function listParams({ filters = {}, page = 1, pageSize = DEFAULT_PAGE_SIZE, selected = null }) {
  return {
    ...filters,
    page: page > 1 ? page : undefined,
    page_size: pageSize !== DEFAULT_PAGE_SIZE ? pageSize : undefined,
    selected: selected || undefined,
  }
}

// ---------------------------------------------------------------------------
// Business helpers
// ---------------------------------------------------------------------------

/** A real calendar day written YYYY-MM-DD. `2026-02-30` is not one. */
export function isIsoDate(value) {
  if (typeof value !== 'string' || !/^\d{4}-\d{2}-\d{2}$/.test(value)) return false
  const [year, month, day] = value.split('-').map(Number)
  const date = new Date(Date.UTC(year, month - 1, day))
  return (
    date.getUTCFullYear() === year && date.getUTCMonth() === month - 1 && date.getUTCDate() === day
  )
}

/**
 * The invoice filters meaning "overdue as of `asOf`".
 *
 * The date is ALWAYS supplied by the user. The ERP runs on simulated time, so
 * the browser's clock says nothing about the business date - this function
 * never falls back to it, and returns null without a valid date.
 *
 * Partially Paid invoices are not included: the API filters on one payment
 * status at a time.
 */
export function overdueFilters(asOf) {
  if (!isIsoDate(asOf)) return null
  return {
    invoice_approval_status: 'Approved',
    invoice_payment_status: 'Unpaid',
    due_before: asOf,
  }
}

/** Lowercase, accents removed: "Société" matches "societe". */
export function normalizeText(value) {
  return String(value ?? '')
    .normalize('NFD')
    .replace(/[̀-ͯ]/g, '')
    .toLowerCase()
    .trim()
}

/**
 * Filter the suppliers already loaded in the browser (about 180 rows).
 * `q` searches the id, the trading name, the legal name and the VAT number.
 */
export function filterSuppliers(suppliers, filters = {}) {
  const query = normalizeText(filters.q)
  return suppliers.filter(
    (supplier) =>
      (!filters.supplier_status || supplier.supplier_status === filters.supplier_status) &&
      (!filters.supplier_category || supplier.supplier_category === filters.supplier_category) &&
      (!filters.country || supplier.country_code === filters.country) &&
      (!filters.currency || supplier.default_currency === filters.currency) &&
      (!filters.payment_terms_days ||
        String(supplier.payment_terms_days) === String(filters.payment_terms_days)) &&
      (!query ||
        [supplier.supplier_id, supplier.supplier_name, supplier.supplier_legal_name, supplier.vat_number]
          .some((value) => normalizeText(value).includes(query))),
  )
}

/** Local pagination for a list held in memory. The page is clamped to range. */
export function slicePage(rows, page, pageSize) {
  const total = rows.length
  const totalPages = Math.ceil(total / pageSize)
  const current = Math.min(Math.max(page, 1), Math.max(totalPages, 1))
  return {
    rows: rows.slice((current - 1) * pageSize, current * pageSize),
    page: current,
    total,
    totalPages,
  }
}

export function supplierName(suppliersById, supplierId) {
  return suppliersById.get(supplierId)?.supplier_name ?? supplierId
}

export function costCenterLabel(costCentersById, costCenterId) {
  const costCenter = costCentersById.get(costCenterId)
  return costCenter ? `${costCenter.cost_center_id} · ${costCenter.cost_center_name}` : costCenterId
}

import assert from 'node:assert/strict'
import { test } from 'node:test'

import {
  buildHash, buildQuery, filterSuppliers, isIsoDate, listParams, overdueFilters, parseHash,
  readListState, slicePage,
} from '../src/lib.js'

test('buildQuery drops empty values and encodes business labels', () => {
  assert.equal(
    buildQuery({ invoice_approval_status: 'Pending Approval', supplier_id: '', currency: null, page: 2 }),
    '?invoice_approval_status=Pending+Approval&page=2',
  )
  assert.equal(buildQuery({}), '')
})

test('hash routes round-trip, and only the four screens exist', () => {
  const hash = buildHash('invoices', { supplier_id: 'SUP-00002', invoice_approval_status: 'Pending Approval' })
  assert.deepEqual(parseHash(hash), {
    screen: 'invoices',
    params: { supplier_id: 'SUP-00002', invoice_approval_status: 'Pending Approval' },
  })
  assert.equal(parseHash('').screen, 'dashboard')
  assert.equal(parseHash('#/').screen, 'dashboard')
  assert.equal(parseHash('#/expenses').screen, null)
  assert.equal(parseHash('#/commissions').screen, null)
})

test('list state falls back to safe defaults and omits them from the hash', () => {
  const state = readListState({ page: '-3', page_size: '1000', currency: 'CHF', junk: 'x' }, ['currency'])
  assert.deepEqual(state, { filters: { currency: 'CHF' }, page: 1, pageSize: 25, selected: null })
  assert.equal(buildQuery(listParams(state)), '?currency=CHF')
  assert.equal(
    buildQuery(listParams({ ...state, page: 3, pageSize: 50, selected: 'INV-2026-00001' })),
    '?currency=CHF&page=3&page_size=50&selected=INV-2026-00001',
  )
})

test('isIsoDate accepts real calendar days only', () => {
  assert.equal(isIsoDate('2026-10-25'), true)
  assert.equal(isIsoDate('2026-02-30'), false)
  assert.equal(isIsoDate('25/10/2026'), false)
  assert.equal(isIsoDate(''), false)
  assert.equal(isIsoDate(undefined), false)
})

test('overdue filters need an explicit business date and never use the browser clock', () => {
  assert.equal(overdueFilters(''), null)
  assert.equal(overdueFilters(undefined), null)
  assert.equal(overdueFilters('not-a-date'), null)

  const realNow = Date.now
  Date.now = () => { throw new Error('the browser clock must not be read') }
  try {
    assert.deepEqual(overdueFilters('2026-10-25'), {
      invoice_approval_status: 'Approved',
      invoice_payment_status: 'Unpaid',
      due_before: '2026-10-25',
    })
  } finally {
    Date.now = realNow
  }
})

const SUPPLIERS = [
  { supplier_id: 'SUP-00001', supplier_name: 'Kestrel Digital', supplier_legal_name: 'Kestrel Digital SPRL', vat_number: 'BE46722894677', supplier_status: 'Active', supplier_category: 'IT & Software', country_code: 'BE', default_currency: 'EUR', payment_terms_days: 60 },
  { supplier_id: 'SUP-00002', supplier_name: 'Falcon Travel', supplier_legal_name: 'Société Falcon Voyages SA', vat_number: 'FR12345678901', supplier_status: 'Blocked', supplier_category: 'Travel', country_code: 'FR', default_currency: 'EUR', payment_terms_days: 0 },
  { supplier_id: 'SUP-00003', supplier_name: 'Alpine Data', supplier_legal_name: null, vat_number: null, supplier_status: 'Active', supplier_category: 'Market Data', country_code: 'CH', default_currency: 'CHF', payment_terms_days: 30 },
]

test('supplier search is local, case- and accent-insensitive, on id, names and VAT', () => {
  const ids = (rows) => rows.map((row) => row.supplier_id)
  assert.deepEqual(ids(filterSuppliers(SUPPLIERS, { q: 'KESTREL' })), ['SUP-00001'])
  assert.deepEqual(ids(filterSuppliers(SUPPLIERS, { q: 'societe' })), ['SUP-00002'])
  assert.deepEqual(ids(filterSuppliers(SUPPLIERS, { q: 'sup-00003' })), ['SUP-00003'])
  assert.deepEqual(ids(filterSuppliers(SUPPLIERS, { q: 'FR1234' })), ['SUP-00002'])
  assert.equal(filterSuppliers(SUPPLIERS, { q: '   ' }).length, 3)
})

test('supplier filters combine, and payment terms of 0 days still filter', () => {
  const ids = (rows) => rows.map((row) => row.supplier_id)
  assert.deepEqual(ids(filterSuppliers(SUPPLIERS, { supplier_status: 'Active', currency: 'CHF' })), ['SUP-00003'])
  assert.deepEqual(ids(filterSuppliers(SUPPLIERS, { payment_terms_days: '0' })), ['SUP-00002'])
  assert.deepEqual(filterSuppliers(SUPPLIERS, { country: 'IT' }), [])
})

test('local pagination clamps the page into range', () => {
  const rows = Array.from({ length: 180 }, (_, index) => index)
  assert.deepEqual(
    { ...slicePage(rows, 4, 50), rows: undefined },
    { rows: undefined, page: 4, total: 180, totalPages: 4 },
  )
  assert.equal(slicePage(rows, 4, 50).rows.length, 30)
  assert.equal(slicePage(rows, 99, 50).page, 4)
  assert.deepEqual(slicePage([], 3, 25), { rows: [], page: 1, total: 0, totalPages: 0 })
})

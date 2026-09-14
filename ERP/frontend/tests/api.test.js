import assert from 'node:assert/strict'
import { afterEach, test } from 'node:test'

import {
  ApiError, countInvoices, fetchInvoice, fetchInvoices, loadReferenceData, request,
} from '../src/api.js'

const realFetch = globalThis.fetch
afterEach(() => { globalThis.fetch = realFetch })

function mockFetch(handler) {
  const calls = []
  globalThis.fetch = async (url, init = {}) => {
    calls.push({ url, init })
    return handler(url, init)
  }
  return calls
}

const json = (status, body) =>
  new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })

test('requests use the relative /api/v1 path and never carry an API key', async () => {
  const calls = mockFetch(() => json(200, { data: [], pagination: {} }))
  await fetchInvoices({ invoice_approval_status: 'Pending Approval', supplier_id: '', page: 2 })

  assert.equal(calls[0].url, '/api/v1/invoices?invoice_approval_status=Pending+Approval&page=2')
  const headerNames = Object.keys(calls[0].init.headers ?? {}).map((name) => name.toLowerCase())
  assert.equal(headerNames.includes('x-api-key'), false)
})

test('ids are URL-encoded', async () => {
  const calls = mockFetch(() => json(200, { invoice_id: 'x' }))
  await fetchInvoice('INV/2026 01')
  assert.equal(calls[0].url, '/api/v1/invoices/INV%2F2026%2001')
})

test('a 401 names ERP_API_KEY', async () => {
  mockFetch(() => json(401, { detail: 'Missing or invalid API key' }))
  await assert.rejects(request('/suppliers'), (error) =>
    error instanceof ApiError && error.status === 401 && error.message.includes('ERP_API_KEY'))
})

test('FastAPI details are readable: a 404 string and a 422 list', async () => {
  mockFetch(() => json(404, { detail: 'Invoice not found' }))
  await assert.rejects(request('/invoices/NOPE'), { message: 'Invoice not found', status: 404 })

  mockFetch(() =>
    json(422, { detail: [{ loc: ['query', 'page_size'], msg: 'Input should be less than or equal to 500' }] }))
  await assert.rejects(request('/invoices?page_size=900'), {
    message: 'page_size: Input should be less than or equal to 500',
    status: 422,
  })
})

test('an unreachable API and a non-JSON proxy error are reported, not thrown raw', async () => {
  mockFetch(() => { throw new TypeError('fetch failed') })
  await assert.rejects(request('/stats/overview'), (error) => error instanceof ApiError && error.status === 0)

  mockFetch(() => new Response('', { status: 500 }))
  await assert.rejects(request('/stats/overview'), (error) => error.status === 500 && error.message.includes('8001'))

  mockFetch(() => new Response('<!doctype html>', { status: 200 }))
  await assert.rejects(request('/stats/overview'), /non-JSON/)
})

test('a count asks for a single row and reads total_records', async () => {
  const calls = mockFetch(() => json(200, { data: [{}], pagination: { total_records: 155 } }))
  const count = await countInvoices({ invoice_approval_status: 'Approved', due_before: '2026-10-25' })

  assert.equal(count, 155)
  assert.equal(calls[0].url, '/api/v1/invoices?invoice_approval_status=Approved&due_before=2026-10-25&page=1&page_size=1')
})

test('reference data sorts suppliers by name and flags a truncated supplier list', async () => {
  const calls = mockFetch((url) => {
    if (url.startsWith('/api/v1/stats/filters')) return json(200, { currency: ['CHF', 'EUR'] })
    if (url.startsWith('/api/v1/suppliers')) {
      return json(200, {
        data: [
          { supplier_id: 'SUP-2', supplier_name: 'Zeta' },
          { supplier_id: 'SUP-1', supplier_name: 'Alpha' },
        ],
        pagination: { total_records: 201 },
      })
    }
    return json(200, { data: [{ cost_center_id: 'CC-1', cost_center_name: 'Group' }], pagination: { total_records: 1 } })
  })

  const reference = await loadReferenceData()
  assert.deepEqual(reference.suppliers.map((s) => s.supplier_name), ['Alpha', 'Zeta'])
  assert.equal(reference.suppliersById.get('SUP-2').supplier_name, 'Zeta')
  assert.equal(reference.suppliersTruncated, true)
  assert.ok(calls.some((call) => call.url === '/api/v1/suppliers?page_size=200'))
})

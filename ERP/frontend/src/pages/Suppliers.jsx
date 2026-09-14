import { useEffect, useMemo, useState } from 'react'

import { countInvoices, countPayments, fetchSupplier } from '../api.js'
import {
  DataTable, DetailPanel, Empty, Fields, FilterForm, Link, Pager, Remote,
} from '../components.jsx'
import { formatCount, formatDate, formatDateTime } from '../format.js'
import { useFetch, useListState, useReference } from '../hooks.js'
import { filterSuppliers, slicePage } from '../lib.js'

// `q` is the free-text search. Every filter here runs in the browser, on the
// suppliers loaded once at start-up (about 180 rows).
const FILTERS = ['supplier_status', 'supplier_category', 'country', 'currency', 'payment_terms_days', 'q']

export function Suppliers({ params, navigate }) {
  const reference = useReference()
  const list = useListState('suppliers', params, navigate, FILTERS)

  const [search, setSearch] = useState(list.filters.q ?? '')
  useEffect(() => setSearch(list.filters.q ?? ''), [list.filters.q])

  const filtersKey = JSON.stringify(list.filters)
  const matches = useMemo(
    () => filterSuppliers(reference.suppliers, list.filters),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [reference.suppliers, filtersKey],
  )
  const view = slicePage(matches, list.page, list.pageSize)

  const terms = [...new Set(reference.suppliers.map((s) => s.payment_terms_days))].sort((a, b) => a - b)
  const fields = [
    { name: 'supplier_status', label: 'Status', type: 'select', options: reference.filters.supplier_status },
    { name: 'supplier_category', label: 'Category', type: 'select', options: reference.filters.supplier_category },
    { name: 'country', label: 'Country', type: 'select', options: reference.filters.supplier_country },
    { name: 'currency', label: 'Currency', type: 'select', options: reference.filters.currency },
    { name: 'payment_terms_days', label: 'Payment terms (days)', type: 'select', options: terms },
  ]

  const columns = [
    { key: 'supplier_id', header: 'Supplier' },
    { key: 'supplier_name', header: 'Name' },
    { key: 'supplier_category', header: 'Category' },
    { key: 'country_code', header: 'Country' },
    { key: 'default_currency', header: 'Currency' },
    { key: 'payment_terms_days', header: 'Terms (days)', numeric: true },
    { key: 'supplier_status', header: 'Status' },
    { key: 'onboarded_on', header: 'Onboarded', render: (r) => formatDate(r.onboarded_on) },
    { key: 'deactivated_on', header: 'Deactivated', render: (r) => formatDate(r.deactivated_on) },
  ]

  return (
    <div className={'screen' + (list.selected ? ' with-detail' : '')}>
      <div className="toolbar">
        <h1>Suppliers</h1>
        <button type="button" onClick={reference.reload}>
          Reload suppliers
        </button>
      </div>

      {reference.suppliersTruncated && (
        <p className="state state-error">
          More suppliers exist than the 200 loaded: names, filters and search below are incomplete.
        </p>
      )}

      <label className="search">
        Search{' '}
        <input
          type="search"
          name="q"
          placeholder="Name, legal name, ID or VAT number"
          value={search}
          onChange={(event) => {
            setSearch(event.target.value)
            // Local and instant; `replace` keeps typing out of the back-button history.
            list.update({ filters: { ...list.filters, q: event.target.value }, page: 1 }, { replace: true })
          }}
        />
      </label>

      <FilterForm
        fields={fields}
        values={list.filters}
        onApply={(draft) => list.applyFilters({ ...draft, q: list.filters.q })}
        onReset={list.resetFilters}
      />

      <p className="hint">
        {formatCount(matches.length)} of {formatCount(reference.suppliers.length)} loaded suppliers ·
        filtered in the browser.
      </p>

      {view.total === 0 ? (
        <Empty>No supplier matches this search.</Empty>
      ) : (
        <DataTable
          columns={columns}
          rows={view.rows}
          rowKey="supplier_id"
          selectedKey={list.selected}
          onRowClick={list.select}
        />
      )}
      <Pager
        page={view.page}
        pageSize={list.pageSize}
        total={view.total}
        totalPages={view.totalPages}
        onPage={list.setPage}
        onPageSize={list.setPageSize}
      />

      {list.selected && <SupplierDetail id={list.selected} onClose={list.close} />}
    </div>
  )
}

function SupplierDetail({ id, onClose }) {
  const result = useFetch(async (signal) => {
    const options = { signal }
    const [supplier, invoices, pending, unpaid, payments, failed] = await Promise.all([
      fetchSupplier(id, options),
      countInvoices({ supplier_id: id }, options),
      countInvoices({ supplier_id: id, invoice_approval_status: 'Pending Approval' }, options),
      countInvoices({ supplier_id: id, invoice_approval_status: 'Approved', invoice_payment_status: 'Unpaid' }, options),
      countPayments({ supplier_id: id }, options),
      countPayments({ supplier_id: id, payment_status: 'Failed' }, options),
    ])
    return { supplier, counts: { invoices, pending, unpaid, payments, failed } }
  }, [id])

  return (
    <DetailPanel title={`Supplier ${id}`} onClose={onClose}>
      <Remote result={result}>
        {({ supplier, counts }) => (
          <>
            <Fields
              items={[
                ['Name', supplier.supplier_name],
                ['Legal name', supplier.supplier_legal_name],
                ['Category', supplier.supplier_category],
                ['Country', supplier.country_code],
                ['VAT number', supplier.vat_number],
                ['IBAN (masked)', supplier.iban_masked],
                ['Default currency', supplier.default_currency],
                ['Payment terms', `${supplier.payment_terms_days} days`],
                ['Status', supplier.supplier_status],
                ['Onboarded', formatDate(supplier.onboarded_on)],
                ['Deactivated', formatDate(supplier.deactivated_on)],
                ['Updated', formatDateTime(supplier.updated_at)],
              ]}
            />

            <h3>Activity</h3>
            <DataTable
              rowKey="label"
              rows={[
                { label: 'Invoices', count: counts.invoices, params: { supplier_id: id }, screen: 'invoices' },
                { label: 'Pending approval', count: counts.pending, params: { supplier_id: id, invoice_approval_status: 'Pending Approval' }, screen: 'invoices' },
                { label: 'Approved and unpaid', count: counts.unpaid, params: { supplier_id: id, invoice_approval_status: 'Approved', invoice_payment_status: 'Unpaid' }, screen: 'invoices' },
                { label: 'Payments', count: counts.payments, params: { supplier_id: id }, screen: 'payments' },
                { label: 'Failed payments', count: counts.failed, params: { supplier_id: id, payment_status: 'Failed' }, screen: 'payments' },
              ]}
              columns={[
                { key: 'label', header: 'Items' },
                { key: 'count', header: 'Count', numeric: true, render: (r) => formatCount(r.count) },
                { key: 'open', header: '', render: (r) => <Link screen={r.screen} params={r.params}>Open list</Link> },
              ]}
            />
            <p className="hint">Counts only: the API exposes no per-supplier amount or balance.</p>
          </>
        )}
      </Remote>
    </DetailPanel>
  )
}

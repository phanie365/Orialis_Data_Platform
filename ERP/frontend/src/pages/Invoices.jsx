import { useState } from 'react'

import { fetchInvoice, fetchInvoiceAllocations, fetchInvoices } from '../api.js'
import {
  DataTable, DetailPanel, Empty, Fields, FilterForm, Link, ListState, Pager, Remote,
} from '../components.jsx'
import { formatAmount, formatDate, formatDateTime, formatPercent, orEmpty } from '../format.js'
import { useFetch, useListState, useReference } from '../hooks.js'
import { costCenterLabel, isIsoDate, overdueFilters, supplierName } from '../lib.js'

const FILTERS = [
  'invoice_approval_status', 'invoice_payment_status', 'supplier_id', 'cost_center_id',
  'invoice_type', 'currency', 'invoice_date_from', 'invoice_date_to', 'due_before',
  'min_gross_amount_eur',
]

export function Invoices({ params, navigate }) {
  const reference = useReference()
  const list = useListState('invoices', params, navigate, FILTERS)
  const query = { ...list.filters, page: list.page, page_size: list.pageSize }
  const result = useFetch((signal) => fetchInvoices(query, { signal }), [JSON.stringify(query)])
  const [asOf, setAsOf] = useState(list.filters.due_before ?? '')

  const fields = [
    { name: 'invoice_approval_status', label: 'Approval status', type: 'select', options: reference.filters.invoice_approval_status },
    { name: 'invoice_payment_status', label: 'Payment status', type: 'select', options: reference.filters.invoice_payment_status },
    {
      name: 'supplier_id', label: 'Supplier', type: 'select',
      options: reference.suppliers.map((s) => ({ value: s.supplier_id, label: `${s.supplier_name} (${s.supplier_id})` })),
    },
    {
      name: 'cost_center_id', label: 'Cost centre', type: 'select',
      options: reference.costCenters.map((c) => ({ value: c.cost_center_id, label: `${c.cost_center_id} · ${c.cost_center_name}` })),
    },
    { name: 'invoice_type', label: 'Type', type: 'select', options: reference.filters.invoice_type },
    { name: 'currency', label: 'Currency', type: 'select', options: reference.filters.currency },
    { name: 'invoice_date_from', label: 'Invoice date from', type: 'date' },
    { name: 'invoice_date_to', label: 'Invoice date to', type: 'date' },
    { name: 'due_before', label: 'Due before (excl.)', type: 'date' },
    { name: 'min_gross_amount_eur', label: 'Min gross, EUR', type: 'number' },
  ]

  const columns = [
    { key: 'invoice_id', header: 'Invoice' },
    { key: 'supplier_id', header: 'Supplier', render: (r) => supplierName(reference.suppliersById, r.supplier_id) },
    { key: 'supplier_invoice_number', header: 'Supplier no.' },
    { key: 'cost_center_id', header: 'Cost centre' },
    { key: 'invoice_type', header: 'Type' },
    { key: 'invoice_date', header: 'Invoice date', render: (r) => formatDate(r.invoice_date) },
    { key: 'due_date', header: 'Due', render: (r) => formatDate(r.due_date) },
    { key: 'gross_amount', header: 'Gross', numeric: true, render: (r) => formatAmount(r.gross_amount, r.currency_code) },
    { key: 'gross_amount_eur', header: 'Gross, EUR', numeric: true, render: (r) => formatAmount(r.gross_amount_eur, 'EUR') },
    { key: 'paid_amount', header: 'Paid', numeric: true, render: (r) => formatAmount(r.paid_amount, r.currency_code) },
    { key: 'invoice_approval_status', header: 'Approval' },
    { key: 'invoice_payment_status', header: 'Payment' },
  ]

  const page = result.data

  return (
    <div className={'screen' + (list.selected ? ' with-detail' : '')}>
      <h1>Supplier invoices</h1>
      <FilterForm fields={fields} values={list.filters} onApply={list.applyFilters} onReset={list.resetFilters} />

      <form
        className="preset"
        onSubmit={(event) => {
          event.preventDefault()
          const overdue = overdueFilters(asOf)
          if (overdue) list.applyFilters({ ...list.filters, ...overdue })
        }}
      >
        <label className="inline">
          Overdue as of{' '}
          <input type="date" name="overdue_as_of" value={asOf} onChange={(event) => setAsOf(event.target.value)} />
        </label>
        <button type="submit" disabled={!isIsoDate(asOf)}>
          Show overdue
        </button>
        <span className="hint">
          Approved, Unpaid, due strictly before the ERP business date you enter (not the browser date).
        </span>
      </form>

      <p className="hint">Sorted by invoice ID, ascending (API order): page 1 holds the oldest invoices.</p>

      <ListState result={result} count={page?.data.length} empty="No invoice matches these filters.">
        {page && (
          <DataTable
            columns={columns}
            rows={page.data}
            rowKey="invoice_id"
            selectedKey={list.selected}
            onRowClick={list.select}
            dimmed={result.loading}
          />
        )}
      </ListState>
      {page && (
        <Pager
          page={page.pagination.page}
          pageSize={page.pagination.page_size}
          total={page.pagination.total_records}
          totalPages={page.pagination.total_pages}
          onPage={list.setPage}
          onPageSize={list.setPageSize}
        />
      )}

      {list.selected && <InvoiceDetail id={list.selected} onClose={list.close} />}
    </div>
  )
}

function InvoiceDetail({ id, onClose }) {
  const reference = useReference()
  const result = useFetch(
    (signal) => Promise.all([fetchInvoice(id, { signal }), fetchInvoiceAllocations(id, { signal })]),
    [id],
  )

  return (
    <DetailPanel title={`Invoice ${id}`} onClose={onClose}>
      <Remote result={result}>
        {([invoice, allocations]) => (
          <>
            <Fields
              items={[
                ['Supplier', (
                  <Link screen="suppliers" params={{ selected: invoice.supplier_id }}>
                    {supplierName(reference.suppliersById, invoice.supplier_id)} ({invoice.supplier_id})
                  </Link>
                )],
                ['Supplier invoice no.', invoice.supplier_invoice_number],
                ['Cost centre', costCenterLabel(reference.costCentersById, invoice.cost_center_id)],
                ['Type', invoice.invoice_type],
                ['Invoice date', formatDate(invoice.invoice_date)],
                ['Received', formatDate(invoice.received_date)],
                ['Due', formatDate(invoice.due_date)],
                ['Net', formatAmount(invoice.net_amount, invoice.currency_code)],
                ['Tax treatment', invoice.tax_treatment],
                ['Tax rate', formatPercent(invoice.tax_rate)],
                ['Tax', formatAmount(invoice.tax_amount, invoice.currency_code)],
                ['Gross', formatAmount(invoice.gross_amount, invoice.currency_code)],
                ['FX rate to EUR', orEmpty(invoice.fx_rate_to_eur)],
                ['Gross, EUR', formatAmount(invoice.gross_amount_eur, 'EUR')],
                ['Paid', formatAmount(invoice.paid_amount, invoice.currency_code)],
                ['Approval status', invoice.invoice_approval_status],
                ['Approved by', invoice.approved_by_employee_ref],
                ['Approved at', formatDateTime(invoice.approved_at)],
                ['Rejection reason', invoice.rejection_reason],
                ['Payment status', invoice.invoice_payment_status],
                ['Created', formatDateTime(invoice.created_at)],
                ['Updated', formatDateTime(invoice.updated_at)],
              ]}
            />
            <p className="links">
              <Link screen="invoices" params={{ supplier_id: invoice.supplier_id }}>All invoices of this supplier</Link>
              {' · '}
              <Link screen="payments" params={{ supplier_id: invoice.supplier_id }}>Payments to this supplier</Link>
            </p>

            <h3>Payment allocations ({allocations.data.length})</h3>
            {allocations.data.length === 0 ? (
              <Empty>Not matched to any payment.</Empty>
            ) : (
              <DataTable
                rowKey="allocation_id"
                rows={allocations.data}
                columns={[
                  {
                    key: 'payment_id', header: 'Payment',
                    render: (a) => <Link screen="payments" params={{ selected: a.payment_id }}>{a.payment_id}</Link>,
                  },
                  { key: 'payment_date', header: 'Date', render: (a) => formatDate(a.payment_date) },
                  { key: 'payment_status', header: 'Payment status' },
                  { key: 'allocated_amount', header: 'Allocated', numeric: true, render: (a) => formatAmount(a.allocated_amount, a.currency_code) },
                  {
                    key: 'allocation_status', header: 'Allocation',
                    render: (a) => (a.cancellation_reason ? `${a.allocation_status} (${a.cancellation_reason})` : a.allocation_status),
                  },
                ]}
              />
            )}
            <p className="hint">Only Active allocations of Executed payments count towards the paid amount.</p>
          </>
        )}
      </Remote>
    </DetailPanel>
  )
}

import { fetchPayment, fetchPaymentAllocations, fetchPayments } from '../api.js'
import {
  DataTable, DetailPanel, Empty, Fields, FilterForm, Link, ListState, Pager, Remote,
} from '../components.jsx'
import { formatAmount, formatDate, formatDateTime } from '../format.js'
import { useFetch, useListState, useReference } from '../hooks.js'
import { supplierName } from '../lib.js'

const FILTERS = [
  'payment_status', 'payment_method', 'supplier_id', 'currency',
  'payment_date_from', 'payment_date_to', 'min_amount',
]

export function Payments({ params, navigate }) {
  const reference = useReference()
  const list = useListState('payments', params, navigate, FILTERS)
  const query = { ...list.filters, page: list.page, page_size: list.pageSize }
  const result = useFetch((signal) => fetchPayments(query, { signal }), [JSON.stringify(query)])

  const fields = [
    { name: 'payment_status', label: 'Status', type: 'select', options: reference.filters.payment_status },
    { name: 'payment_method', label: 'Method', type: 'select', options: reference.filters.payment_method },
    {
      name: 'supplier_id', label: 'Supplier', type: 'select',
      options: reference.suppliers.map((s) => ({ value: s.supplier_id, label: `${s.supplier_name} (${s.supplier_id})` })),
    },
    { name: 'currency', label: 'Currency', type: 'select', options: reference.filters.currency },
    { name: 'payment_date_from', label: 'Payment date from', type: 'date' },
    { name: 'payment_date_to', label: 'Payment date to', type: 'date' },
    { name: 'min_amount', label: 'Min amount (payment currency)', type: 'number' },
  ]

  const columns = [
    { key: 'payment_id', header: 'Payment' },
    { key: 'supplier_id', header: 'Supplier', render: (r) => supplierName(reference.suppliersById, r.supplier_id) },
    { key: 'payment_date', header: 'Payment date', render: (r) => formatDate(r.payment_date) },
    { key: 'value_date', header: 'Value date', render: (r) => formatDate(r.value_date) },
    { key: 'payment_method', header: 'Method' },
    { key: 'payment_amount', header: 'Amount', numeric: true, render: (r) => formatAmount(r.payment_amount, r.currency_code) },
    { key: 'payment_status', header: 'Status' },
    { key: 'bank_reference', header: 'Bank ref.' },
    { key: 'executed_at', header: 'Executed at', render: (r) => formatDateTime(r.executed_at) },
  ]

  const page = result.data
  const preset = (status) => list.applyFilters({ ...list.filters, payment_status: status })

  return (
    <div className={'screen' + (list.selected ? ' with-detail' : '')}>
      <h1>Payments</h1>
      <FilterForm fields={fields} values={list.filters} onApply={list.applyFilters} onReset={list.resetFilters} />

      <div className="preset">
        <span className="hint">Shortcuts:</span>
        <button type="button" onClick={() => preset('Failed')}>Failed</button>
        <button type="button" onClick={() => preset('Initiated')}>Initiated (in flight)</button>
      </div>

      <p className="hint">
        Sorted by payment ID, ascending (API order): page 1 holds the oldest payments. Amounts are in
        each payment's own currency; there is no EUR equivalent on payments.
      </p>

      <ListState result={result} count={page?.data.length} empty="No payment matches these filters.">
        {page && (
          <DataTable
            columns={columns}
            rows={page.data}
            rowKey="payment_id"
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

      {list.selected && <PaymentDetail id={list.selected} onClose={list.close} />}
    </div>
  )
}

function PaymentDetail({ id, onClose }) {
  const reference = useReference()
  const result = useFetch(
    (signal) => Promise.all([fetchPayment(id, { signal }), fetchPaymentAllocations(id, { signal })]),
    [id],
  )

  return (
    <DetailPanel title={`Payment ${id}`} onClose={onClose}>
      <Remote result={result}>
        {([payment, allocations]) => (
          <>
            <Fields
              items={[
                ['Supplier', (
                  <Link screen="suppliers" params={{ selected: payment.supplier_id }}>
                    {supplierName(reference.suppliersById, payment.supplier_id)} ({payment.supplier_id})
                  </Link>
                )],
                ['Payment date', formatDate(payment.payment_date)],
                ['Value date', formatDate(payment.value_date)],
                ['Method', payment.payment_method],
                ['Amount', formatAmount(payment.payment_amount, payment.currency_code)],
                ['Status', payment.payment_status],
                ['Bank reference', payment.bank_reference],
                ['Executed at', formatDateTime(payment.executed_at)],
                ['Failure reason', payment.failure_reason],
                ['Created', formatDateTime(payment.created_at)],
                ['Updated', formatDateTime(payment.updated_at)],
              ]}
            />
            <p className="links">
              <Link screen="payments" params={{ supplier_id: payment.supplier_id }}>All payments to this supplier</Link>
              {' · '}
              <Link screen="invoices" params={{ supplier_id: payment.supplier_id }}>Invoices of this supplier</Link>
            </p>

            <h3>Invoices settled ({allocations.data.length})</h3>
            {allocations.data.length === 0 ? (
              <Empty>This payment settles no invoice.</Empty>
            ) : (
              <DataTable
                rowKey="allocation_id"
                rows={allocations.data}
                columns={[
                  {
                    key: 'invoice_id', header: 'Invoice',
                    render: (a) => <Link screen="invoices" params={{ selected: a.invoice_id }}>{a.invoice_id}</Link>,
                  },
                  { key: 'invoice_type', header: 'Type' },
                  { key: 'due_date', header: 'Due', render: (a) => formatDate(a.due_date) },
                  { key: 'gross_amount', header: 'Invoice gross', numeric: true, render: (a) => formatAmount(a.gross_amount, a.currency_code) },
                  { key: 'allocated_amount', header: 'Allocated', numeric: true, render: (a) => formatAmount(a.allocated_amount, a.currency_code) },
                  {
                    key: 'allocation_status', header: 'Allocation',
                    render: (a) => (a.cancellation_reason ? `${a.allocation_status} (${a.cancellation_reason})` : a.allocation_status),
                  },
                  { key: 'invoice_payment_status', header: 'Invoice now' },
                ]}
              />
            )}
            <p className="hint">A credit note appears as a negative line netted off the transfer.</p>
          </>
        )}
      </Remote>
    </DetailPanel>
  )
}

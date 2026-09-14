import { countInvoices, fetchOverview } from '../api.js'
import { DataTable, ErrorBox, Link, Loading, Remote, Tile } from '../components.jsx'
import { formatAmount, formatCount, formatDate } from '../format.js'
import { useFetch } from '../hooks.js'
import { overdueFilters } from '../lib.js'

export function Dashboard({ params, navigate }) {
  const overview = useFetch((signal) => fetchOverview({ signal }), [])

  // The business date comes from the user, never from the browser clock.
  const asOf = params.asof ?? ''
  const overdueQuery = overdueFilters(asOf)
  const overdue = useFetch(
    overdueQuery ? (signal) => countInvoices(overdueQuery, { signal }) : null,
    [asOf],
  )

  const refresh = () => {
    overview.reload()
    if (overdueQuery) overdue.reload()
  }

  return (
    <div className="screen">
      <div className="toolbar">
        <h1>Dashboard</h1>
        <button type="button" onClick={refresh}>
          Refresh
        </button>
      </div>
      <p className="hint">All figures cover the whole ERP history: the stats endpoint has no period filter.</p>

      <section className="panel overdue">
        <h2>Overdue invoices</h2>
        <label className="inline">
          ERP business date{' '}
          <input
            type="date"
            name="asof"
            value={asOf}
            onChange={(event) => navigate('dashboard', { asof: event.target.value }, { replace: true })}
          />
        </label>
        <OverdueResult asOf={asOf} query={overdueQuery} result={overdue} />
      </section>

      <Remote result={overview}>{(data) => <Overview data={data} />}</Remote>
    </div>
  )
}

function OverdueResult({ asOf, query, result }) {
  if (!query) {
    return (
      <p className="hint">
        Enter the ERP business date to count approved, unpaid invoices due strictly before it. The
        browser date is not used: the ERP runs on simulated time.
      </p>
    )
  }
  if (result.error) return <ErrorBox error={result.error} onRetry={result.reload} />
  if (result.loading || result.data === null) return <Loading />
  return (
    <p className="overdue-result">
      <strong>{formatCount(result.data)}</strong> approved, unpaid invoices due before{' '}
      {formatDate(asOf)} · <Link screen="invoices" params={query}>open the list</Link>
    </p>
  )
}

function Overview({ data }) {
  const totals = data.totals
  return (
    <>
      <div className="tiles">
        <Tile label="Suppliers (active / total)">
          {formatCount(totals.active_suppliers)} / {formatCount(totals.total_suppliers)}
        </Tile>
        <Tile label="Cost centres">{formatCount(totals.total_cost_centers)}</Tile>
        <Tile label="Supplier invoices">{formatCount(totals.total_invoices)}</Tile>
        <Tile label="Payments">{formatCount(totals.total_payments)}</Tile>
        <Tile label="Approved invoices, EUR">{formatAmount(totals.approved_amount_eur, 'EUR')}</Tile>
        <Tile label="Outstanding on approved invoices, EUR">
          {formatAmount(totals.outstanding_amount_eur, 'EUR')}
        </Tile>
      </div>

      <div className="grid">
        <InvoiceBreakdown
          title="Invoices by approval status"
          rows={data.invoices_by_approval_status}
          labelKey="status"
          filter="invoice_approval_status"
        />
        <InvoiceBreakdown
          title="Invoices by payment status"
          rows={data.invoices_by_payment_status}
          labelKey="status"
          filter="invoice_payment_status"
        />
        <InvoiceBreakdown
          title="Invoices by currency"
          rows={data.invoices_by_currency}
          labelKey="currency"
          filter="currency"
        />

        <section className="panel" data-block="payments-by-status">
          <h2>Payments by status</h2>
          <DataTable
            rowKey="status"
            rows={data.payments_by_status}
            columns={[
              {
                key: 'status',
                header: 'Status',
                render: (row) => (
                  <Link screen="payments" params={{ payment_status: row.status }}>
                    {row.status}
                  </Link>
                ),
              },
              {
                key: 'payment_count',
                header: 'Payments',
                numeric: true,
                render: (row) => formatCount(row.payment_count),
              },
            ]}
          />
          <p className="hint">Counts only: payments are in EUR or CHF, and the two are never added together.</p>
        </section>

        <section className="panel" data-block="top-suppliers">
          <h2>Top suppliers by invoice count</h2>
          <DataTable
            rowKey="supplier_id"
            rows={data.top_suppliers}
            columns={[
              {
                key: 'supplier_name',
                header: 'Supplier',
                render: (row) => (
                  <Link screen="suppliers" params={{ selected: row.supplier_id }}>
                    {row.supplier_name}
                  </Link>
                ),
              },
              { key: 'supplier_category', header: 'Category' },
              {
                key: 'invoice_count',
                header: 'Invoices',
                numeric: true,
                render: (row) => (
                  <Link screen="invoices" params={{ supplier_id: row.supplier_id }}>
                    {formatCount(row.invoice_count)}
                  </Link>
                ),
              },
              {
                key: 'amount_eur',
                header: 'Amount, EUR',
                numeric: true,
                render: (row) => formatAmount(row.amount_eur, 'EUR'),
              },
            ]}
          />
          <p className="hint">All invoice statuses included.</p>
        </section>

        <section className="panel" data-block="top-cost-centers">
          <h2>Top cost centres by approved spend</h2>
          <DataTable
            rowKey="cost_center_id"
            rows={data.top_cost_centers}
            columns={[
              {
                key: 'cost_center_id',
                header: 'Cost centre',
                render: (row) => (
                  <Link
                    screen="invoices"
                    params={{ cost_center_id: row.cost_center_id, invoice_approval_status: 'Approved' }}
                  >
                    {row.cost_center_id} · {row.cost_center_name}
                  </Link>
                ),
              },
              { key: 'cost_center_type', header: 'Type' },
              {
                key: 'invoice_count',
                header: 'Invoices',
                numeric: true,
                render: (row) => formatCount(row.invoice_count),
              },
              {
                key: 'amount_eur',
                header: 'Approved, EUR',
                numeric: true,
                render: (row) => formatAmount(row.amount_eur, 'EUR'),
              },
            ]}
          />
        </section>
      </div>
    </>
  )
}

function InvoiceBreakdown({ title, rows, labelKey, filter }) {
  return (
    <section className="panel" data-block={filter}>
      <h2>{title}</h2>
      <DataTable
        rowKey={labelKey}
        rows={rows}
        columns={[
          {
            key: labelKey,
            header: labelKey === 'currency' ? 'Currency' : 'Status',
            render: (row) => (
              <Link screen="invoices" params={{ [filter]: row[labelKey] }}>
                {row[labelKey]}
              </Link>
            ),
          },
          {
            key: 'invoice_count',
            header: 'Invoices',
            numeric: true,
            render: (row) => formatCount(row.invoice_count),
          },
          {
            key: 'amount_eur',
            header: 'Amount, EUR',
            numeric: true,
            render: (row) => formatAmount(row.amount_eur, 'EUR'),
          },
        ]}
      />
    </section>
  )
}

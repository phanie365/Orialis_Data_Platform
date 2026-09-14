import { Fragment, useEffect, useState } from 'react'

import { EMPTY, formatCount } from './format.js'
import { buildHash, PAGE_SIZES } from './lib.js'

const isBlank = (value) => value === null || value === undefined || value === ''

/** A plain anchor to a hash route. Clicking it inside a clickable row does not select the row. */
export function Link({ screen, params, children }) {
  return (
    <a href={buildHash(screen, params)} onClick={(event) => event.stopPropagation()}>
      {children}
    </a>
  )
}

// ---------------------------------------------------------------------------
// Loading, empty and error states
// ---------------------------------------------------------------------------

export function Loading({ children = 'Loading…' }) {
  return <p className="state state-loading">{children}</p>
}

export function Empty({ children }) {
  return <p className="state state-empty">{children}</p>
}

export function ErrorBox({ error, onRetry }) {
  return (
    <div className="state state-error" role="alert">
      <strong>Error.</strong> {error?.message || String(error)}
      {onRetry && (
        <button type="button" onClick={onRetry}>
          Retry
        </button>
      )}
    </div>
  )
}

/** Error, then loading, then the content - for a detail or a dashboard block. */
export function Remote({ result, children }) {
  if (result.error) return <ErrorBox error={result.error} onRetry={result.reload} />
  if (result.loading || result.data === null) return <Loading />
  return children(result.data)
}

/** Same for a list: a list already on screen stays visible while it refreshes. */
export function ListState({ result, count, empty, children }) {
  if (result.error) return <ErrorBox error={result.error} onRetry={result.reload} />
  if (result.data === null) return <Loading />
  return (
    <>
      {result.loading && <Loading>Refreshing…</Loading>}
      {count === 0 ? <Empty>{empty}</Empty> : children}
    </>
  )
}

// ---------------------------------------------------------------------------
// Tables, pagination, filters
// ---------------------------------------------------------------------------

export function DataTable({ columns, rows, rowKey, selectedKey, onRowClick, dimmed }) {
  return (
    <div className={'table-wrap' + (dimmed ? ' dimmed' : '')}>
      <table>
        <thead>
          <tr>
            {columns.map((column) => (
              <th key={column.key} className={column.numeric ? 'num' : undefined}>
                {column.header}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => {
            const key = row[rowKey]
            const clickable = Boolean(onRowClick)
            return (
              <tr
                key={key}
                data-key={key}
                className={[clickable && 'clickable', key === selectedKey && 'selected']
                  .filter(Boolean)
                  .join(' ')}
                tabIndex={clickable ? 0 : undefined}
                onClick={clickable ? () => onRowClick(key) : undefined}
                onKeyDown={
                  clickable ? (event) => event.key === 'Enter' && onRowClick(key) : undefined
                }
              >
                {columns.map((column) => {
                  const value = column.render ? column.render(row) : row[column.key]
                  return (
                    <td key={column.key} className={column.numeric ? 'num' : undefined}>
                      {isBlank(value) ? EMPTY : value}
                    </td>
                  )
                })}
              </tr>
            )
          })}
        </tbody>
      </table>
    </div>
  )
}

export function Pager({ page, pageSize, total, totalPages, onPage, onPageSize }) {
  const first = total === 0 ? 0 : (page - 1) * pageSize + 1
  const last = Math.min(page * pageSize, total)
  return (
    <div className="pager">
      <span className="pager-range">
        {total === 0 ? 'No records' : `${formatCount(first)}–${formatCount(last)} of ${formatCount(total)}`}
      </span>
      <button type="button" disabled={page <= 1} onClick={() => onPage(1)}>
        « First
      </button>
      <button type="button" disabled={page <= 1} onClick={() => onPage(page - 1)}>
        ‹ Prev
      </button>
      <span className="pager-page">
        Page {formatCount(page)} / {formatCount(Math.max(totalPages, 1))}
      </span>
      <button type="button" disabled={page >= totalPages} onClick={() => onPage(page + 1)}>
        Next ›
      </button>
      <button type="button" disabled={page >= totalPages} onClick={() => onPage(totalPages)}>
        Last »
      </button>
      <label className="inline">
        Rows{' '}
        <select value={pageSize} onChange={(event) => onPageSize(Number(event.target.value))}>
          {PAGE_SIZES.map((size) => (
            <option key={size} value={size}>
              {size}
            </option>
          ))}
        </select>
      </label>
    </div>
  )
}

/**
 * Filters are edited in a draft and sent with "Apply" (or Enter), so typing an
 * amount does not fire one request per keystroke.
 */
export function FilterForm({ fields, values, onApply, onReset }) {
  const [draft, setDraft] = useState(values)
  const valuesKey = JSON.stringify(values)
  // Follow the hash: a link from another screen replaces the filters.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  useEffect(() => setDraft(values), [valuesKey])

  const active = Object.values(values).some((value) => !isBlank(value))
  const set = (name, value) => setDraft((current) => ({ ...current, [name]: value }))

  return (
    <form
      className="filters"
      onSubmit={(event) => {
        event.preventDefault()
        onApply(draft)
      }}
    >
      {fields.map((field) => (
        <label key={field.name}>
          <span>{field.label}</span>
          {field.type === 'select' ? (
            <select
              name={field.name}
              value={draft[field.name] ?? ''}
              onChange={(event) => set(field.name, event.target.value)}
            >
              <option value="">All</option>
              {field.options.map((option) => {
                const { value, label } =
                  typeof option === 'object' ? option : { value: option, label: String(option) }
                return (
                  <option key={value} value={String(value)}>
                    {label}
                  </option>
                )
              })}
            </select>
          ) : (
            <input
              name={field.name}
              type={field.type}
              step={field.type === 'number' ? 'any' : undefined}
              placeholder={field.placeholder}
              value={draft[field.name] ?? ''}
              onChange={(event) => set(field.name, event.target.value)}
            />
          )}
        </label>
      ))}
      <div className="filter-actions">
        <button type="submit">Apply</button>
        <button type="button" onClick={onReset} disabled={!active}>
          Reset
        </button>
      </div>
    </form>
  )
}

// ---------------------------------------------------------------------------
// Detail panel and fields
// ---------------------------------------------------------------------------

export function DetailPanel({ title, onClose, children }) {
  useEffect(() => {
    const onKey = (event) => event.key === 'Escape' && onClose()
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])

  return (
    <aside className="detail" aria-label={title}>
      <header>
        <h2>{title}</h2>
        <button type="button" onClick={onClose} aria-label="Close detail">
          ×
        </button>
      </header>
      <div className="detail-body">{children}</div>
    </aside>
  )
}

/** `[[label, value], ...]` as a compact two-column list. */
export function Fields({ items }) {
  return (
    <dl className="fields">
      {items.map(([label, value]) => (
        <Fragment key={label}>
          <dt>{label}</dt>
          <dd>{isBlank(value) ? EMPTY : value}</dd>
        </Fragment>
      ))}
    </dl>
  )
}

export function Tile({ label, children }) {
  return (
    <div className="tile">
      <div className="tile-label">{label}</div>
      <div className="tile-value">{children}</div>
    </div>
  )
}

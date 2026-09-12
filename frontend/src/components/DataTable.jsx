/**
 * The shared table for the list pages.
 *
 * Holds the four states every list has to handle honestly - loading, error,
 * empty, populated - so that Clients, Advisors and Interactions do not each
 * reimplement them slightly differently.
 *
 * The loading state is a skeleton of the right shape rather than a spinner:
 * the table does not jump when the rows arrive, and the page keeps its
 * structure while it waits.
 */

import './DataTable.css'

function Skeleton({ columns, rows = 8 }) {
  return (
    <tbody>
      {Array.from({ length: rows }, (_, rowIndex) => (
        <tr key={rowIndex}>
          {columns.map((column, columnIndex) => (
            <td key={column.key}>
              <span
                className="skeleton datatable__skeleton"
                // Varying widths so the placeholder reads as text rather
                // than as a set of identical grey slabs.
                style={{ width: `${[68, 54, 80, 46, 62, 72][columnIndex % 6]}%` }}
              />
            </td>
          ))}
        </tr>
      ))}
    </tbody>
  )
}

export function DataTable({
  columns,
  rows,
  rowKey,
  loading,
  error,
  emptyMessage = 'No records match these filters.',
  onRowClick,
}) {
  const columnCount = columns.length

  return (
    <div className="datatable">
      <table className="datatable__table">
        <thead>
          <tr>
            {columns.map((column) => (
              <th
                key={column.key}
                className={'label' + (column.align === 'right' ? ' datatable__right' : '')}
                style={column.width ? { width: column.width } : undefined}
              >
                {column.header}
              </th>
            ))}
          </tr>
        </thead>

        {loading ? (
          <Skeleton columns={columns} />
        ) : (
          <tbody>
            {error && (
              <tr>
                <td colSpan={columnCount}>
                  <div className="state state--error">
                    {error.message}
                    {error.status === 401 && (
                      <div className="state__detail">
                        Set CRM_API_KEY in frontend/.env, then restart the dev server.
                      </div>
                    )}
                  </div>
                </td>
              </tr>
            )}

            {!error && rows.length === 0 && (
              <tr>
                <td colSpan={columnCount}>
                  <div className="state">{emptyMessage}</div>
                </td>
              </tr>
            )}

            {!error &&
              rows.map((row) => (
                <tr
                  key={rowKey(row)}
                  className={onRowClick ? 'datatable__row--clickable' : undefined}
                  onClick={onRowClick ? () => onRowClick(row) : undefined}
                  // A clickable row must also be reachable from the keyboard.
                  // Without this the whole list would be unusable without a
                  // mouse, which is not an acceptable trade for a table.
                  tabIndex={onRowClick ? 0 : undefined}
                  role={onRowClick ? 'link' : undefined}
                  onKeyDown={
                    onRowClick
                      ? (event) => {
                          if (event.key === 'Enter' || event.key === ' ') {
                            event.preventDefault()
                            onRowClick(row)
                          }
                        }
                      : undefined
                  }
                >
                  {columns.map((column) => (
                    <td
                      key={column.key}
                      className={column.align === 'right' ? 'datatable__right' : undefined}
                    >
                      {column.render(row)}
                    </td>
                  ))}
                </tr>
              ))}
          </tbody>
        )}
      </table>
    </div>
  )
}

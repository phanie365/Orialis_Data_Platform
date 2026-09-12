/**
 * Server-side pagination controls.
 *
 * Every figure shown here comes from the API's `pagination` envelope - page,
 * page_size, total_records, total_pages. Nothing is inferred from the length
 * of the rows on screen, which would silently disagree with the API on the
 * last page.
 *
 * The page window is bounded: with 5,000 clients at 25 per page there are 200
 * pages, and rendering 200 buttons would be its own kind of failure. Five
 * numbers around the current page, with first and last always reachable.
 */

import './Pagination.css'

function pageWindow(current, total, span = 2) {
  const pages = []
  const from = Math.max(1, current - span)
  const to = Math.min(total, current + span)

  if (from > 1) {
    pages.push(1)
    if (from > 2) pages.push('gap-start')
  }
  for (let page = from; page <= to; page += 1) pages.push(page)
  if (to < total) {
    if (to < total - 1) pages.push('gap-end')
    pages.push(total)
  }
  return pages
}

export function Pagination({ pagination, onPageChange, onPageSizeChange, pageSizes = [25, 50, 100] }) {
  if (!pagination) return null

  const { page, page_size: pageSize, total_records: total, total_pages: totalPages } = pagination

  // An empty result set is a real state, not a broken one: show the range
  // honestly rather than "1-0 of 0".
  const first = total === 0 ? 0 : (page - 1) * pageSize + 1
  const last = Math.min(page * pageSize, total)

  return (
    <div className="pager">
      <div className="pager__range tnum">
        {total === 0 ? (
          'No records'
        ) : (
          <>
            {first.toLocaleString('en-GB')}–{last.toLocaleString('en-GB')}
            <span className="pager__of">of</span>
            {total.toLocaleString('en-GB')}
          </>
        )}
      </div>

      <div className="pager__controls">
        <button
          type="button"
          className="pager__step"
          disabled={page <= 1}
          onClick={() => onPageChange(page - 1)}
        >
          Previous
        </button>

        <div className="pager__pages">
          {totalPages > 0 &&
            pageWindow(page, totalPages).map((entry) =>
              typeof entry === 'number' ? (
                <button
                  type="button"
                  key={entry}
                  className={
                    'pager__page tnum' + (entry === page ? ' pager__page--current' : '')
                  }
                  aria-current={entry === page ? 'page' : undefined}
                  onClick={() => onPageChange(entry)}
                >
                  {entry}
                </button>
              ) : (
                <span className="pager__gap" key={entry}>
                  ·
                </span>
              ),
            )}
        </div>

        <button
          type="button"
          className="pager__step"
          disabled={page >= totalPages}
          onClick={() => onPageChange(page + 1)}
        >
          Next
        </button>

        {onPageSizeChange && (
          <label className="pager__size">
            <span className="label">Rows</span>
            <select
              value={pageSize}
              onChange={(event) => onPageSizeChange(Number(event.target.value))}
            >
              {pageSizes.map((size) => (
                <option value={size} key={size}>
                  {size}
                </option>
              ))}
            </select>
          </label>
        )}
      </div>
    </div>
  )
}

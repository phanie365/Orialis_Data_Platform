/**
 * The filter row above a list.
 *
 * Every option comes from `/api/v1/stats/filters`, so no business value is
 * written into this file. A filter whose options have not loaded yet renders
 * disabled rather than empty - an enabled picker with nothing in it looks
 * broken.
 *
 * Two kinds of control, both native: a `<select>` for a closed set of values,
 * and an `<input>` for a free identifier or a date. Native elements are not a
 * compromise here - they are keyboard-accessible, they work on touch, and
 * they carry no dependency.
 */

import './FilterBar.css'

export function FilterBar({ filters, values, onChange, onReset, resultCount, loading }) {
  const hasActiveFilter = filters.some((filter) => values[filter.name])

  return (
    <div className="filters">
      <div className="filters__controls">
        {filters.map((filter) => (
          <label className="filters__field" key={filter.name}>
            <span className="filters__label label">{filter.label}</span>

            {filter.type === 'select' ? (
              <select
                className="filters__input"
                value={values[filter.name] ?? ''}
                disabled={!filter.options || filter.options.length === 0}
                onChange={(event) => onChange(filter.name, event.target.value)}
              >
                <option value="">
                  {filter.options?.length ? 'All' : 'Loading…'}
                </option>
                {(filter.options ?? []).map((option) => (
                  <option value={option} key={option}>
                    {option}
                  </option>
                ))}
              </select>
            ) : (
              <input
                className="filters__input"
                type={filter.type === 'date' ? 'date' : 'text'}
                value={values[filter.name] ?? ''}
                placeholder={filter.placeholder}
                onChange={(event) => onChange(filter.name, event.target.value)}
              />
            )}
          </label>
        ))}
      </div>

      <div className="filters__summary">
        {!loading && resultCount !== null && (
          resultCount === 0 ? (
            // Not "0". Cormorant Garamond's zero is a very narrow oval, and
            // set alone at this size it reads as a pair of parentheses - on
            // precisely the screen where the figure matters most. Words are
            // unambiguous where that glyph is not.
            <span className="filters__none">No matching records</span>
          ) : (
            <span className="filters__count tnum">
              {resultCount.toLocaleString('en-GB')}
              <span className="filters__countUnit">
                {resultCount === 1 ? 'record' : 'records'}
              </span>
            </span>
          )
        )}
        {hasActiveFilter && (
          <button type="button" className="filters__reset" onClick={onReset}>
            Clear filters
          </button>
        )}
      </div>
    </div>
  )
}

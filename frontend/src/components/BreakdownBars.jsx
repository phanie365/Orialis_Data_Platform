/**
 * A distribution rendered as horizontal bars.
 *
 * Used by Clients by Segment, Clients by Country and Client Risk Overview -
 * three panels with the same shape of data, so they share one component
 * rather than three near-identical ones.
 *
 * Deliberately no chart library. Each bar is a div with a percentage width;
 * a 100 kB dependency to draw a rectangle would contradict both the brief
 * and the aesthetic. It also means the bars inherit the design tokens
 * directly instead of being themed through a library's own abstraction.
 *
 * Colours come from the single-family ramp: forest, then progressively
 * lighter. Categorical colours are avoided on purpose - one segment is not a
 * different kind of thing from another, it is more or less of the same
 * thing, and the ramp says that.
 */

import './BreakdownBars.css'

const RAMP = ['var(--series-1)', 'var(--series-2)', 'var(--series-3)', 'var(--series-4)']

export function BreakdownBars({ rows, labelKey, total }) {
  // Bars are scaled against the LARGEST value, not against the total. With a
  // 3,250 / 1,350 / 400 split, scaling against the total would leave the
  // smallest bar a 8% sliver and the panel would read as empty. The share of
  // the total is still shown, as a number, on the right.
  const largest = Math.max(...rows.map((row) => row.client_count), 1)

  return (
    <ul className="bars">
      {rows.map((row, index) => {
        const value = row.client_count
        const share = total ? (value / total) * 100 : 0

        return (
          <li className="bars__row" key={row[labelKey]}>
            <div className="bars__head">
              <span className="bars__label">{row[labelKey]}</span>
              <span className="bars__figures">
                <span className="bars__value tnum">
                  {value.toLocaleString('en-GB')}
                </span>
                <span className="bars__share tnum">{share.toFixed(1)}%</span>
              </span>
            </div>

            <div
              className="bars__track"
              role="img"
              aria-label={`${row[labelKey]}: ${value} clients, ${share.toFixed(1)} percent`}
            >
              <div
                className="bars__fill"
                style={{
                  width: `${(value / largest) * 100}%`,
                  background: RAMP[index % RAMP.length],
                }}
              />
            </div>
          </li>
        )
      })}
    </ul>
  )
}

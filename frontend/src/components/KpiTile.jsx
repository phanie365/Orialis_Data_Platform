/**
 * One headline figure.
 *
 * The number is the element: Cormorant at 42px, forest green, tabular
 * figures so the four tiles align on their digits. The caption above it is
 * the small letterspaced capital used throughout the moodboard, and a short
 * line of context sits underneath - the tile says what the number is, not
 * just how big it is.
 *
 * No icon, no percentage badge, no sparkline. The CRM holds no history, so
 * a trend indicator would be invented data.
 */

import './KpiTile.css'

export function KpiTile({ label, value, context, loading }) {
  return (
    <div className="kpi">
      <div className="kpi__label label">{label}</div>

      {loading ? (
        <div className="skeleton kpi__skeleton" />
      ) : (
        <div className="kpi__value tnum">{value.toLocaleString('en-GB')}</div>
      )}

      {context && <div className="kpi__context">{context}</div>}
    </div>
  )
}

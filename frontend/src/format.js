/**
 * Shared formatting.
 *
 * These were duplicated across the dashboard feed, the interaction list and
 * the client history - three copies of the same rule, free to drift apart.
 * Worse, the client detail page had started importing them from a PAGE,
 * which inverts the dependency: a page may use a helper, a helper must not
 * belong to a page.
 */

/** "12 Sep 2026, 18:59" - a precise moment, in a European reading order. */
export function formatTimestamp(value) {
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return value
  return date.toLocaleString('en-GB', {
    day: 'numeric',
    month: 'short',
    year: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  })
}

/** "12 March 2024" - a date, where the time carries no meaning. */
export function formatDate(value, { long = false } = {}) {
  if (!value) return '—'
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return value
  return date.toLocaleDateString('en-GB', {
    day: 'numeric',
    month: long ? 'long' : 'short',
    year: 'numeric',
  })
}

/** A whole number, grouped. */
export function formatCount(value) {
  return Number(value ?? 0).toLocaleString('en-GB')
}

/**
 * Map a free-text outcome onto one of the three muted status tones.
 *
 * `outcome` is free text in the CRM, so this reads it rather than switching
 * on a closed set. An unrecognised outcome falls through to the neutral
 * tone instead of losing its dot.
 */
export function outcomeTone(outcome) {
  const text = (outcome || '').toLowerCase()
  if (text.includes('follow') || text.includes('action required')) return 'pending'
  if (text.includes('no action')) return 'neutral'
  return 'active'
}

/**
 * Display formatting. Every amount is shown WITH its currency, and no function
 * here adds amounts together: EUR and CHF are never mixed on screen.
 */

export const EMPTY = '—'

const amountFormat = new Intl.NumberFormat('en-GB', {
  minimumFractionDigits: 2,
  maximumFractionDigits: 2,
})
const countFormat = new Intl.NumberFormat('en-GB')

const isBlank = (value) => value === null || value === undefined || value === ''

export function formatAmount(value, currency) {
  if (isBlank(value)) return EMPTY
  const number = Number(value)
  if (!Number.isFinite(number)) return EMPTY
  return currency ? `${amountFormat.format(number)} ${currency}` : amountFormat.format(number)
}

export function formatCount(value) {
  return isBlank(value) ? EMPTY : countFormat.format(value)
}

/** `2026-10-01` -> `01/10/2026`, by string, so no timezone can shift the day. */
export function formatDate(value) {
  if (isBlank(value)) return EMPTY
  const match = /^(\d{4})-(\d{2})-(\d{2})/.exec(String(value))
  return match ? `${match[3]}/${match[2]}/${match[1]}` : String(value)
}

/** Instants are stored in UTC and shown in UTC, labelled as such. */
export function formatDateTime(value) {
  if (isBlank(value)) return EMPTY
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return String(value)
  const pad = (number) => String(number).padStart(2, '0')
  return (
    `${pad(date.getUTCDate())}/${pad(date.getUTCMonth() + 1)}/${date.getUTCFullYear()} ` +
    `${pad(date.getUTCHours())}:${pad(date.getUTCMinutes())} UTC`
  )
}

/** Tax rates are stored as percentages (10.00 means 10 %). */
export function formatPercent(value) {
  return isBlank(value) ? EMPTY : `${amountFormat.format(Number(value))} %`
}

export function orEmpty(value) {
  return isBlank(value) ? EMPTY : String(value)
}

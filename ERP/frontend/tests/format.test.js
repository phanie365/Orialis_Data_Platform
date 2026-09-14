import assert from 'node:assert/strict'
import { test } from 'node:test'

import { formatAmount, formatCount, formatDate, formatDateTime, formatPercent } from '../src/format.js'

test('amounts always carry their own currency', () => {
  assert.equal(formatAmount(1234.5, 'CHF'), '1,234.50 CHF')
  assert.equal(formatAmount(-250, 'EUR'), '-250.00 EUR')
  assert.equal(formatAmount(38469650.19, 'EUR'), '38,469,650.19 EUR')
  assert.equal(formatAmount(null, 'EUR'), '—')
  assert.equal(formatAmount('abc', 'EUR'), '—')
})

test('counts, dates and instants', () => {
  assert.equal(formatCount(8129), '8,129')
  assert.equal(formatCount(0), '0')
  assert.equal(formatDate('2026-10-01'), '01/10/2026')
  assert.equal(formatDate(null), '—')
  // UTC in, UTC out: an instant late on the 23rd stays on the 23rd.
  assert.equal(formatDateTime('2026-10-23T23:30:00+00:00'), '23/10/2026 23:30 UTC')
  assert.equal(formatPercent(10), '10.00 %')
})

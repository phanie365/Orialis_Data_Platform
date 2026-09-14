"""
Daily activity simulator for the Orialis ERP.

    python ERP/scripts/simulate_day.py

Continues the history where the seed stops, one business day at a time, using
the SAME business rules: the modules here import their rates, seasonality and
row builders from `ERP.seed` rather than restating them.

    clock.py   the business date, and the guard that stops it going backwards
    day.py     one day's work: new activity, status transitions, settlement

Unlike the seed, this is deliberately NOT idempotent: two runs are two days,
not the same day twice. Each run is still atomic - the whole day is applied,
or none of it is.
"""

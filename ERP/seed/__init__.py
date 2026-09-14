"""
Deterministic generator for the Orialis ERP historical dataset.

    October 2023 -> September 2026, 36 months, ~41 300 rows.

The entry point is `ERP/scripts/seed_all.py`. This package holds the
generation logic, split by domain:

    toolkit       determinism, calendar, seasonality, distributions
    people        the 93 humans shared with the CRM, and the name pools
    reference     cost centres (25) and FX rates (36)
    organisation  suppliers (~180) and employees (~185)
    purchases     invoices, payments and their matching
    activity      expense claims and commissions
    integrity     the multi-row invariants, checked before COMMIT

Nothing here connects to a database: every module returns plain dictionaries.
The load is a separate step, which is what makes the generator testable
without PostgreSQL and what lets the whole history be built, checked, and
discarded if one invariant fails.
"""

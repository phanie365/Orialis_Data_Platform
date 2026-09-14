"""
File extracts of the Orialis ERP - the SECOND way its data leaves the system.

    conventions.py        the format decisions, in one place
    expenses_csv.py       daily CSV delta, on updated_at
    commissions_xlsx.py   monthly workbook, a snapshot

Deliberately unlike the REST API. `expenses` and `commissions` are published
ONLY as files, and the files speak a different language from both the API and
the database: European CSV conventions, business column names, status codes,
and - in the workbook - real Excel dates and numbers.

A data platform whose sources are all REST APIs is not a realistic one. This
package exists to make sure this one is not.

Nothing here writes to the database. The export script opens its connection
with `default_transaction_read_only`, so the guarantee is enforced by
PostgreSQL rather than promised by the code.
"""

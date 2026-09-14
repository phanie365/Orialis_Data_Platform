"""
The daily expense extract: one CSV per calendar day.

---------------------------------------------------------------------------
WHAT IS IN THE FILE, AND WHY
---------------------------------------------------------------------------

Three approaches were available. The specification asked for the most
realistic one, and for one that does not make the future ingestion work
trivial.

  1. NEW CLAIMS ONLY (`created_at` on that day)
     Rejected. Trivial downstream - an append-only feed with no updates -
     and, worse, wrong. An expense feed that never reported an approval or a
     reimbursement would tell the receiving system nothing about the only
     events it cares about. A claim is interesting precisely because it
     changes.

  2. A FULL SNAPSHOT EVERY DAY
     Rejected. 13 000 rows a day to describe a few dozen changes, and it
     makes incremental ingestion a non-problem: keep the last file, discard
     the rest.

  3. A DELTA ON `updated_at`  <- CHOSEN
     Everything whose `updated_at` falls inside that calendar day, UTC:

         updated_at >= day 00:00:00Z  AND  updated_at < day+1 00:00:00Z

     Half-open, so the days tile the timeline exactly: every row belongs to
     exactly one daily file, with no overlap and no gap.

This is what a nightly ERP extract actually produces, and it leaves the
interesting properties intact:

  - a claim appears in SEVERAL files over its life - the day it is filed,
    the day it is approved, the day it is reimbursed - each time with its
    state on that day. The receiver must UPSERT on `EXPENSE_REF`, keeping
    the row with the largest `LAST_MODIFIED_DT`. Appending blindly would
    triple the data.

  - the files are not disjoint sets of entities, only of events. Counting
    rows across files does not count expenses.

  - there are no deletes, because the ERP never deletes. No tombstones are
    needed, which is one real difficulty the receiver is spared.

---------------------------------------------------------------------------
THE ONE PROPERTY WORTH UNDERSTANDING BEFORE BACKFILLING
---------------------------------------------------------------------------

`updated_at` holds ONE instant: the most recent change. It does not keep a
history.

So a backfill of the seeded history cannot reconstruct the intermediate
states. A claim filed on 5 March 2024 and reimbursed on 10 April 2024 has a
single `updated_at` of 10 April, and therefore appears ONLY in the file for
10 April - never in the one for 5 March, even though something really did
happen that day.

That is not a defect of this exporter. It is the defining limitation of any
`updated_at`-based feed, and the reason change-data-capture exists. The
practical consequences:

    backfilled days      each expense appears EXACTLY ONCE, in the file for
                         its last modification. The union of all backfilled
                         files is the current state of the table.

    days captured live   a claim appears once per day on which it changed,
                         which is what a feed running forward actually sees.

Both are in the same directory and look identical. A consumer that assumes
the historical files describe history will be wrong, and the manifest says
so on every file.
"""

from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from .conventions import (EXPENSE_CATEGORY_CODES, EXPENSE_STATUS_CODES,
                          as_amount, as_date, as_text, as_timestamp,
                          code, csv_manifest, write_csv, write_manifest)

# Business column order: identity, person, classification, dates, money,
# status, then the technical columns. A reading order, not the table's.
HEADER = [
    "EXPENSE_REF",
    "EMPLOYEE_NUMBER",
    "EMPLOYEE_LAST_NAME",
    "EMPLOYEE_FIRST_NAME",
    "OFFICE_CODE",
    "COST_CENTRE_CODE",
    "CATEGORY_CODE",
    "EXPENSE_DT",
    "SUBMITTED_DT",
    "CURRENCY",
    "NET_AMT",
    "VAT_RATE_PCT",
    "VAT_AMT",
    "GROSS_AMT",
    "FX_RATE_EUR",
    "GROSS_AMT_EUR",
    "STATUS_CODE",
    "APPROVED_BY",
    "APPROVED_DT",
    "REJECT_REASON",
    "REIMBURSED_DT",
    "REIMBURSEMENT_REF",
    "RECEIPT_REF",
    "CREATED_DT",
    "LAST_MODIFIED_DT",
    "EXTRACT_ID",
    "EXTRACT_DT",
]

# The employee name and office are joined in on purpose. They are not in the
# `expenses` table, and `employees` is not exposed by the REST API - so this
# feed is the ONLY place a consumer can see who filed a claim. That
# asymmetry is deliberate: different consumers were given different
# interfaces at different times, which is what a heterogeneous landscape
# looks like.
QUERY = """
    SELECT e.expense_id, e.employee_ref, emp.last_name, emp.first_name,
           emp.office_code, e.cost_center_id, e.expense_category,
           e.expense_date, e.submitted_date, e.currency_code,
           e.net_amount, e.tax_rate, e.tax_amount, e.gross_amount,
           e.fx_rate_to_eur, e.gross_amount_eur, e.expense_status,
           e.approved_by_employee_ref, e.approved_at, e.rejection_reason,
           e.reimbursed_on, e.reimbursement_reference, e.receipt_reference,
           e.created_at, e.updated_at
    FROM expenses e
    JOIN employees emp ON emp.employee_ref = e.employee_ref
    WHERE e.updated_at >= %s AND e.updated_at < %s
    ORDER BY e.updated_at, e.expense_id
"""


def output_path(root: Path, business_date: date) -> Path:
    """`daily/expenses/YYYY/MM/EXPENSES_YYYYMMDD.csv`.

    Deterministic, sortable, and partitioned by year and month so a directory
    listing stays usable after three years of daily files.
    """
    return (root / "daily" / "expenses"
            / f"{business_date:%Y}" / f"{business_date:%m}"
            / f"EXPENSES_{business_date:%Y%m%d}.csv")


def extract_identity(business_date: date) -> tuple[str, date]:
    """The run's own identity, derived from the business date.

    `EXTRACT_DT` is the MORNING AFTER the business day - the nightly batch
    runs once the day is closed - and not a wall-clock reading. That is what
    keeps the file byte-identical when it is regenerated from unchanged data:
    a `now()` in a technical column would make every re-run produce a
    different file and make determinism untestable.
    """
    return (f"EXP-D-{business_date:%Y%m%d}", business_date + timedelta(days=1))


def export_day(connection, root: Path, business_date: date) -> dict:
    """Write one day's extract. Reads only; never writes to the database."""
    start = datetime.combine(business_date, datetime.min.time(), timezone.utc)
    end = start + timedelta(days=1)

    rows = connection.execute(QUERY, (start, end)).fetchall()

    extract_id, extract_date = extract_identity(business_date)
    extract_dt = as_date(extract_date)

    lines = [[
        as_text(row["expense_id"]),
        as_text(row["employee_ref"]),
        as_text(row["last_name"]),
        as_text(row["first_name"]),
        as_text(row["office_code"]),
        as_text(row["cost_center_id"]),
        code(EXPENSE_CATEGORY_CODES, row["expense_category"]),
        as_date(row["expense_date"]),
        as_date(row["submitted_date"]),
        as_text(row["currency_code"]),
        as_amount(row["net_amount"]),
        as_amount(row["tax_rate"]),
        as_amount(row["tax_amount"]),
        as_amount(row["gross_amount"]),
        as_amount(row["fx_rate_to_eur"], places=6),
        as_amount(row["gross_amount_eur"]),
        code(EXPENSE_STATUS_CODES, row["expense_status"]),
        as_text(row["approved_by_employee_ref"]),
        as_timestamp(row["approved_at"]),
        as_text(row["rejection_reason"]),
        as_date(row["reimbursed_on"]),
        as_text(row["reimbursement_reference"]),
        as_text(row["receipt_reference"]),
        as_timestamp(row["created_at"]),
        as_timestamp(row["updated_at"]),
        extract_id,
        extract_dt,
    ] for row in rows]

    path = output_path(root, business_date)
    # Written even when empty. A header-only file says "nothing changed
    # that day"; a missing file says "the batch did not run". A feed that
    # cannot tell them apart eventually reads an outage as a quiet week.
    count = write_csv(path, HEADER, lines)

    manifest = write_manifest(path, csv_manifest(path, count, {
        "dataset": "expenses",
        "business_date": business_date.isoformat(),
        "extract_id": extract_id,
        "extract_date": extract_date.isoformat(),
        "extraction_mode": "delta on updated_at, half-open [day, day+1) UTC",
        "grain": "one row per expense MODIFIED on this business date",
        "consumer_note": (
            "An expense appears in several daily files over its life. "
            "Upsert on EXPENSE_REF, keeping the greatest LAST_MODIFIED_DT. "
            "Row counts across files do not count expenses."),
        "status_codes": EXPENSE_STATUS_CODES,
        "category_codes": EXPENSE_CATEGORY_CODES,
    }))

    return {
        "path": path,
        "manifest": manifest,
        "rows": count,
        "business_date": business_date,
    }

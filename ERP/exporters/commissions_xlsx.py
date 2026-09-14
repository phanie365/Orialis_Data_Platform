"""
The monthly commission workbook: one .xlsx per month.

---------------------------------------------------------------------------
WHY A WORKBOOK, AND WHY IT DIFFERS FROM THE CSV ON PURPOSE
---------------------------------------------------------------------------

This file goes to payroll and finance, and it is read by people. That single
fact drives every difference from the daily CSV:

    CSV                              XLSX
    machine-to-machine               opened by a human
    status codes (REIMB, APPR)       readable labels (Reimbursed, Approved)
    dates as DD/MM/YYYY strings      REAL Excel dates, displayed DD/MM/YYYY
    amounts as "1234,56" strings     REAL numbers, formatted # ##0,00
    rates as strings                 real fractions, displayed as percentages

The contrast is the point of producing both. A platform that ingests only
CSVs learns to parse strings; one that ingests only workbooks learns to trust
types. A realistic landscape hands it both and lets it discover that
`1234,56` and `1234.56` are the same money.

---------------------------------------------------------------------------
TWO SHEETS, AND THE JUSTIFICATION FOR THE SECOND
---------------------------------------------------------------------------

    Commissions   one row per commission - what finance books
    Summary       one row per advisor   - what payroll pays

They are not the same question. Payroll transfers ONE amount to a person and
needs it per person; finance books each commission against its cost centre
and needs the line. A single sheet would force one of the two to aggregate by
hand in the file it was sent, every month.

That is a real need, not decoration, which is the bar the specification set.
There is no third sheet: the export metadata lives in the workbook properties
and in the manifest beside the file, where it does not interfere with reading
either grid.

---------------------------------------------------------------------------
WHAT "THE MONTH'S COMMISSIONS" MEANS
---------------------------------------------------------------------------

Commissions CALCULATED in that month - `calculated_at` inside it - not
commissions whose period falls in it.

That is the run, and it is what a payroll department recognises: the January
workbook carries January's monthly commissions AND the previous year's
annual bonus, because both are computed in the same end-of-January run. A
file built on the period instead would split that run across two files and
match nothing payroll has ever seen.

---------------------------------------------------------------------------
A SNAPSHOT, NOT A DELTA - AND IT CAN BE RESTATED
---------------------------------------------------------------------------

The daily CSV is an immutable delta. This is the opposite: a snapshot of that
month's commissions AS THEY STAND when the export runs.

A commission is Calculated at month end, Validated a week or two later, Paid
after that. Regenerating October's workbook in December therefore produces a
file with the same rows and different statuses. That is not a defect - it is
what a re-issued payroll statement is - but it means the consumer must treat
the monthly file as REPLACEABLE, keyed on the period, where the daily file is
append-only.

Two feeds, two contracts, in the same system. Which is the realistic case.
"""

from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font
from openpyxl.utils import get_column_letter

from .conventions import SCHEMA_VERSION, sha256_of, write_manifest

# Headers are human sentences, not column names. This workbook is opened by a
# person; the CSV is the one that speaks in UPPER_SNAKE.
DETAIL_HEADER = [
    "Commission ref", "Employee number", "Last name", "First name",
    "Office", "Cost centre", "Commission type", "Period type",
    "Period start", "Period end", "Basis amount", "Rate", "Currency",
    "Commission amount", "FX rate to EUR", "Commission amount EUR",
    "Status", "Calculated on", "Validated on", "Validated by", "Paid on",
    "Payroll ref", "Source system",
]

SUMMARY_HEADER = [
    "Employee number", "Last name", "First name", "Office", "Currency",
    "Commission count", "Total amount", "Total amount EUR",
]

DATE_FORMAT = "DD/MM/YYYY"
AMOUNT_FORMAT = "#,##0.00"
RATE_FORMAT = "0.00%"

DETAIL_SHEET = "Commissions"
SUMMARY_SHEET = "Summary"

QUERY = """
    SELECT c.commission_id, c.employee_ref, emp.last_name, emp.first_name,
           emp.office_code, c.cost_center_id, c.commission_type,
           c.period_type, c.period_start, c.period_end, c.basis_amount,
           c.commission_rate, c.currency_code, c.commission_amount,
           c.fx_rate_to_eur, c.commission_amount_eur, c.commission_status,
           c.calculated_at, c.validated_at, c.validated_by_employee_ref,
           c.paid_on, c.payroll_reference, c.source_system
    FROM commissions c
    JOIN employees emp ON emp.employee_ref = c.employee_ref
    WHERE c.calculated_at >= %s AND c.calculated_at < %s
    ORDER BY emp.last_name, emp.first_name, c.commission_type, c.commission_id
"""


def month_bounds(year: int, month: int) -> tuple[datetime, datetime]:
    from datetime import timezone
    start = datetime(year, month, 1, tzinfo=timezone.utc)
    end = (datetime(year + 1, 1, 1, tzinfo=timezone.utc) if month == 12
           else datetime(year, month + 1, 1, tzinfo=timezone.utc))
    return start, end


def output_path(root: Path, year: int, month: int) -> Path:
    """`monthly/commissions/YYYY/COMMISSIONS_YYYYMM.xlsx`."""
    return (root / "monthly" / "commissions" / f"{year:04d}"
            / f"COMMISSIONS_{year:04d}{month:02d}.xlsx")


def export_month(connection, root: Path, year: int, month: int) -> dict:
    """Write one month's workbook. Reads only; never writes to the database."""
    start, end = month_bounds(year, month)
    rows = connection.execute(QUERY, (start, end)).fetchall()

    workbook = Workbook()
    _write_detail(workbook, rows)
    _write_summary(workbook, rows)
    _set_properties(workbook, year, month, len(rows))

    path = output_path(root, year, month)
    path.parent.mkdir(parents=True, exist_ok=True)
    workbook.save(path)

    advisors = {row["employee_ref"] for row in rows}
    total_eur = sum((row["commission_amount_eur"] or Decimal(0)) for row in rows)

    manifest = write_manifest(path, {
        "file": path.name,
        "format": "xlsx",
        "schema_version": SCHEMA_VERSION,
        "dataset": "commissions",
        "period": f"{year:04d}-{month:02d}",
        "period_start": start.date().isoformat(),
        "period_end": (end - timedelta(days=1)).date().isoformat(),
        "sheets": {
            DETAIL_SHEET: {"rows": len(rows), "columns": len(DETAIL_HEADER),
                           "grain": "one row per commission"},
            SUMMARY_SHEET: {"rows": len(advisors),
                            "columns": len(SUMMARY_HEADER),
                            "grain": "one row per advisor"},
        },
        "row_count": len(rows),
        "advisor_count": len(advisors),
        "total_amount_eur": float(round(total_eur, 2)),
        "extraction_mode": "snapshot of commissions CALCULATED in this month",
        "restatable": True,
        "consumer_note": (
            "A snapshot, not a delta. Statuses advance after the run "
            "(Calculated -> Validated -> Paid), so re-exporting an earlier "
            "month yields the same rows with newer statuses. Replace the "
            "period wholesale; do not append."),
        "value_types": {
            "dates": "real Excel dates, displayed DD/MM/YYYY",
            "amounts": "real numbers, displayed #,##0.00",
            "rate": "real fraction, displayed as a percentage",
            "status": "readable labels, not codes (unlike the CSV feed)",
        },
        # The byte checksum of an .xlsx is NOT stable across runs - it is a
        # zip, and a zip carries entry metadata. So the manifest publishes a
        # checksum of the CELL VALUES instead, which is what a consumer
        # actually cares about and which IS reproducible. The byte digest is
        # published too, for transport integrity within a single delivery.
        "content_sha256": content_digest(path),
        "file_sha256": sha256_of(path),
    })

    return {"path": path, "manifest": manifest, "rows": len(rows),
            "advisors": len(advisors), "year": year, "month": month}


def _write_detail(workbook, rows):
    sheet = workbook.active
    sheet.title = DETAIL_SHEET
    sheet.append(DETAIL_HEADER)

    for row in rows:
        sheet.append([
            row["commission_id"],
            row["employee_ref"],
            row["last_name"],
            row["first_name"],
            row["office_code"],
            row["cost_center_id"],
            row["commission_type"],      # readable label, not a code
            row["period_type"],
            row["period_start"],
            row["period_end"],
            _number(row["basis_amount"]),
            _number(row["commission_rate"]),
            row["currency_code"],
            _number(row["commission_amount"]),
            _number(row["fx_rate_to_eur"]),
            _number(row["commission_amount_eur"]),
            row["commission_status"],
            _naive(row["calculated_at"]),
            _naive(row["validated_at"]),
            row["validated_by_employee_ref"],
            row["paid_on"],
            row["payroll_reference"],
            row["source_system"],
        ])

    _format(sheet, dates=("I", "J", "R", "S", "U"),
            amounts=("K", "N", "P"), rates=("L",), fx=("O",))


def _write_summary(workbook, rows):
    """One row per advisor: what payroll actually transfers."""
    sheet = workbook.create_sheet(SUMMARY_SHEET)
    sheet.append(SUMMARY_HEADER)

    totals = {}
    for row in rows:
        key = row["employee_ref"]
        entry = totals.setdefault(key, {
            "last_name": row["last_name"], "first_name": row["first_name"],
            "office": row["office_code"], "currency": row["currency_code"],
            "count": 0, "amount": Decimal(0), "amount_eur": Decimal(0),
        })
        entry["count"] += 1
        entry["amount"] += row["commission_amount"] or Decimal(0)
        entry["amount_eur"] += row["commission_amount_eur"] or Decimal(0)

    # In order of first appearance in the detail rows, which QUERY already
    # sorts by name. So the two sheets list advisors in the same order, and
    # two exports of the same month lay the rows out identically - the
    # determinism the specification asks for. A Python sort here would be
    # case-sensitive ("BIANCHI" before "Barbieri") and disagree with the
    # database collation the detail sheet follows.
    for employee_ref, entry in totals.items():
        sheet.append([
            employee_ref, entry["last_name"], entry["first_name"],
            entry["office"], entry["currency"], entry["count"],
            _number(entry["amount"]), _number(entry["amount_eur"]),
        ])

    _format(sheet, dates=(), amounts=("G", "H"), rates=(), fx=())


def _format(sheet, dates, amounts, rates, fx):
    """Header styling, number formats and column widths.

    Cosmetic, but not decoration: a workbook a finance team cannot read at a
    glance gets re-keyed by hand, and that is where errors enter.
    """
    for cell in sheet[1]:
        cell.font = Font(bold=True)
        cell.alignment = Alignment(horizontal="center")
    sheet.freeze_panes = "A2"

    for row in sheet.iter_rows(min_row=2):
        for cell in row:
            letter = cell.column_letter
            if letter in dates:
                cell.number_format = DATE_FORMAT
            elif letter in rates:
                cell.number_format = RATE_FORMAT
            elif letter in fx:
                cell.number_format = "0.000000"
            elif letter in amounts:
                cell.number_format = AMOUNT_FORMAT

    for index in range(1, sheet.max_column + 1):
        letter = get_column_letter(index)
        width = max((len(str(cell.value)) for cell in sheet[letter]
                     if cell.value is not None), default=10)
        sheet.column_dimensions[letter].width = min(max(width + 2, 11), 26)


def _set_properties(workbook, year, month, row_count):
    """Document properties - and FIXED timestamps.

    openpyxl would otherwise stamp the current time into docProps, which
    would make two exports of an unchanged month differ. Deriving the stamp
    from the period instead keeps the workbook reproducible.
    """
    from datetime import timezone
    _, end = month_bounds(year, month)
    stamp = (end - timedelta(days=1)).replace(tzinfo=None)

    workbook.properties.title = f"Orialis commissions {year:04d}-{month:02d}"
    workbook.properties.creator = "Orialis ERP"
    workbook.properties.description = (
        f"{row_count} commissions calculated in {year:04d}-{month:02d}. "
        f"Snapshot at export time; statuses may advance afterwards.")
    workbook.properties.created = stamp
    workbook.properties.modified = stamp


def _number(value):
    """Decimal -> float, so Excel stores a NUMBER rather than text.

    Rounded to the stored scale first: a Decimal that cannot be represented
    exactly as a float would otherwise land in the cell as 750.0000000000001.
    """
    if value is None:
        return None
    return float(round(Decimal(value), 6))


def _naive(value):
    """Excel has no timezone. The instant is UTC; the manifest says so."""
    if value is None:
        return None
    from datetime import timezone
    if value.tzinfo is not None:
        value = value.astimezone(timezone.utc)
    return value.replace(tzinfo=None)


def content_digest(path: Path) -> str:
    """A checksum of the CELL VALUES, reproducible across exports.

    The bytes of an .xlsx are not stable - it is a zip archive, and archives
    carry metadata. Hashing what the sheets actually contain gives a
    consumer something it can compare between two deliveries of the same
    period, which is the question it is really asking: did the data change?
    """
    import hashlib
    from openpyxl import load_workbook

    workbook = load_workbook(path, read_only=True, data_only=True)
    digest = hashlib.sha256()
    for name in workbook.sheetnames:
        digest.update(name.encode("utf-8"))
        for row in workbook[name].iter_rows(values_only=True):
            digest.update(repr(row).encode("utf-8"))
    workbook.close()
    return digest.hexdigest()

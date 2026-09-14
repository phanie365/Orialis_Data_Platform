"""
The file-format conventions of the Orialis ERP extracts.

---------------------------------------------------------------------------
WHY THE FILES DO NOT LOOK LIKE THE DATABASE
---------------------------------------------------------------------------

A file feed is not a database dump. It is written by an export program that
was specified by a finance team, for a receiving system that was specified by
somebody else, usually years apart. The result is a format with its own
vocabulary, its own date convention and its own idea of what a number looks
like - and a data platform has to cope with all of it.

So these extracts deliberately differ from PostgreSQL in the ways a real ERP
extract differs:

    column names     UPPER_SNAKE business names, not table columns
                     (`EXPENSE_REF`, not `expense_id`; `COST_CENTRE_CODE`,
                     with the British spelling the finance team uses)
    column order     identity, then person, then classification, then dates,
                     then money, then status - a reading order, not the
                     order the columns happen to sit in the table
    dates            DD/MM/YYYY, the European convention
    numbers          comma decimal separator - which is exactly WHY the field
                     separator is a semicolon
    status           short codes, as a machine-to-machine feed uses
    encoding         UTF-8 with BOM, what a Windows finance workstation
                     expects
    line endings     CRLF

None of this is corruption, and none of it is difficulty for its own sake.
Every one of these choices is what a European ERP extract actually looks
like, and each creates real - but fair - parsing work downstream.

The Excel file differs from the CSV on purpose too, and that is the point of
having both: machine-to-machine CSV carries codes and serialised strings,
while a human-facing workbook carries readable labels, real Excel dates and
real numbers. A platform that ingests both cannot assume one shape.
"""

import csv
import hashlib
import json
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

# ---------------------------------------------------------------------------
# CSV dialect
# ---------------------------------------------------------------------------

# Semicolon, not comma. This is not a preference: the decimal separator below
# is a comma, so a comma field separator would make every amount ambiguous.
# The two decisions come as a pair, and that pair is the single most common
# shape of a European CSV extract.
DELIMITER = ";"

# UTF-8 with a byte-order mark. The BOM is what makes Excel on a French or
# German workstation open the file with the right encoding instead of
# mangling every accent - and accented names are exactly what these extracts
# carry. A reader must strip it: `encoding="utf-8-sig"`.
ENCODING = "utf-8-sig"

# CRLF, as RFC 4180 specifies and as every Windows-side ERP writes.
LINE_TERMINATOR = "\r\n"

# Minimal quoting: a field is quoted only when it contains the delimiter, a
# quote or a newline.
QUOTING = csv.QUOTE_MINIMAL

DATE_FORMAT = "%d/%m/%Y"
DATETIME_FORMAT = "%d/%m/%Y %H:%M:%S"

# Timestamps are written WITHOUT a timezone offset, which is what most ERP
# extracts do - and a trap worth naming rather than hiding. Every instant in
# these files is UTC. The manifest beside each file says so explicitly, so
# the information exists even though the field does not carry it.
TIMESTAMP_TIMEZONE = "UTC"

DECIMAL_SEPARATOR = ","

# No thousands separator. Some ERPs write "1 234,56"; that is a step too far
# for a machine-to-machine feed and would be difficulty for its own sake.
THOUSANDS_SEPARATOR = ""

# An absent value is an empty field. Not "NULL", not "N/A" - those are
# strings that a careless reader turns into data.
NULL = ""

SCHEMA_VERSION = "1.0"


# ---------------------------------------------------------------------------
# Business codes
# ---------------------------------------------------------------------------
# A machine-to-machine feed carries codes, not sentences. The receiving
# system has a lookup table; the label may be translated on its side.
#
# The mapping is declared here, in one place, and repeated in every manifest,
# so a consumer never has to guess what REIMB means.

EXPENSE_STATUS_CODES = {
    "Draft": "DRAFT",
    "Submitted": "SUBM",
    "Approved": "APPR",
    "Rejected": "REJ",
    "Reimbursed": "REIMB",
    "Cancelled": "CANC",
}

EXPENSE_CATEGORY_CODES = {
    "Travel": "TRV",
    "Accommodation": "ACC",
    "Meals": "MEA",
    "Client Entertainment": "ENT",
    "Transport": "TRA",
    "Training": "TRN",
    "Telecom": "TEL",
    "Office Supplies": "OFF",
}


# ---------------------------------------------------------------------------
# Serialisation
# ---------------------------------------------------------------------------

def as_date(value) -> str:
    """A calendar day, DD/MM/YYYY. Empty when absent."""
    if value is None:
        return NULL
    if isinstance(value, datetime):
        value = value.date()
    return value.strftime(DATE_FORMAT)


def as_timestamp(value) -> str:
    """An instant, DD/MM/YYYY HH:MM:SS, in UTC, with no offset written.

    The offset is dropped because that is what these extracts do. It is
    recoverable: the manifest states the timezone, and every instant in the
    ERP is stored in UTC.
    """
    if value is None:
        return NULL
    if value.tzinfo is not None:
        from datetime import timezone
        value = value.astimezone(timezone.utc)
    return value.strftime(DATETIME_FORMAT)


def as_amount(value, places: int = 2) -> str:
    """A monetary amount: fixed decimals, comma separator, no grouping.

    Formatted from the Decimal PostgreSQL returns, never from a float. A
    float round-trip is how a cent goes missing, and an export that loses
    cents is worse than no export.
    """
    if value is None:
        return NULL
    if not isinstance(value, Decimal):
        value = Decimal(str(value))
    text = f"{value:.{places}f}"
    return text.replace(".", DECIMAL_SEPARATOR)


def as_text(value) -> str:
    """Anything else. `None` becomes an empty field, never the word None."""
    return NULL if value is None else str(value)


def code(mapping: dict, value) -> str:
    """Translate a business value into its feed code.

    An unmapped value passes through UNCHANGED rather than becoming an error
    or a blank. If a new expense category is added to the ERP tomorrow, this
    feed keeps working and the receiving system sees a value it does not
    know - which is a visible, diagnosable problem. Silently blanking it
    would be an invisible one.
    """
    if value is None:
        return NULL
    return mapping.get(value, value)


# ---------------------------------------------------------------------------
# Writing
# ---------------------------------------------------------------------------

def write_csv(path: Path, header: list[str], rows: list[list[str]]) -> int:
    """Write one extract, and return the number of data rows.

    The file is always written, even with zero rows. A header-only file and a
    missing file mean different things: "nothing happened that day" and
    "the batch did not run". A downstream feed that cannot tell them apart
    will eventually treat an outage as a quiet day.
    """
    path.parent.mkdir(parents=True, exist_ok=True)

    # newline="" is required: csv writes the line terminator itself, and
    # letting Python translate it as well produces CRCRLF on Windows.
    with path.open("w", encoding=ENCODING, newline="") as handle:
        writer = csv.writer(handle, delimiter=DELIMITER, quoting=QUOTING,
                            lineterminator=LINE_TERMINATOR)
        writer.writerow(header)
        writer.writerows(rows)

    return len(rows)


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(65536), b""):
            digest.update(block)
    return digest.hexdigest()


def write_manifest(path: Path, payload: dict) -> Path:
    """The control file that travels beside every extract.

    A classic of file-based feeds, and the thing that makes a file feed
    auditable at all: the receiver can verify it got the whole file, knows
    how many rows to expect, and does not have to guess the encoding or the
    delimiter.

    DELIBERATELY FREE OF ANY WALL-CLOCK VALUE. Everything in it is derived
    from the data and the business date, so re-running an unchanged export
    reproduces the manifest byte for byte - which is what lets a test assert
    determinism rather than merely hope for it.
    """
    manifest_path = path.with_suffix(path.suffix + ".manifest.json")
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True)
        + "\n",
        encoding="utf-8",
    )
    return manifest_path


def csv_manifest(path: Path, row_count: int, extra: dict) -> dict:
    """The manifest body shared by every CSV extract."""
    payload = {
        "file": path.name,
        "format": "csv",
        "schema_version": SCHEMA_VERSION,
        "row_count": row_count,
        "sha256": sha256_of(path),
        "encoding": ENCODING,
        "delimiter": DELIMITER,
        "decimal_separator": DECIMAL_SEPARATOR,
        "thousands_separator": THOUSANDS_SEPARATOR or None,
        "line_terminator": "CRLF",
        "quoting": "minimal",
        "date_format": "DD/MM/YYYY",
        "timestamp_format": "DD/MM/YYYY HH:MM:SS",
        "timestamp_timezone": TIMESTAMP_TIMEZONE,
        "null_representation": "empty field",
    }
    payload.update(extra)
    return payload

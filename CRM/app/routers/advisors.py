"""Advisor endpoints of the Orialis CRM API."""

import math
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, HTTPException, Path, Query

from ..database import get_connection

# Same prefix as the other routers, but its own tag: Swagger shows "clients",
# "branches" and "advisors" as three separate sections.
router = APIRouter(prefix="/api/v1", tags=["advisors"])


# Maps the public query-parameter name to the SQL condition it produces.
# Each template holds exactly one "?", which is where the caller's value is
# bound - the value itself is never written into the string.
#
# Only the SQL written HERE can ever reach the query, so a caller cannot
# inject a column name or an operator.
#
# `spoken_language` is the odd one out. spoken_languages is stored as a
# comma-separated list ("FR,EN,IT"), so the filter cannot be an equality: it
# has to look INSIDE the text. See the note on delimiters below.
FILTER_CONDITIONS = {
    "branch_id": "branch_id = ?",
    "advisor_status": "advisor_status = ?",
    "specialization": "specialization = ?",
    "spoken_language": "',' || spoken_languages || ',' LIKE '%,' || ? || ',%'",
    "updated_since": "updated_at >= ?",
}

# How timestamps are stored in the CRM database: "2026-09-01 14:30:00".
# Note the SPACE between date and time, where an ISO-8601 input uses a "T".
TIMESTAMP_FORMAT = "%Y-%m-%d %H:%M:%S"


def build_where_clause(filters):
    """Build the WHERE clause from the filters that were actually provided.

    Returns the SQL fragment and the list of values to bind to it. Filters
    left empty are skipped, so the clause grows with the request:

        no filter                -> ""                        []
        branch_id=BR001          -> " WHERE branch_id = ?"     ["BR001"]
        branch_id=BR001
        &spoken_language=FR      -> " WHERE branch_id = ?
                                       AND ',' || spoken_languages || ','
                                           LIKE '%,' || ? || ',%'"
                                                          ["BR001", "FR"]
    """
    conditions = []
    values = []

    for parameter_name, value in filters.items():
        if value is None:
            continue
        conditions.append(FILTER_CONDITIONS[parameter_name])
        values.append(value)

    if not conditions:
        return "", []

    return " WHERE " + " AND ".join(conditions), values


@router.get(
    "/advisors",
    summary="List advisors",
    response_description="A page of advisors, plus pagination metadata.",
)
def list_advisors(
    page: int = Query(
        1,
        ge=1,
        description="Page number to return, starting at 1.",
    ),
    page_size: int = Query(
        50,
        ge=1,
        le=200,
        description="Number of advisors per page (1 to 200).",
    ),
    branch_id: Optional[str] = Query(
        None,
        description="Filter on the branch the advisor works in, e.g. BR001.",
    ),
    advisor_status: Optional[str] = Query(
        None,
        description="Filter on the advisor status, e.g. Active.",
    ),
    specialization: Optional[str] = Query(
        None,
        description=(
            "Filter on the advisor's specialization. One of: "
            "Wealth Management, Investment Advisory, Retirement Planning, "
            "Estate Planning, International Clients."
        ),
    ),
    spoken_language: Optional[str] = Query(
        None,
        description=(
            "Return advisors who speak this language. Matches a single code "
            "inside the comma-separated spoken_languages field, so FR also "
            "matches an advisor recorded as 'FR,EN,IT'. Codes in use: "
            "FR, EN, IT."
        ),
    ),
    updated_since: Optional[datetime] = Query(
        None,
        description=(
            "Return only advisors whose updated_at timestamp is greater than "
            "or equal to this date-time. Used for incremental data "
            "extraction. Format: YYYY-MM-DDTHH:MM:SS, "
            "e.g. 2026-01-01T00:00:00."
        ),
    ),
):
    """Return a page of advisors, with optional business filters.

    Every filter is optional and they can all be combined. `total_records`
    always reflects the filters in use, not the size of the whole table.

    Database columns are returned as they are, with no renaming and no
    transformation.
    """
    # Rendered into the exact string format used by the database, so SQLite
    # compares two strings written the same way. Sending the ISO form
    # ("...T00:00:00") would compare a "T" against a space and silently drop
    # valid rows.
    updated_since_value = (
        updated_since.strftime(TIMESTAMP_FORMAT) if updated_since else None
    )

    where_sql, where_values = build_where_clause({
        "branch_id": branch_id,
        "advisor_status": advisor_status,
        "specialization": specialization,
        "spoken_language": spoken_language,
        "updated_since": updated_since_value,
    })

    # Page 1 starts at row 0, page 2 at row `page_size`, and so on.
    offset = (page - 1) * page_size

    with get_connection() as connection:
        # How many advisors match the filters, ignoring pagination. COUNT(*)
        # is computed by SQLite, so no row is transferred to Python for it.
        total_records = connection.execute(
            f"SELECT COUNT(*) FROM advisors{where_sql}",
            where_values,
        ).fetchone()[0]

        # Only the requested page leaves the database. ORDER BY makes the
        # paging stable: without it, SQLite is free to return rows in any
        # order and an advisor could appear on two pages, or on none.
        rows = connection.execute(
            f"SELECT * FROM advisors{where_sql} ORDER BY advisor_id LIMIT ? OFFSET ?",
            where_values + [page_size, offset],
        ).fetchall()

    # Integer ceiling: 100 records of 50 -> 2 pages, 101 -> 3 pages.
    # No match means 0 records and 0 pages, and still an HTTP 200.
    total_pages = math.ceil(total_records / page_size)

    return {
        "data": [dict(row) for row in rows],
        "pagination": {
            "page": page,
            "page_size": page_size,
            "total_records": total_records,
            "total_pages": total_pages,
        },
    }


@router.get(
    "/advisors/{advisor_id}",
    summary="Get one advisor by id",
    response_description="The advisor matching the requested id.",
    responses={404: {"description": "No advisor exists with this id."}},
)
def get_advisor(
    advisor_id: str = Path(
        ...,
        description="Identifier of the advisor to retrieve, e.g. ADV001.",
    ),
):
    """Return a single advisor, identified by their `advisor_id`.

    This endpoint addresses ONE precise resource, so it returns the advisor
    object itself rather than a paginated envelope. An unknown id is an
    error, not an empty result, and answers 404.
    """
    with get_connection() as connection:
        # The id is a bound parameter, never glued into the SQL string.
        # fetchone() returns a single row, or None if nothing matched.
        row = connection.execute(
            "SELECT * FROM advisors WHERE advisor_id = ?",
            (advisor_id,),
        ).fetchone()

    if row is None:
        raise HTTPException(status_code=404, detail="Advisor not found")

    return dict(row)

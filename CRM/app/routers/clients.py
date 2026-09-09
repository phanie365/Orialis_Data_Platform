"""Client endpoints of the Orialis CRM API."""

import math
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, HTTPException, Path, Query

from ..database import get_connection

# An APIRouter groups related routes. The prefix is added in front of every
# route declared below, so "/clients" here becomes "/api/v1/clients".
# `tags` is only used to group the routes in the automatic documentation.
router = APIRouter(prefix="/api/v1", tags=["clients"])


# Maps the public query-parameter name to the real database column.
# The API speaks a stable, readable language ("country", "segment") while the
# table keeps its own column names - and callers never need to know them.
#
# This dictionary is also a safety boundary: only the column names written
# HERE can ever reach the SQL string. A caller cannot inject a column name.
#
# Each entry is (column, comparison operator). Most filters are exact
# matches; `updated_since` is a lower bound, which is what makes incremental
# extraction possible.
FILTER_COLUMNS = {
    "country": ("country_of_residence", "="),
    "segment": ("client_segment", "="),
    "risk_profile": ("risk_profile", "="),
    "client_status": ("client_status", "="),
    "advisor_id": ("advisor_id", "="),
    "updated_since": ("updated_at", ">="),
}

# How timestamps are stored in the CRM database: "2026-09-01 14:30:00".
# Note the SPACE between date and time, where an ISO-8601 input uses a "T".
TIMESTAMP_FORMAT = "%Y-%m-%d %H:%M:%S"


def build_where_clause(filters):
    """Build the WHERE clause from the filters that were actually provided.

    Returns the SQL fragment and the list of values to bind to it.

    Filters left empty are skipped, so the clause grows with the request:

        no filter                      -> ""                       []
        country=France                 -> " WHERE country_of_residence = ?"
                                                                   ["France"]
        country=France&segment=Standard
                                       -> " WHERE country_of_residence = ?
                                             AND client_segment = ?"
                                                       ["France", "Standard"]

    Note what is and is not inserted into the string: the COLUMN NAME comes
    from FILTER_COLUMNS, which we control. The VALUE never does - it is
    replaced by a "?" placeholder and passed to SQLite separately.
    """
    conditions = []
    values = []

    for parameter_name, value in filters.items():
        if value is None:
            continue
        column, operator = FILTER_COLUMNS[parameter_name]
        conditions.append(f"{column} {operator} ?")
        values.append(value)

    if not conditions:
        return "", []

    return " WHERE " + " AND ".join(conditions), values


@router.get(
    "/clients",
    summary="List clients",
    response_description="A page of clients, plus pagination metadata.",
)
def list_clients(
    page: int = Query(
        1,
        ge=1,
        description="Page number to return, starting at 1.",
    ),
    page_size: int = Query(
        100,
        ge=1,
        le=500,
        description="Number of clients per page (1 to 500).",
    ),
    country: Optional[str] = Query(
        None,
        description=(
            "Filter on the client's country of residence. "
            "One of: France, Switzerland, Belgium, Italy."
        ),
    ),
    segment: Optional[str] = Query(
        None,
        description=(
            "Filter on the commercial segment. "
            "One of: Standard, Patrimonial, Private Banking."
        ),
    ),
    risk_profile: Optional[str] = Query(
        None,
        description=(
            "Filter on the investment risk profile. "
            "One of: Conservative, Balanced, Growth."
        ),
    ),
    client_status: Optional[str] = Query(
        None,
        description=(
            "Filter on the client status. One of: Active, Inactive, Prospect."
        ),
    ),
    advisor_id: Optional[str] = Query(
        None,
        description="Filter on the advisor following the client, e.g. ADV014.",
    ),
    updated_since: Optional[datetime] = Query(
        None,
        description=(
            "Return only clients whose updated_at timestamp is greater than "
            "or equal to this date-time. Used for incremental data "
            "extraction. Format: YYYY-MM-DDTHH:MM:SS, "
            "e.g. 2026-09-01T00:00:00."
        ),
    ),
):
    """Return a page of clients, with optional business filters.

    Every filter is optional and they can all be combined. `total_records`
    always reflects the filters in use, not the size of the whole table.

    Database columns are returned as they are, with no renaming and no
    transformation.
    """
    # FastAPI hands us a real datetime object. It is rendered into the exact
    # string format used by the database, so SQLite compares two strings
    # written the same way. Passing the datetime object straight through
    # would send "2026-09-01T00:00:00" - with a "T" - and "T" sorts after a
    # space, so the comparison would silently drop valid rows.
    updated_since_value = (
        updated_since.strftime(TIMESTAMP_FORMAT) if updated_since else None
    )

    where_sql, where_values = build_where_clause({
        "country": country,
        "segment": segment,
        "risk_profile": risk_profile,
        "client_status": client_status,
        "advisor_id": advisor_id,
        "updated_since": updated_since_value,
    })

    # Page 1 starts at row 0, page 2 at row `page_size`, and so on.
    offset = (page - 1) * page_size

    with get_connection() as connection:
        # How many clients match the filters, ignoring pagination. COUNT(*)
        # is computed by SQLite, so no row is transferred to Python for it.
        total_records = connection.execute(
            f"SELECT COUNT(*) FROM clients{where_sql}",
            where_values,
        ).fetchone()[0]

        # Only the requested page leaves the database. ORDER BY makes the
        # paging stable: without it, SQLite is free to return rows in any
        # order and a client could appear on two pages, or on none.
        rows = connection.execute(
            f"SELECT * FROM clients{where_sql} ORDER BY client_id LIMIT ? OFFSET ?",
            where_values + [page_size, offset],
        ).fetchall()

    # Integer ceiling: 5000 records of 100 -> 50 pages, 5001 -> 51 pages.
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
    "/clients/{client_id}",
    summary="Get one client by id",
    response_description="The client matching the requested id.",
    responses={404: {"description": "No client exists with this id."}},
)
def get_client(
    client_id: str = Path(
        ...,
        description="Identifier of the client to retrieve, e.g. CLT0001.",
    ),
):
    """Return a single client, identified by its `client_id`.

    Unlike the list endpoint, this one addresses ONE precise resource: it
    returns the client object itself, not a paginated envelope. An unknown
    id is an error, not an empty result, so it answers 404.

    Database columns are returned as they are, with no renaming and no
    transformation.
    """
    with get_connection() as connection:
        # The id is passed as a bound parameter, never glued into the SQL
        # string - same rule as every other query in this router.
        # fetchone() returns a single row, or None if nothing matched.
        row = connection.execute(
            "SELECT * FROM clients WHERE client_id = ?",
            (client_id,),
        ).fetchone()

    if row is None:
        # HTTPException lets FastAPI build the response: status 404 and a
        # body of {"detail": "Client not found"}.
        raise HTTPException(status_code=404, detail="Client not found")

    return dict(row)

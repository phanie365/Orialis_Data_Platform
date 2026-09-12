"""Interaction endpoints of the Orialis CRM API."""

import math
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, HTTPException, Path, Query

from ..database import get_connection

# Same prefix as the other routers, but its own tag: Swagger shows "clients",
# "branches", "advisors" and "interactions" as separate sections.
router = APIRouter(prefix="/api/v1", tags=["interactions"])


# Maps the public query-parameter name to the SQL condition it produces.
# Each template holds exactly one "%s", which is where the caller's value is
# bound - the value itself is never written into the string.
#
# Note the two different time columns. They answer two different questions:
#   interaction_date -> WHEN THE MEETING HAPPENED   (business event)
#   created_at       -> WHEN THE RECORD WAS WRITTEN (CRM bookkeeping)
FILTER_CONDITIONS = {
    "client_id": "client_id = %s",
    "advisor_id": "advisor_id = %s",
    "interaction_type": "interaction_type = %s",
    "channel": "channel = %s",
    "date_from": "interaction_date >= %s",
    "date_to": "interaction_date <= %s",
    "created_since": "created_at >= %s",
}

# Most recent interactions first. `interaction_id` breaks ties: 49 timestamps
# in this table are shared by several interactions, and a sort that leaves
# ties unordered is not stable - the same row could then appear on two pages,
# or on none. The id is unique, so it makes the ordering total.
ORDER_BY = "ORDER BY interaction_date DESC, interaction_id DESC"


def build_where_clause(filters):
    """Build the WHERE clause from the filters that were actually provided.

    Returns the SQL fragment and the list of values to bind to it. Filters
    left empty are skipped, so the clause grows with the request:

        no filter               -> ""                           []
        channel=Phone           -> " WHERE channel = %s"         ["Phone"]
        advisor_id=ADV001
        &date_from=2026-01-01   -> " WHERE advisor_id = %s
                                      AND interaction_date >= %s"
                                    ["ADV001", datetime(2026, 1, 1)]

    The SQL comes from FILTER_CONDITIONS, which we control. The VALUE never
    does - it is bound to the "%s" placeholder and passed to the server
    separately.
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
    "/interactions",
    summary="List interactions",
    response_description="A page of interactions, plus pagination metadata.",
)
def list_interactions(
    page: int = Query(
        1,
        ge=1,
        description="Page number to return, starting at 1.",
    ),
    page_size: int = Query(
        100,
        ge=1,
        le=500,
        description="Number of interactions per page (1 to 500).",
    ),
    client_id: Optional[str] = Query(
        None,
        description="Filter on the client involved, e.g. CLT0001.",
    ),
    advisor_id: Optional[str] = Query(
        None,
        description="Filter on the advisor involved, e.g. ADV001.",
    ),
    interaction_type: Optional[str] = Query(
        None,
        description=(
            "Filter on the interaction type. One of: Portfolio Review, "
            "Advisory, Follow-up, Client Meeting, Administrative Update, "
            "Prospecting, Complaint."
        ),
    ),
    channel: Optional[str] = Query(
        None,
        description=(
            "Filter on the channel used. One of: Email, Phone, Video Call, "
            "In Person, Client Portal."
        ),
    ),
    date_from: Optional[datetime] = Query(
        None,
        description=(
            "BUSINESS DATE filter: keep interactions that HAPPENED on or "
            "after this date-time (interaction_date >= date_from). "
            "Format: YYYY-MM-DDTHH:MM:SS."
        ),
    ),
    date_to: Optional[datetime] = Query(
        None,
        description=(
            "BUSINESS DATE filter: keep interactions that HAPPENED on or "
            "before this date-time (interaction_date <= date_to). "
            "Format: YYYY-MM-DDTHH:MM:SS."
        ),
    ),
    created_since: Optional[datetime] = Query(
        None,
        description=(
            "RECORD DATE filter, for incremental extraction: keep "
            "interactions whose CRM record was CREATED on or after this "
            "date-time (created_at >= created_since). This is not the same "
            "as date_from: a meeting held last month can be recorded today, "
            "and only created_since will catch it. "
            "Format: YYYY-MM-DDTHH:MM:SS."
        ),
    ),
):
    """Return a page of interactions, most recent first.

    Every filter is optional and they can all be combined. `total_records`
    always reflects the filters in use, not the size of the whole table.

    Database columns are returned as they are, with no renaming and no
    transformation.
    """
    where_sql, where_values = build_where_clause({
        "client_id": client_id,
        "advisor_id": advisor_id,
        "interaction_type": interaction_type,
        "channel": channel,
        # Both time columns are real TIMESTAMPTZ, and FastAPI already parsed
        # these parameters into datetimes, so the objects go straight to the
        # server. The text era needed a strftime() here: comparing the ISO
        # form ("...T00:00:00") against the stored form ("... 00:00:00")
        # compared a "T" with a space and silently dropped valid rows.
        #
        # A value given without an offset is naive, and PostgreSQL reads it
        # in the session time zone - UTC here, which is also how the stored
        # instants were written. A value given WITH an offset now works too,
        # which the string comparison could never have handled.
        "date_from": date_from,
        "date_to": date_to,
        "created_since": created_since,
    })

    # Page 1 starts at row 0, page 2 at row `page_size`, and so on.
    offset = (page - 1) * page_size

    with get_connection() as connection:
        # How many interactions match the filters, ignoring pagination.
        # COUNT(*) is computed by the server, so no row is transferred.
        #
        # The aggregate gets an explicit alias: rows come back as
        # dictionaries (dict_row), so there is no row[0] to read, and
        # relying on the driver's default name for an unnamed expression
        # would be guesswork.
        total_records = connection.execute(
            f"SELECT COUNT(*) AS total FROM interactions{where_sql}",
            where_values,
        ).fetchone()["total"]

        # Only the requested page leaves the database. On a 30,000-row table
        # this is the difference between a few kilobytes and several
        # megabytes per request.
        rows = connection.execute(
            f"SELECT * FROM interactions{where_sql} {ORDER_BY} LIMIT %s OFFSET %s",
            where_values + [page_size, offset],
        ).fetchall()

    # Integer ceiling: 30000 records of 100 -> 300 pages, 30001 -> 301.
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
    "/interactions/{interaction_id}",
    summary="Get one interaction by id",
    response_description="The interaction matching the requested id.",
    responses={404: {"description": "No interaction exists with this id."}},
)
def get_interaction(
    interaction_id: str = Path(
        ...,
        description="Identifier of the interaction to retrieve, e.g. INT000001.",
    ),
):
    """Return a single interaction, identified by its `interaction_id`.

    This endpoint addresses ONE precise resource, so it returns the
    interaction object itself rather than a paginated envelope. An unknown id
    is an error, not an empty result, and answers 404.
    """
    with get_connection() as connection:
        # The id is a bound parameter, never glued into the SQL string.
        # fetchone() returns a single row, or None if nothing matched.
        row = connection.execute(
            "SELECT * FROM interactions WHERE interaction_id = %s",
            (interaction_id,),
        ).fetchone()

    if row is None:
        raise HTTPException(status_code=404, detail="Interaction not found")

    return dict(row)

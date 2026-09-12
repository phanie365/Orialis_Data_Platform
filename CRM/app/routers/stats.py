"""Aggregate endpoints of the Orialis CRM API.

This module is deliberately separate from the four resource routers. Those
expose CRM **records** - a client, an advisor, an interaction - and page
through them. This one exposes **numbers about** those records, and returns
no operational row at all.

Keeping the two apart matters more than it looks. An endpoint that answered
with both counts and a list of recent interactions would force every caller
that wants a total to also pay for rows it did not ask for, and would make
the dashboard's data contract impossible to reason about. `/stats/overview`
therefore stops at the aggregates; the dashboard fetches its activity feed
from `/api/v1/interactions` like any other consumer.

Everything here is computed by PostgreSQL with COUNT and GROUP BY. No table
is loaded into Python to be aggregated in a loop: the 5,000 clients never
leave the database, only the handful of resulting numbers do.
"""

from fastapi import APIRouter, Query

from ..database import get_connection

# Same prefix as the resource routers, its own Swagger section. The API-key
# dependency is declared once on the parent router in main.py, so this
# module inherits it without repeating it.
router = APIRouter(prefix="/api/v1", tags=["stats"])


# The client status that counts as "active" on the dashboard. Named here
# rather than written inside the SQL string so that the query stays
# parameterised and the business meaning stays visible.
ACTIVE_CLIENT_STATUS = "Active"


# ---------------------------------------------------------------------------
# The queries
# ---------------------------------------------------------------------------
# Each one returns aggregates only. They are grouped as module constants so
# that the endpoint body reads as a sequence of intentions rather than a wall
# of SQL, and so that the test suite can import and re-run them.

# Four scalar counts in a single round trip. Subqueries in the SELECT list
# are evaluated once each; this is one statement, not four.
TOTALS_SQL = """
    SELECT
        (SELECT COUNT(*) FROM clients)                        AS total_clients,
        (SELECT COUNT(*) FROM clients WHERE client_status = %s) AS active_clients,
        (SELECT COUNT(*) FROM advisors)                       AS total_advisors,
        (SELECT COUNT(*) FROM interactions)                   AS total_interactions
"""

# The three client breakdowns. Each returns the dimension VALUES alongside
# their counts, which is the point: the frontend no longer has to know that
# segments are Standard / Patrimonial / Private Banking. The reference data
# stays in the database, where it belongs.
#
# ORDER BY is two-part everywhere: by count first, then by the label. The
# second key is not decoration - without it, two dimensions holding the same
# count could swap places between two calls and the chart would reshuffle
# for no reason.
CLIENTS_BY_SEGMENT_SQL = """
    SELECT client_segment AS segment,
           COUNT(*)       AS client_count
    FROM clients
    GROUP BY client_segment
    ORDER BY client_count DESC, segment
"""

CLIENTS_BY_COUNTRY_SQL = """
    SELECT country_of_residence AS country,
           COUNT(*)             AS client_count
    FROM clients
    GROUP BY country_of_residence
    ORDER BY client_count DESC, country
"""

CLIENTS_BY_RISK_PROFILE_SQL = """
    SELECT risk_profile AS risk_profile,
           COUNT(*)     AS client_count
    FROM clients
    GROUP BY risk_profile
    ORDER BY client_count DESC, risk_profile
"""

# Advisors ranked by how many clients are assigned to them.
#
# LEFT JOIN, not an inner join: an advisor with no client is a real and
# meaningful case, and an inner join would silently drop them from the
# network. COUNT(c.client_id) rather than COUNT(*) for the same reason -
# COUNT(*) counts the row produced by the join, which is 1 even when the
# right-hand side is all NULLs, and would report an empty advisor as having
# one client.
#
# Only the columns that exist are returned: identifier, name, and the count.
# No revenue, no performance, no score - the CRM holds none of that.
TOP_ADVISORS_SQL = """
    SELECT a.advisor_id,
           a.first_name,
           a.last_name,
           COUNT(c.client_id) AS client_count
    FROM advisors a
    LEFT JOIN clients c ON c.advisor_id = a.advisor_id
    GROUP BY a.advisor_id, a.first_name, a.last_name
    ORDER BY client_count DESC, a.advisor_id
    LIMIT %s
"""


@router.get(
    "/stats/overview",
    summary="Aggregated CRM figures for the dashboard",
    response_description=(
        "Headline counts and client breakdowns, plus the advisors holding "
        "the most clients."
    ),
)
def get_overview(
    top_advisors_limit: int = Query(
        5,
        ge=1,
        le=20,
        description=(
            "How many advisors to return in `top_advisors`, ranked by the "
            "number of clients assigned to them."
        ),
    ),
):
    """Return every aggregate the dashboard needs, in one call.

    This endpoint exists because the alternative was untenable. Ranking the
    advisors from the paginated API meant one request per advisor - 100 round
    trips for a single panel - or downloading all 5,000 clients (about 2.1 MB)
    to group them in the browser. Both push work to the client that the
    database does in milliseconds.

    The breakdowns carry their own labels, so the frontend holds no copy of
    the CRM's reference values.

    Read-only: nothing here writes, and no transaction is opened beyond the
    implicit one each SELECT runs in.
    """
    # One pooled connection for the five statements. Borrowing it once keeps
    # the whole snapshot on the same session instead of spreading it over
    # five checkouts.
    with get_connection() as connection:
        totals = connection.execute(
            TOTALS_SQL,
            (ACTIVE_CLIENT_STATUS,),
        ).fetchone()

        by_segment = connection.execute(CLIENTS_BY_SEGMENT_SQL).fetchall()
        by_country = connection.execute(CLIENTS_BY_COUNTRY_SQL).fetchall()
        by_risk = connection.execute(CLIENTS_BY_RISK_PROFILE_SQL).fetchall()

        top_advisors = connection.execute(
            TOP_ADVISORS_SQL,
            (top_advisors_limit,),
        ).fetchall()

    return {
        # Flat scalars, already shaped for the four KPI tiles.
        "totals": dict(totals),
        "clients_by_segment": [dict(row) for row in by_segment],
        "clients_by_country": [dict(row) for row in by_country],
        "clients_by_risk_profile": [dict(row) for row in by_risk],
        "top_advisors": [dict(row) for row in top_advisors],
    }


# ---------------------------------------------------------------------------
# Reference values for the list filters
# ---------------------------------------------------------------------------
# The list pages offer a filter per business dimension - country, segment,
# risk profile, status, specialisation, language, interaction type, channel.
# To render those as pickers rather than as free-text boxes, the frontend has
# to know which values exist.
#
# It cannot discover them from the paginated endpoints: the only way would be
# to page through all 5,000 clients and all 30,130 interactions and collect
# the distinct values, which is precisely the download this architecture
# exists to avoid. The alternative - writing the values into the React source
# - would put a second, silently diverging copy of the CRM's reference data
# outside the database.
#
# So they are served from here. Note that this ADDS an endpoint rather than
# changing one: every existing route keeps its exact contract.
#
# Each dimension is small (1 to 7 values) and the whole payload is under a
# kilobyte. DISTINCT over the interactions table - the largest scan here -
# was measured at 10.6 ms.

FILTER_VALUES_SQL = {
    "client_country": "SELECT DISTINCT country_of_residence AS value FROM clients ORDER BY value",
    "client_segment": "SELECT DISTINCT client_segment AS value FROM clients ORDER BY value",
    "client_risk_profile": "SELECT DISTINCT risk_profile AS value FROM clients ORDER BY value",
    "client_status": "SELECT DISTINCT client_status AS value FROM clients ORDER BY value",
    "advisor_specialization": "SELECT DISTINCT specialization AS value FROM advisors ORDER BY value",
    "advisor_status": "SELECT DISTINCT advisor_status AS value FROM advisors ORDER BY value",
    "advisor_job_title": "SELECT DISTINCT job_title AS value FROM advisors ORDER BY value",
    # `spoken_languages` is a delimited string - "FR,EN,IT" - so the column
    # holds 7 combinations for only 3 actual languages. The filter matches ONE
    # language, so the string is split and flattened here; returning the raw
    # combinations would offer "FR,EN,IT" as a choice, which is not a language.
    "advisor_spoken_language": """
        SELECT DISTINCT TRIM(language) AS value
        FROM advisors, unnest(string_to_array(spoken_languages, ',')) AS language
        WHERE TRIM(language) <> ''
        ORDER BY value
    """,
    "interaction_type": "SELECT DISTINCT interaction_type AS value FROM interactions ORDER BY value",
    "interaction_channel": "SELECT DISTINCT channel AS value FROM interactions ORDER BY value",
}


@router.get(
    "/stats/filters",
    summary="Distinct values for every filterable dimension",
    response_description=(
        "One sorted list of values per filter, so the UI can offer pickers "
        "without holding a copy of the CRM's reference data."
    ),
)
def get_filter_values():
    """Return the distinct value of every dimension the list endpoints filter on.

    Reference data, not aggregates: no counts here. What this answers is
    "which segments exist", not "how many clients are in each" - that
    question belongs to `/stats/overview`.

    Read-only, and cheap enough to be fetched once per page load.
    """
    values = {}

    with get_connection() as connection:
        for name, sql in FILTER_VALUES_SQL.items():
            rows = connection.execute(sql).fetchall()
            # NULLs would arrive as a blank option that filters nothing.
            values[name] = [row["value"] for row in rows if row["value"] is not None]

    return values

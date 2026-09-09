"""Branch endpoints of the Orialis CRM API."""

from typing import Optional

from fastapi import APIRouter, HTTPException, Path, Query

from ..database import get_connection

# Same prefix as the clients router, but a different tag: Swagger will show
# "clients" and "branches" as two separate sections.
router = APIRouter(prefix="/api/v1", tags=["branches"])


# Maps the public query-parameter name to the real database column, with the
# comparison operator. Only the column names written HERE can reach the SQL
# string, so a caller can never inject a column name.
FILTER_COLUMNS = {
    "country": ("country", "="),
    "branch_status": ("branch_status", "="),
}


def build_where_clause(filters):
    """Build the WHERE clause from the filters that were actually provided.

    Returns the SQL fragment and the list of values to bind to it. Filters
    left empty are skipped, so the clause grows with the request:

        no filter          -> ""                          []
        country=France     -> " WHERE country = ?"        ["France"]
        country=France
        &branch_status=Active
                           -> " WHERE country = ?
                                 AND branch_status = ?"   ["France", "Active"]

    The COLUMN NAME comes from FILTER_COLUMNS, which we control. The VALUE
    never does - it is replaced by a "?" placeholder and passed to SQLite
    separately.
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
    "/branches",
    summary="List branches",
    response_description="Every branch matching the filters, plus their count.",
)
def list_branches(
    country: Optional[str] = Query(
        None,
        description=(
            "Filter on the country the branch is located in. "
            "One of: France, Switzerland, Belgium, Italy."
        ),
    ),
    branch_status: Optional[str] = Query(
        None,
        description="Filter on the branch status, e.g. Active.",
    ),
):
    """Return the Orialis branches, with optional filters.

    The network holds only a handful of branches, so this endpoint returns
    them all at once: there is no pagination. `total_records` is counted from
    the rows actually returned, never hardcoded.

    Database columns are returned as they are, with no renaming and no
    transformation.
    """
    where_sql, where_values = build_where_clause({
        "country": country,
        "branch_status": branch_status,
    })

    with get_connection() as connection:
        rows = connection.execute(
            f"SELECT * FROM branches{where_sql} ORDER BY branch_id",
            where_values,
        ).fetchall()

    branches = [dict(row) for row in rows]

    return {
        "data": branches,
        # Counted from the result itself. Since every matching branch is
        # returned, no separate COUNT(*) query is needed here.
        "total_records": len(branches),
    }


@router.get(
    "/branches/{branch_id}",
    summary="Get one branch by id",
    response_description="The branch matching the requested id.",
    responses={404: {"description": "No branch exists with this id."}},
)
def get_branch(
    branch_id: str = Path(
        ...,
        description="Identifier of the branch to retrieve, e.g. BR001.",
    ),
):
    """Return a single branch, identified by its `branch_id`.

    This endpoint addresses ONE precise resource, so it returns the branch
    object itself. An unknown id is an error, not an empty result, and
    answers 404.
    """
    with get_connection() as connection:
        # The id is a bound parameter, never glued into the SQL string.
        # fetchone() returns a single row, or None if nothing matched.
        row = connection.execute(
            "SELECT * FROM branches WHERE branch_id = ?",
            (branch_id,),
        ).fetchone()

    if row is None:
        raise HTTPException(status_code=404, detail="Branch not found")

    return dict(row)

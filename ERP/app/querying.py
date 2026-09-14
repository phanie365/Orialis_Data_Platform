"""
Filtering, ordering and pagination, shared by every ERP resource router.

The CRM repeats this logic in each of its routers. Gathering it here is the
one deliberate departure: four resources with four near-identical copies of a
WHERE builder is four places for the same bug to hide, and the incremental
contract below is far too important to be restated four times and drift.

---------------------------------------------------------------------------
THE INCREMENTAL CONTRACT - READ THIS BEFORE BUILDING AN INGESTION LAYER
---------------------------------------------------------------------------

Every mutable ERP table carries `updated_at`, and every list endpoint accepts
`updated_since`. The semantics are:

    updated_at >= updated_since          INCLUSIVE lower bound

This delivers **at-least-once**, never exactly-once, and the choice is
deliberate rather than a rounding detail:

    A row sitting exactly ON the watermark is returned AGAIN on the next
    extraction. Re-reading a row is harmless when the downstream load
    deduplicates on the primary key. Missing one is irreversible.

The effect is not theoretical here. The simulator issues payments in
campaigns, twice a week, so hundreds of rows share a `created_at` to the
minute; the CRM measured 337 rows on a single timestamp in its own data. An
exclusive bound (`>`) would drop every row that shared the watermark's
timestamp with the last row of the previous page.

**The ingestion layer is responsible for idempotence and deduplication.**
This API does not solve it, does not pretend to, and does not expose a cursor
or a change log. Upsert on the primary key downstream and the duplicates cost
nothing.

---------------------------------------------------------------------------
WHY THE ORDER CHANGES WHEN YOU EXTRACT
---------------------------------------------------------------------------

    no updated_since   ->  ORDER BY <primary key>
    updated_since      ->  ORDER BY updated_at, <primary key>

Both are total orders, so pagination is stable either way. But the second is
not a nicety - it is what keeps the at-least-once promise true while data is
moving underneath a multi-page extraction.

Consider a consumer paging through with `updated_since`, while the simulator
updates rows:

    ordered by primary key   a row you have ALREADY passed gets updated. Its
                             position does not change, so you never see it
                             again. The change is LOST. That is at-most-once.

    ordered by updated_at    the same row moves to the END of the result set,
                             because its `updated_at` just became the largest
                             in the table. You see it again. At-least-once
                             holds.

The tie-break on the primary key is what makes the second order total: with
hundreds of rows sharing a timestamp, `ORDER BY updated_at` alone lets
PostgreSQL return them in any order it likes, and a row could then appear on
two pages, or on none.
"""

import math
from typing import Any

# Hop-by-hop of SQL: the operators a filter is allowed to use. Anything not
# in this map cannot reach a query.
ALLOWED_OPERATORS = ("=", ">=", "<=", "<", ">", "IS NOT NULL", "IS NULL")


def build_where_clause(filter_columns: dict, filters: dict):
    """Build a WHERE clause from the filters that were actually provided.

    Returns `(sql_fragment, values)`.

    `filter_columns` maps the PUBLIC parameter name to `(column, operator)`.
    That indirection is two things at once:

      - a vocabulary. The API speaks "currency" and "status" while the table
        keeps `currency_code` and `invoice_approval_status`; callers never
        need to know the column names, and the columns can be renamed without
        breaking the contract.

      - a SAFETY BOUNDARY. Only column names written in that dictionary can
        ever reach the SQL string. A caller cannot inject one.

    Note precisely what is and is not interpolated: the COLUMN comes from the
    dictionary, which we control. The VALUE never does - it becomes a `%s`
    placeholder and is sent to the server separately.
    """
    conditions = []
    values = []

    for name, value in filters.items():
        if value is None:
            continue
        column, operator = filter_columns[name]
        if operator not in ALLOWED_OPERATORS:
            raise ValueError(f"operateur non autorise : {operator}")
        conditions.append(f"{column} {operator} %s")
        values.append(value)

    if not conditions:
        return "", []

    return " WHERE " + " AND ".join(conditions), values


def order_clause(primary_key: str, incremental: bool) -> str:
    """The ORDER BY that keeps pagination stable - see the module docstring.

    Always a TOTAL order. Without the primary-key tie-break, rows sharing an
    `updated_at` could be returned in a different order on two calls, and a
    row would appear twice or not at all across page boundaries.
    """
    if incremental:
        return f" ORDER BY updated_at, {primary_key}"
    return f" ORDER BY {primary_key}"


def list_page(connection, table: str, primary_key: str, filter_columns: dict,
              filters: dict, page: int, page_size: int,
              columns: str = "*") -> dict[str, Any]:
    """One page of a resource, plus pagination metadata.

    Two statements, both executed by the server: a COUNT that transfers no
    row, and a SELECT that transfers only the requested page. Nothing loads
    the table into Python.
    """
    where_sql, values = build_where_clause(filter_columns, filters)
    incremental = filters.get("updated_since") is not None
    order_sql = order_clause(primary_key, incremental)
    offset = (page - 1) * page_size

    # How many rows match the filters, ignoring pagination. The aggregate
    # gets an explicit alias: rows come back as dictionaries, so there is no
    # row[0] to read.
    total_records = connection.execute(
        f"SELECT COUNT(*) AS total FROM {table}{where_sql}", values
    ).fetchone()["total"]

    rows = connection.execute(
        f"SELECT {columns} FROM {table}{where_sql}{order_sql} "
        f"LIMIT %s OFFSET %s",
        values + [page_size, offset],
    ).fetchall()

    return {
        "data": [dict(row) for row in rows],
        "pagination": {
            "page": page,
            "page_size": page_size,
            "total_records": total_records,
            # Integer ceiling: 8000 records of 100 -> 80 pages, 8001 -> 81.
            # No match means 0 records, 0 pages, and still an HTTP 200.
            "total_pages": math.ceil(total_records / page_size),
            # Stated on every response so that a consumer reading only the
            # payload still learns which order it is paging through, and
            # therefore which guarantee it is relying on.
            "ordering": ("updated_at, " + primary_key) if incremental
                        else primary_key,
            "extraction_semantics": "at-least-once" if incremental else None,
        },
    }

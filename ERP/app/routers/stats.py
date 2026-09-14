"""Aggregate endpoints of the Orialis ERP API.

Deliberately separate from the resource routers. Those expose ERP RECORDS - an
invoice, a payment - and page through them. This one exposes NUMBERS ABOUT
those records and returns no operational row at all.

Keeping the two apart matters more than it looks. An endpoint answering with
both totals and a list of recent invoices would force every caller that wants
a figure to also pay for rows it did not ask for.

Everything here is computed by PostgreSQL with COUNT, SUM and GROUP BY. No
table is loaded into Python to be aggregated in a loop: the 8 600 invoices
never leave the database - only the resulting handful of numbers do. On this
dataset the whole overview is four round trips and a few dozen rows.
"""

from fastapi import APIRouter

from ..database import get_connection

router = APIRouter(prefix="/api/v1", tags=["stats"])


# ---------------------------------------------------------------------------
# The queries
# ---------------------------------------------------------------------------
# Grouped as module constants so the endpoint reads as a sequence of
# intentions rather than a wall of SQL, and so the tests can import and
# re-run them.

# Scalar counts and sums in ONE round trip. Each subquery in the SELECT list
# is evaluated once; this is one statement, not seven.
#
# The amounts are in EUR because `gross_amount_eur` is the group-currency
# figure FROZEN on each document at the rate of its own month. Summing
# `gross_amount` across currencies would add euros to francs.
TOTALS_SQL = """
    SELECT
        (SELECT COUNT(*) FROM suppliers)                                AS total_suppliers,
        (SELECT COUNT(*) FROM suppliers WHERE supplier_status = 'Active') AS active_suppliers,
        (SELECT COUNT(*) FROM cost_centers)                             AS total_cost_centers,
        (SELECT COUNT(*) FROM supplier_invoices)                        AS total_invoices,
        (SELECT COUNT(*) FROM payments)                                 AS total_payments,
        (SELECT COALESCE(SUM(gross_amount_eur), 0) FROM supplier_invoices
          WHERE invoice_approval_status = 'Approved')                   AS approved_amount_eur,
        (SELECT COALESCE(SUM(
             (gross_amount_eur - paid_amount * fx_rate_to_eur)), 0)
           FROM supplier_invoices
          WHERE invoice_approval_status = 'Approved'
            AND invoice_payment_status <> 'Paid')                       AS outstanding_amount_eur
"""

# The two invoice status axes, each with its own count and amount. Two
# breakdowns rather than one cross-tab, because the axes are independent and
# a consumer almost always wants one of them, not the 15-cell matrix.
#
# ORDER BY is two-part everywhere: by count first, then by label. The second
# key is not decoration - two statuses holding the same count could otherwise
# swap places between two calls and a chart would reshuffle for no reason.
APPROVAL_BREAKDOWN_SQL = """
    SELECT invoice_approval_status AS status,
           COUNT(*) AS invoice_count,
           ROUND(COALESCE(SUM(gross_amount_eur), 0), 2) AS amount_eur
    FROM supplier_invoices
    GROUP BY invoice_approval_status
    ORDER BY invoice_count DESC, status
"""

PAYMENT_STATUS_BREAKDOWN_SQL = """
    SELECT invoice_payment_status AS status,
           COUNT(*) AS invoice_count,
           ROUND(COALESCE(SUM(gross_amount_eur), 0), 2) AS amount_eur
    FROM supplier_invoices
    GROUP BY invoice_payment_status
    ORDER BY invoice_count DESC, status
"""

CURRENCY_BREAKDOWN_SQL = """
    SELECT currency_code AS currency,
           COUNT(*) AS invoice_count,
           ROUND(COALESCE(SUM(gross_amount_eur), 0), 2) AS amount_eur
    FROM supplier_invoices
    GROUP BY currency_code
    ORDER BY invoice_count DESC, currency
"""

# Spend by cost centre, joined to the name so the caller does not have to
# resolve CC-FR-001 itself. LIMIT is applied by the server.
TOP_COST_CENTRES_SQL = """
    SELECT i.cost_center_id, c.cost_center_name, c.cost_center_type,
           COUNT(*) AS invoice_count,
           ROUND(COALESCE(SUM(i.gross_amount_eur), 0), 2) AS amount_eur
    FROM supplier_invoices i
    JOIN cost_centers c ON c.cost_center_id = i.cost_center_id
    WHERE i.invoice_approval_status = 'Approved'
    GROUP BY i.cost_center_id, c.cost_center_name, c.cost_center_type
    ORDER BY amount_eur DESC, i.cost_center_id
    LIMIT 10
"""

# The Pareto tail, which is the single most characteristic fact about this
# supplier base: a handful of vendors issue most of the paper.
TOP_SUPPLIERS_SQL = """
    SELECT i.supplier_id, s.supplier_name, s.supplier_category,
           COUNT(*) AS invoice_count,
           ROUND(COALESCE(SUM(i.gross_amount_eur), 0), 2) AS amount_eur
    FROM supplier_invoices i
    JOIN suppliers s ON s.supplier_id = i.supplier_id
    GROUP BY i.supplier_id, s.supplier_name, s.supplier_category
    ORDER BY invoice_count DESC, i.supplier_id
    LIMIT 10
"""

PAYMENT_BREAKDOWN_SQL = """
    SELECT payment_status AS status,
           COUNT(*) AS payment_count,
           ROUND(COALESCE(SUM(payment_amount), 0), 2) AS amount
    FROM payments
    GROUP BY payment_status
    ORDER BY payment_count DESC, status
"""


@router.get(
    "/stats/overview",
    summary="Every aggregate a dashboard needs, in one call",
)
def overview():
    """Counts and amounts about the ERP, computed entirely in SQL.

    Returns no invoice, no payment and no supplier row - only numbers. A
    caller that wants records asks a resource endpoint for them.
    """
    with get_connection() as connection:
        totals = connection.execute(TOTALS_SQL).fetchone()
        approval = connection.execute(APPROVAL_BREAKDOWN_SQL).fetchall()
        payment_status = connection.execute(PAYMENT_STATUS_BREAKDOWN_SQL).fetchall()
        currency = connection.execute(CURRENCY_BREAKDOWN_SQL).fetchall()
        cost_centres = connection.execute(TOP_COST_CENTRES_SQL).fetchall()
        suppliers = connection.execute(TOP_SUPPLIERS_SQL).fetchall()
        payments = connection.execute(PAYMENT_BREAKDOWN_SQL).fetchall()

    return {
        "totals": {key: _number(value) for key, value in dict(totals).items()},
        "invoices_by_approval_status": [dict(row) for row in approval],
        "invoices_by_payment_status": [dict(row) for row in payment_status],
        "invoices_by_currency": [dict(row) for row in currency],
        "top_cost_centers": [dict(row) for row in cost_centres],
        "top_suppliers": [dict(row) for row in suppliers],
        "payments_by_status": [dict(row) for row in payments],
    }


def _number(value):
    """Round the money subqueries; leave the counts alone."""
    return round(float(value), 2) if isinstance(value, float) else value


# ---------------------------------------------------------------------------
# Filter values
# ---------------------------------------------------------------------------
# The distinct values of every filterable dimension, so a consumer can build
# pickers without hard-coding the reference data. Business values stay in the
# database, where they belong - the day a supplier category is added, the
# picker learns about it without a release.
FILTER_VALUES_SQL = {
    "supplier_status": "SELECT DISTINCT supplier_status AS v FROM suppliers ORDER BY v",
    "supplier_category": "SELECT DISTINCT supplier_category AS v FROM suppliers ORDER BY v",
    "supplier_country": "SELECT DISTINCT country_code AS v FROM suppliers ORDER BY v",
    "currency": "SELECT DISTINCT currency_code AS v FROM supplier_invoices ORDER BY v",
    "invoice_type": "SELECT DISTINCT invoice_type AS v FROM supplier_invoices ORDER BY v",
    "invoice_approval_status":
        "SELECT DISTINCT invoice_approval_status AS v FROM supplier_invoices ORDER BY v",
    "invoice_payment_status":
        "SELECT DISTINCT invoice_payment_status AS v FROM supplier_invoices ORDER BY v",
    "tax_treatment": "SELECT DISTINCT tax_treatment AS v FROM supplier_invoices ORDER BY v",
    "payment_status": "SELECT DISTINCT payment_status AS v FROM payments ORDER BY v",
    "payment_method": "SELECT DISTINCT payment_method AS v FROM payments ORDER BY v",
    "cost_center_type": "SELECT DISTINCT cost_center_type AS v FROM cost_centers ORDER BY v",
    "cost_center_country":
        "SELECT DISTINCT country_code AS v FROM cost_centers WHERE country_code IS NOT NULL ORDER BY v",
    "allocation_status":
        "SELECT DISTINCT allocation_status AS v FROM payment_allocations ORDER BY v",
}


@router.get(
    "/stats/filters",
    summary="The distinct values of every filterable dimension",
)
def filter_values():
    """Reference values for building pickers, read from the data itself."""
    result = {}
    with get_connection() as connection:
        for name, sql in FILTER_VALUES_SQL.items():
            result[name] = [row["v"] for row in connection.execute(sql).fetchall()]
    return result

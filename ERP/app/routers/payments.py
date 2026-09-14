"""Payment and allocation endpoints of the Orialis ERP API.

---------------------------------------------------------------------------
WHY `payment_allocations` IS EXPOSED
---------------------------------------------------------------------------

The specification asked for a justification before making it a public
resource. Here it is, and it comes down to one fact:

    Without the allocations, `payments` and `invoices` CANNOT BE JOINED.

There is no `invoice_id` on a payment and no `payment_id` on an invoice, and
that is not an oversight - the relationship is genuinely many-to-many. One
invoice can be settled by several transfers (a partial payment, then the
balance); one transfer can settle several invoices of the same supplier (a
batch run, with a credit note netted off as a negative line). The allocation
is the only place that relationship exists.

A consumer given only `payments` and `invoices` could see that 8 000 invoices
exist and that 8 000 payments were made, and would have no way to say which
settled which. Every question worth asking of an accounts-payable system -
what is still outstanding, how late did we pay this supplier, which invoices
did this transfer cover - needs the link.

Two further reasons:

  - `invoices.paid_amount` is a DERIVED aggregate. Exposing the total while
    withholding the rows it is computed from means asking a consumer to trust
    a number it cannot audit.

  - a failed transfer keeps its allocations, and they count for nothing. That
    distinction is invisible unless the allocation and the payment status can
    be read together.

So it is exposed twice, on purpose:

    /payments/{id}/allocations    the natural reading - what did this
                                  transfer settle?
    /invoices/{id}/allocations    the mirror - how was this invoice paid?
    /allocations                  the flat, paginated, incremental list an
                                  ingestion layer needs to extract the table

The sub-resources alone would not do: a data platform has to be able to
extract the whole table incrementally, not walk it one parent at a time.
"""

from datetime import date, datetime
from typing import Optional

from fastapi import APIRouter, HTTPException, Path, Query

from ..database import get_connection
from ..querying import list_page

router = APIRouter(prefix="/api/v1", tags=["payments"])

TABLE = "payments"
PRIMARY_KEY = "payment_id"

FILTER_COLUMNS = {
    "supplier_id": ("supplier_id", "="),
    "payment_status": ("payment_status", "="),
    "payment_method": ("payment_method", "="),
    "currency": ("currency_code", "="),
    "payment_date_from": ("payment_date", ">="),
    "payment_date_to": ("payment_date", "<="),
    "min_amount": ("payment_amount", ">="),
    "updated_since": ("updated_at", ">="),
}

ALLOCATION_TABLE = "payment_allocations"
ALLOCATION_KEY = "allocation_id"

ALLOCATION_FILTERS = {
    "payment_id": ("payment_id", "="),
    "invoice_id": ("invoice_id", "="),
    "supplier_id": ("supplier_id", "="),
    "allocation_status": ("allocation_status", "="),
    "currency": ("currency_code", "="),
    "updated_since": ("updated_at", ">="),
}


@router.get(
    "/payments",
    summary="List payments",
    response_description="A page of payments, plus pagination metadata.",
)
def list_payments(
    page: int = Query(1, ge=1, description="Page number, starting at 1."),
    page_size: int = Query(100, ge=1, le=500,
                           description="Rows per page (1 to 500)."),
    supplier_id: Optional[str] = Query(
        None, description="Payments to this supplier, e.g. SUP-00042."),
    payment_status: Optional[str] = Query(
        None,
        description=(
            "Initiated, Executed, Failed or Cancelled. Only EXECUTED "
            "payments contribute to an invoice's paid_amount - a transfer "
            "in flight or rejected counts for nothing.")),
    payment_method: Optional[str] = Query(
        None, description="SEPA Credit Transfer, SWIFT, Direct Debit, Card, "
                          "Cheque."),
    currency: Optional[str] = Query(None, description="EUR or CHF."),
    payment_date_from: Optional[date] = Query(
        None, description="Ordered on or after this date."),
    payment_date_to: Optional[date] = Query(
        None, description="Ordered on or before this date."),
    min_amount: Optional[float] = Query(
        None, description="Only payments of at least this amount."),
    updated_since: Optional[datetime] = Query(
        None,
        description=(
            "Inclusive lower bound on updated_at. Payments move often - a "
            "transfer is Initiated on a Tuesday and Executed two days later "
            "- so this table changes more than any other.")),
):
    """A page of payments, with optional filters.

    Payments are issued in CAMPAIGNS, on Tuesdays and Fridays. Hundreds of
    rows therefore share a `created_at` to the minute, which is exactly why
    the incremental bound is inclusive and the ordering has a tie-break.
    """
    with get_connection() as connection:
        return list_page(
            connection, TABLE, PRIMARY_KEY, FILTER_COLUMNS,
            {
                "supplier_id": supplier_id,
                "payment_status": payment_status,
                "payment_method": payment_method,
                "currency": currency,
                "payment_date_from": payment_date_from,
                "payment_date_to": payment_date_to,
                "min_amount": min_amount,
                "updated_since": updated_since,
            },
            page, page_size,
        )


@router.get(
    "/payments/{payment_id}",
    summary="Get one payment by id",
    responses={404: {"description": "No payment exists with this id."}},
)
def get_payment(
    payment_id: str = Path(..., description="e.g. PAY-2026-01234."),
):
    """One payment, addressed directly. 404 when the id is unknown."""
    with get_connection() as connection:
        row = connection.execute(
            f"SELECT * FROM {TABLE} WHERE {PRIMARY_KEY} = %s",
            (payment_id,),
        ).fetchone()

    if row is None:
        raise HTTPException(status_code=404, detail="Payment not found")
    return dict(row)


@router.get(
    "/payments/{payment_id}/allocations",
    summary="The invoices one payment settles",
    responses={404: {"description": "No payment exists with this id."}},
)
def get_payment_allocations(
    payment_id: str = Path(..., description="e.g. PAY-2026-01234."),
):
    """What this transfer paid, line by line.

    A batched payment covers several invoices of one supplier, and may carry
    a NEGATIVE line: a credit note netted off the total. That is why the
    payment amount is not simply the sum of a set of invoices.
    """
    with get_connection() as connection:
        exists = connection.execute(
            f"SELECT 1 FROM {TABLE} WHERE {PRIMARY_KEY} = %s", (payment_id,)
        ).fetchone()
        if exists is None:
            raise HTTPException(status_code=404, detail="Payment not found")

        rows = connection.execute(
            """
            SELECT pa.*, i.invoice_type, i.gross_amount, i.due_date,
                   i.invoice_payment_status
            FROM payment_allocations pa
            JOIN supplier_invoices i ON i.invoice_id = pa.invoice_id
            WHERE pa.payment_id = %s
            ORDER BY pa.created_at, pa.allocation_id
            """,
            (payment_id,),
        ).fetchall()

    return {"payment_id": payment_id, "data": [dict(row) for row in rows]}


@router.get(
    "/allocations",
    summary="List payment allocations",
    response_description="A page of allocations, plus pagination metadata.",
)
def list_allocations(
    page: int = Query(1, ge=1, description="Page number, starting at 1."),
    page_size: int = Query(100, ge=1, le=500,
                           description="Rows per page (1 to 500)."),
    payment_id: Optional[str] = Query(None, description="e.g. PAY-2026-01234."),
    invoice_id: Optional[str] = Query(None, description="e.g. INV-2026-01234."),
    supplier_id: Optional[str] = Query(None, description="e.g. SUP-00042."),
    allocation_status: Optional[str] = Query(
        None,
        description=(
            "Active or Cancelled. A cancelled allocation is a mis-keyed "
            "match that was corrected: the row is KEPT, and points at its "
            "replacement through replaced_by_allocation_id. The ERP never "
            "deletes.")),
    currency: Optional[str] = Query(None, description="EUR or CHF."),
    updated_since: Optional[datetime] = Query(
        None, description="Inclusive lower bound on updated_at."),
):
    """The flat allocation list, for bulk and incremental extraction.

    The two sub-resources above answer "what did this settle?"; this one
    exists so that an ingestion layer can extract the table itself, which no
    amount of walking parents would achieve.
    """
    with get_connection() as connection:
        return list_page(
            connection, ALLOCATION_TABLE, ALLOCATION_KEY, ALLOCATION_FILTERS,
            {
                "payment_id": payment_id,
                "invoice_id": invoice_id,
                "supplier_id": supplier_id,
                "allocation_status": allocation_status,
                "currency": currency,
                "updated_since": updated_since,
            },
            page, page_size,
        )


@router.get(
    "/allocations/{allocation_id}",
    summary="Get one allocation by id",
    responses={404: {"description": "No allocation exists with this id."}},
)
def get_allocation(
    allocation_id: str = Path(..., description="e.g. ALC-2026-01234."),
):
    """One allocation, addressed directly. 404 when the id is unknown."""
    with get_connection() as connection:
        row = connection.execute(
            f"SELECT * FROM {ALLOCATION_TABLE} WHERE {ALLOCATION_KEY} = %s",
            (allocation_id,),
        ).fetchone()

    if row is None:
        raise HTTPException(status_code=404, detail="Allocation not found")
    return dict(row)

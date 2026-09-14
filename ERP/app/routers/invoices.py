"""Supplier-invoice endpoints of the Orialis ERP API.

The route is `/invoices`, the table is `supplier_invoices`. The shorter path
is the one callers will type; the longer table name stays where it earns its
keep - in SQL, where "invoices" would be ambiguous between what Orialis owes
and what Orialis bills.

---------------------------------------------------------------------------
TWO STATUS FILTERS, NOT ONE
---------------------------------------------------------------------------

`invoice_approval_status` and `invoice_payment_status` are independent axes,
and the API exposes them as two independent filters because that is what they
are. "Approved and unpaid" is the normal state of every invoice not yet due -
the single most common question an accounts-payable consumer asks - and it is
expressible only because the two are separate:

    ?invoice_approval_status=Approved&invoice_payment_status=Unpaid

---------------------------------------------------------------------------
WHY THERE IS NO `overdue` FLAG
---------------------------------------------------------------------------

An overdue invoice is one past its due date and not paid. The tempting filter
is `?overdue=true`, and it is refused on purpose: it would have to compare
against a "today", and this API has no business owning one.

The data runs on SIMULATED time - the seed ends on 2026-09-30 and the
simulator carries it forward - so `CURRENT_DATE` would be an arbitrary point
somewhere in the middle of the history, and the answer would change meaning
depending on when the process happens to run. Instead the caller states the
date it means:

    ?due_before=2026-10-01&invoice_payment_status=Unpaid

Slightly longer to type, and it says exactly what it computes.
"""

from datetime import date, datetime
from typing import Optional

from fastapi import APIRouter, HTTPException, Path, Query

from ..database import get_connection
from ..querying import list_page

router = APIRouter(prefix="/api/v1", tags=["invoices"])

TABLE = "supplier_invoices"
PRIMARY_KEY = "invoice_id"

FILTER_COLUMNS = {
    "supplier_id": ("supplier_id", "="),
    "cost_center_id": ("cost_center_id", "="),
    "invoice_type": ("invoice_type", "="),
    "invoice_approval_status": ("invoice_approval_status", "="),
    "invoice_payment_status": ("invoice_payment_status", "="),
    "currency": ("currency_code", "="),
    "tax_treatment": ("tax_treatment", "="),
    "invoice_date_from": ("invoice_date", ">="),
    "invoice_date_to": ("invoice_date", "<="),
    "due_before": ("due_date", "<"),
    "due_after": ("due_date", ">="),
    "min_gross_amount_eur": ("gross_amount_eur", ">="),
    "updated_since": ("updated_at", ">="),
}


@router.get(
    "/invoices",
    summary="List supplier invoices",
    response_description="A page of invoices, plus pagination metadata.",
)
def list_invoices(
    page: int = Query(1, ge=1, description="Page number, starting at 1."),
    page_size: int = Query(100, ge=1, le=500,
                           description="Rows per page (1 to 500)."),
    supplier_id: Optional[str] = Query(
        None, description="Invoices of this supplier, e.g. SUP-00042."),
    cost_center_id: Optional[str] = Query(
        None, description="Invoices charged to this cost centre."),
    invoice_type: Optional[str] = Query(
        None,
        description=(
            "Standard or Credit Note. A credit note carries NEGATIVE "
            "amounts - it is a reduction of what is owed, not a separate "
            "kind of document.")),
    invoice_approval_status: Optional[str] = Query(
        None,
        description=("Draft, Pending Approval, Approved, Rejected or "
                     "Cancelled. Independent of the payment status.")),
    invoice_payment_status: Optional[str] = Query(
        None, description="Unpaid, Partially Paid or Paid."),
    currency: Optional[str] = Query(None, description="EUR or CHF."),
    tax_treatment: Optional[str] = Query(
        None,
        description=("Standard, Reverse Charge, Exempt or Out of Scope. "
                     "Three of the four carry a 0% rate for different "
                     "reasons, which is why the treatment is stored "
                     "alongside the rate.")),
    invoice_date_from: Optional[date] = Query(
        None, description="Issued on or after this date."),
    invoice_date_to: Optional[date] = Query(
        None, description="Issued on or before this date."),
    due_before: Optional[date] = Query(
        None,
        description=("Due strictly before this date. Combine with "
                     "invoice_payment_status=Unpaid to list overdue items "
                     "as of a date you choose.")),
    due_after: Optional[date] = Query(
        None, description="Due on or after this date."),
    min_gross_amount_eur: Optional[float] = Query(
        None, description="Only invoices of at least this amount, in EUR."),
    updated_since: Optional[datetime] = Query(
        None,
        description=(
            "Inclusive lower bound on updated_at. Extraction is "
            "at-least-once; the ingestion layer deduplicates. When this is "
            "set, results are ordered by (updated_at, invoice_id) so that a "
            "row updated mid-extraction moves to the end rather than being "
            "missed.")),
):
    """A page of supplier invoices, with optional filters."""
    with get_connection() as connection:
        return list_page(
            connection, TABLE, PRIMARY_KEY, FILTER_COLUMNS,
            {
                "supplier_id": supplier_id,
                "cost_center_id": cost_center_id,
                "invoice_type": invoice_type,
                "invoice_approval_status": invoice_approval_status,
                "invoice_payment_status": invoice_payment_status,
                "currency": currency,
                "tax_treatment": tax_treatment,
                "invoice_date_from": invoice_date_from,
                "invoice_date_to": invoice_date_to,
                "due_before": due_before,
                "due_after": due_after,
                "min_gross_amount_eur": min_gross_amount_eur,
                "updated_since": updated_since,
            },
            page, page_size,
        )


@router.get(
    "/invoices/{invoice_id}",
    summary="Get one invoice by id",
    responses={404: {"description": "No invoice exists with this id."}},
)
def get_invoice(
    invoice_id: str = Path(..., description="e.g. INV-2026-01234."),
):
    """One invoice, addressed directly. 404 when the id is unknown."""
    with get_connection() as connection:
        row = connection.execute(
            f"SELECT * FROM {TABLE} WHERE {PRIMARY_KEY} = %s",
            (invoice_id,),
        ).fetchone()

    if row is None:
        raise HTTPException(status_code=404, detail="Invoice not found")
    return dict(row)


@router.get(
    "/invoices/{invoice_id}/allocations",
    summary="The allocations that settle one invoice",
    responses={404: {"description": "No invoice exists with this id."}},
)
def get_invoice_allocations(
    invoice_id: str = Path(..., description="e.g. INV-2026-01234."),
):
    """How this invoice was paid, line by line.

    This is what makes `paid_amount` auditable. The figure on the invoice is
    a derived aggregate:

        paid_amount = SUM(allocated_amount)
                      WHERE allocation_status = 'Active'
                        AND payments.payment_status = 'Executed'

    Reading the invoice alone tells you the total; reading this tells you how
    it was arrived at - including the transfers that FAILED and therefore
    count for nothing, and the allocations that were mis-keyed, cancelled and
    replaced. Those rows are kept for ever, so the history of a correction
    stays readable.

    The payment status is joined in precisely because an allocation on its
    own does not tell you whether the money moved.
    """
    with get_connection() as connection:
        exists = connection.execute(
            f"SELECT 1 FROM {TABLE} WHERE {PRIMARY_KEY} = %s", (invoice_id,)
        ).fetchone()
        if exists is None:
            raise HTTPException(status_code=404, detail="Invoice not found")

        rows = connection.execute(
            """
            SELECT pa.*, p.payment_status, p.payment_date, p.payment_method
            FROM payment_allocations pa
            JOIN payments p ON p.payment_id = pa.payment_id
            WHERE pa.invoice_id = %s
            ORDER BY pa.created_at, pa.allocation_id
            """,
            (invoice_id,),
        ).fetchall()

    return {"invoice_id": invoice_id, "data": [dict(row) for row in rows]}

"""Supplier endpoints of the Orialis ERP API.

---------------------------------------------------------------------------
A NOTE ON `iban_masked`
---------------------------------------------------------------------------

The column is returned as stored, and that is safe because of a decision
taken two steps earlier: synthetic IBANs are masked AT SOURCE. The full
number never exists in the database - a CHECK constraint refuses anything
that is not of the form `FR76**********3021`.

So there is nothing for this API to redact. That is the point of masking at
the source rather than at the edge: an export, a dump, a `SELECT *` and this
endpoint all become safe at once, instead of each needing to remember.
"""

from datetime import date, datetime
from typing import Optional

from fastapi import APIRouter, HTTPException, Path, Query

from ..database import get_connection
from ..querying import list_page

router = APIRouter(prefix="/api/v1", tags=["suppliers"])

TABLE = "suppliers"
PRIMARY_KEY = "supplier_id"

FILTER_COLUMNS = {
    "supplier_status": ("supplier_status", "="),
    "supplier_category": ("supplier_category", "="),
    "country": ("country_code", "="),
    "currency": ("default_currency", "="),
    "payment_terms_days": ("payment_terms_days", "="),
    "onboarded_from": ("onboarded_on", ">="),
    "onboarded_to": ("onboarded_on", "<="),
    "updated_since": ("updated_at", ">="),
}


@router.get(
    "/suppliers",
    summary="List suppliers",
    response_description="A page of suppliers, plus pagination metadata.",
)
def list_suppliers(
    page: int = Query(1, ge=1, description="Page number, starting at 1."),
    page_size: int = Query(100, ge=1, le=200,
                           description="Rows per page (1 to 200)."),
    supplier_status: Optional[str] = Query(
        None,
        description=(
            "Active, Inactive or Blocked. The distinction matters: an "
            "Inactive supplier is still paid what it is owed, a Blocked one "
            "is not paid at all.")),
    supplier_category: Optional[str] = Query(
        None, description="e.g. Market Data, IT & Software, Travel."),
    country: Optional[str] = Query(
        None,
        description=(
            "ISO country code. Suppliers are NOT limited to the four Orialis "
            "countries: market data comes from GB and US, holdings from LU.")),
    currency: Optional[str] = Query(None, description="EUR or CHF."),
    payment_terms_days: Optional[int] = Query(
        None, description="0, 15, 30, 45, 60 or 90."),
    onboarded_from: Optional[date] = Query(
        None, description="Onboarded on or after this date."),
    onboarded_to: Optional[date] = Query(
        None, description="Onboarded on or before this date."),
    updated_since: Optional[datetime] = Query(
        None,
        description=(
            "Inclusive lower bound on updated_at. Extraction is "
            "at-least-once; the ingestion layer deduplicates.")),
):
    """A page of suppliers, with optional filters."""
    with get_connection() as connection:
        return list_page(
            connection, TABLE, PRIMARY_KEY, FILTER_COLUMNS,
            {
                "supplier_status": supplier_status,
                "supplier_category": supplier_category,
                "country": country,
                "currency": currency,
                "payment_terms_days": payment_terms_days,
                "onboarded_from": onboarded_from,
                "onboarded_to": onboarded_to,
                "updated_since": updated_since,
            },
            page, page_size,
        )


@router.get(
    "/suppliers/{supplier_id}",
    summary="Get one supplier by id",
    responses={404: {"description": "No supplier exists with this id."}},
)
def get_supplier(
    supplier_id: str = Path(..., description="e.g. SUP-00042."),
):
    """One supplier, addressed directly. 404 when the id is unknown."""
    with get_connection() as connection:
        row = connection.execute(
            f"SELECT * FROM {TABLE} WHERE {PRIMARY_KEY} = %s",
            (supplier_id,),
        ).fetchone()

    if row is None:
        raise HTTPException(status_code=404, detail="Supplier not found")
    return dict(row)

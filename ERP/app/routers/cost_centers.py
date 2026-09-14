"""Cost-centre endpoints of the Orialis ERP API.

The analytical axis: every invoice and every expense is charged to one of
these. Small (25 rows) but referenced everywhere, so it is the first thing an
ingestion layer needs.

The URL says `cost-centers` while the table says `cost_centers`. Hyphens are
the convention in a path; the underscore stays where it belongs, in SQL.
"""

from datetime import datetime
from typing import Optional

from fastapi import APIRouter, HTTPException, Path, Query

from ..database import get_connection
from ..querying import list_page

router = APIRouter(prefix="/api/v1", tags=["cost centres"])

TABLE = "cost_centers"
PRIMARY_KEY = "cost_center_id"

FILTER_COLUMNS = {
    "cost_center_type": ("cost_center_type", "="),
    "country": ("country_code", "="),
    "currency": ("default_currency", "="),
    "cost_center_status": ("cost_center_status", "="),
    "parent_cost_center_id": ("parent_cost_center_id", "="),
    "office_code": ("office_code", "="),
    "updated_since": ("updated_at", ">="),
}


@router.get(
    "/cost-centers",
    summary="List cost centres",
    response_description="A page of cost centres, plus pagination metadata.",
)
def list_cost_centers(
    page: int = Query(1, ge=1, description="Page number, starting at 1."),
    page_size: int = Query(100, ge=1, le=200,
                           description="Rows per page (1 to 200)."),
    cost_center_type: Optional[str] = Query(
        None, description="Group, Country, Branch or Function."),
    country: Optional[str] = Query(
        None, description="ISO country code: FR, BE, CH or IT."),
    currency: Optional[str] = Query(None, description="EUR or CHF."),
    cost_center_status: Optional[str] = Query(
        None, description="Active or Closed."),
    parent_cost_center_id: Optional[str] = Query(
        None, description="Children of this cost centre, e.g. CC-FR-000."),
    office_code: Optional[str] = Query(
        None, description="ERP office code, e.g. FR-PAR-01."),
    updated_since: Optional[datetime] = Query(
        None,
        description=(
            "Return only cost centres whose updated_at is greater than OR "
            "EQUAL TO this instant. Inclusive on purpose: extraction is "
            "at-least-once, and the ingestion layer deduplicates. "
            "Format: 2026-10-01T00:00:00Z."),
    ),
):
    """A page of cost centres, with optional filters.

    `total_records` always reflects the filters in use, never the size of the
    whole table. Columns are returned exactly as stored - no renaming, no
    transformation.
    """
    with get_connection() as connection:
        return list_page(
            connection, TABLE, PRIMARY_KEY, FILTER_COLUMNS,
            {
                "cost_center_type": cost_center_type,
                "country": country,
                "currency": currency,
                "cost_center_status": cost_center_status,
                "parent_cost_center_id": parent_cost_center_id,
                "office_code": office_code,
                "updated_since": updated_since,
            },
            page, page_size,
        )


@router.get(
    "/cost-centers/{cost_center_id}",
    summary="Get one cost centre by id",
    responses={404: {"description": "No cost centre exists with this id."}},
)
def get_cost_center(
    cost_center_id: str = Path(..., description="e.g. CC-FR-001."),
):
    """One cost centre, addressed directly.

    Unlike the list endpoint this returns the object itself, not a paginated
    envelope: it addresses one precise resource. An unknown id is an error,
    not an empty result, so it answers 404.
    """
    with get_connection() as connection:
        row = connection.execute(
            f"SELECT * FROM {TABLE} WHERE {PRIMARY_KEY} = %s",
            (cost_center_id,),
        ).fetchone()

    if row is None:
        raise HTTPException(status_code=404, detail="Cost centre not found")
    return dict(row)

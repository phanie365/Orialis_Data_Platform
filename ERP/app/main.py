"""
Entry point of the Orialis ERP API.

Launch from the repository root with:

    uvicorn ERP.app.main:app --reload

---------------------------------------------------------------------------
INDEPENDENT OF THE CRM, AND CHECKABLE
---------------------------------------------------------------------------

Nothing in this package imports from `CRM/`, reads `DATABASE_URL`, or knows
that a CRM exists. The connection string comes from `ERP_DATABASE_URL`, the
credential from `ERP_API_KEY`, and no query joins, references or reconciles
anything outside the nine ERP tables.

Techniques were borrowed from the CRM API - the pool sizing, the API-key
dependency, the filter allowlist - because they had already been reasoned
through on this deployment. Borrowing a technique is not creating a
dependency: each is reimplemented here, and this package would keep serving
if `CRM/` were deleted tomorrow.
"""

from contextlib import asynccontextmanager

from fastapi import APIRouter, Depends, FastAPI

from .database import close_pool, open_pool, pool_stats
from .routers import cost_centers, invoices, payments, stats, suppliers
from .security import require_api_key


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Open the connection pool on startup, close it on shutdown.

    Everything before `yield` runs once when the application starts, and
    everything after runs once when it stops. The pool therefore exists for
    exactly the lifetime of the process: never built at import time, never
    left behind with connections still open.

    `finally` matters. If the application stops because something failed, the
    pool is still closed and the server-side sessions released, instead of
    lingering until PostgreSQL times them out - which would matter here,
    because the simulator writes to the same database and needs connections.
    """
    open_pool()
    try:
        yield
    finally:
        close_pool()


app = FastAPI(
    title="Orialis ERP API",
    description=(
        "Read-only API over the Orialis ERP, the second fictional source "
        "system of the Orialis Data Platform. It exposes the administrative "
        "and financial side of the firm - cost centres, suppliers, supplier "
        "invoices, payments and their matching - stored in a PostgreSQL "
        "database that is entirely separate from the CRM's.\n\n"
        "**Incremental extraction.** Every list endpoint accepts "
        "`updated_since`, with an INCLUSIVE bound (`updated_at >= value`). "
        "This delivers **at-least-once**: a row sitting exactly on the "
        "watermark is returned again on the next call. That is deliberate - "
        "re-reading a row is harmless when the load deduplicates on the "
        "primary key, whereas missing one is irreversible. **The ingestion "
        "layer is responsible for idempotence and deduplication.**\n\n"
        "When `updated_since` is set, rows are ordered by "
        "`(updated_at, <primary key>)`. The tie-break is not decoration: "
        "payments are issued in campaigns, so hundreds of rows share a "
        "timestamp, and without a total order a row could appear on two "
        "pages or on none.\n\n"
        "**Not exposed in V1.** `expenses` and `commissions` will be "
        "published as file extracts instead, on purpose - a data platform "
        "whose sources are all REST APIs is not a realistic one. "
        "`employees`, `fx_rates` and `simulation_state` are out of scope: "
        "the first has no consumer yet, the second is already frozen onto "
        "every document as `fx_rate_to_eur`, and the third is simulator "
        "metadata rather than ERP data."
    ),
    version="1.0.0",
    lifespan=lifespan,
)

# Every ERP resource hangs off this one router, which carries the API-key
# dependency. Declaring it HERE, once, protects every route below: the
# dependency runs before any endpoint, and no resource module had to repeat
# it - or could forget it.
#
# The parent takes no prefix of its own: each resource router already
# declares `prefix="/api/v1"`, so adding one here would produce
# `/api/v1/api/v1`.
api_v1 = APIRouter(dependencies=[Depends(require_api_key)])

api_v1.include_router(cost_centers.router)
api_v1.include_router(suppliers.router)
api_v1.include_router(invoices.router)
api_v1.include_router(payments.router)

# Aggregates, kept apart from the resource routers on purpose: this one
# returns numbers about the ERP, never ERP records.
api_v1.include_router(stats.router)

app.include_router(api_v1)


# Declared on the application, NOT on the protected router: a load balancer
# or an uptime probe must be able to reach it without holding a credential.
# It exposes no data - a service name, a status, and the SIZE of the
# connection pool, never the connection string.
@app.get("/", tags=["service"], summary="Health check")
def read_root():
    """Confirms the API is up, and reports the state of the pool."""
    return {
        "name": "Orialis ERP API",
        "status": "running",
        "version": "1.0.0",
        "database_pool": pool_stats(),
        "docs": "/docs",
    }

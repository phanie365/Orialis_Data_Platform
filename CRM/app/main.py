"""Entry point of the Orialis CRM API.

Launch from the repository root with:

    uvicorn CRM.app.main:app --reload
"""

from contextlib import asynccontextmanager

from fastapi import APIRouter, Depends, FastAPI

from .database import close_pool, open_pool
from .routers import advisors, branches, clients, interactions, stats
from .security import require_api_key


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Open the connection pool on startup, close it on shutdown.

    Everything before `yield` runs once, when the application starts;
    everything after runs once, when it stops. The pool therefore exists for
    exactly the lifetime of the process - it is never built at import time,
    and it is never left behind with connections still open.

    `finally` matters: if the application stops because something failed, the
    pool still gets closed and the server-side sessions are released instead
    of lingering until PostgreSQL times them out.
    """
    open_pool()
    try:
        yield
    finally:
        close_pool()


app = FastAPI(
    title="Orialis CRM API",
    description=(
        "Read-only API over the Orialis CRM, a fictional source system built "
        "for the Orialis Data Platform project. It exposes the CRM data "
        "(branches, advisors, clients, interactions) stored in a PostgreSQL "
        "database hosted on Supabase, so that downstream data pipelines have "
        "a realistic system to ingest from."
    ),
    version="1.0.0",
    lifespan=lifespan,
)

# Every CRM resource hangs off this one router, which carries the API-key
# dependency. Declaring it HERE, once, protects all eight endpoints: the
# dependency runs before any of them, and none of the four resource modules
# had to be modified.
#
# The parent takes no prefix of its own - each resource router already
# declares `prefix="/api/v1"`, so adding one here would produce
# `/api/v1/api/v1`.
api_v1 = APIRouter(dependencies=[Depends(require_api_key)])

# One router per CRM resource, each with its own Swagger section.
api_v1.include_router(clients.router)
api_v1.include_router(branches.router)
api_v1.include_router(advisors.router)
api_v1.include_router(interactions.router)

# Aggregates, kept apart from the resource routers on purpose: this one
# returns numbers about the CRM, never CRM records.
api_v1.include_router(stats.router)

app.include_router(api_v1)


# Declared on the application, NOT on the protected router: the health check
# stays public so that a load balancer or uptime probe can reach it without
# holding a credential. It exposes no data.
@app.get("/")
def read_root():
    """Health check: confirms the API is up."""
    return {"name": "Orialis CRM API", "status": "running"}

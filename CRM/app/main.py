"""Entry point of the Orialis CRM API.

Launch from the repository root with:

    uvicorn CRM.app.main:app --reload
"""

from contextlib import asynccontextmanager

from fastapi import FastAPI

from .database import close_pool, open_pool
from .routers import advisors, branches, clients, interactions


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

# Registers every route declared in each router onto the application.
# One router per CRM resource, each with its own Swagger section.
app.include_router(clients.router)
app.include_router(branches.router)
app.include_router(advisors.router)
app.include_router(interactions.router)


@app.get("/")
def read_root():
    """Health check: confirms the API is up."""
    return {"name": "Orialis CRM API", "status": "running"}

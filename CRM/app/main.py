"""Entry point of the Orialis CRM API.

Launch from the repository root with:

    uvicorn CRM.app.main:app --reload
"""

from fastapi import FastAPI

from .routers import advisors, branches, clients, interactions

app = FastAPI(
    title="Orialis CRM API",
    description=(
        "Read-only API over the Orialis CRM, a fictional source system built "
        "for the Orialis Data Platform project. It exposes the CRM data "
        "(branches, advisors, clients, interactions) stored in a local SQLite "
        "database, so that downstream data pipelines have a realistic system "
        "to ingest from."
    ),
    version="1.0.0",
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

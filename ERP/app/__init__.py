"""
Read-only FastAPI application over the Orialis ERP database.

    uvicorn ERP.app.main:app --reload

    database.py   the PostgreSQL pool, over ERP_DATABASE_URL
    security.py   the X-API-Key dependency, over ERP_API_KEY
    querying.py   filters, ordering and pagination - and the incremental
                  extraction contract, which is documented there in full
    main.py       the application, and the public health endpoint
    routers/      one module per resource, plus stats.py for aggregates

Nothing here imports from CRM/, reads DATABASE_URL, or references a CRM
table. The two source systems are independent by construction.
"""

"""Access to the Orialis CRM PostgreSQL database.

This is the only module that knows how to reach the database. Every router
goes through `get_connection()` instead of calling psycopg directly, so the
connection is always configured the same way.

The connection string itself lives in `CRM/config.py`, shared with the
scripts, and is never printed.
"""

from contextlib import contextmanager

import psycopg
from psycopg.rows import dict_row

from ..config import get_database_url, scrub

# Read and validate ONCE, when the application starts - not on every request.
#
# Two reasons. A bad or missing DATABASE_URL should stop the app at startup
# rather than turn every request into a 500. And the validation prints
# advisory notes (a missing sslmode, for instance); doing that per request
# would flood the log with the same line.
DATABASE_URL = get_database_url()


@contextmanager
def get_connection():
    """Open a connection to the CRM database, and always close it.

    Used as a context manager, exactly as before:

        with get_connection() as connection:
            rows = connection.execute("SELECT ...", params).fetchall()

    The `finally` block runs even if the query raises, so a failed request
    can never leave a connection open behind it.

    One connection per request. See the note at the bottom of this file on
    when that stops being good enough.
    """
    try:
        # dict_row replaces sqlite3.Row: rows behave like dictionaries, so
        # `row["client_id"]` and `dict(row)` keep working in the routers.
        #
        # autocommit: the API only reads. Without it psycopg opens a
        # transaction on the first SELECT and holds it until the connection
        # closes, leaving the server with an idle-in-transaction session for
        # the length of every request.
        connection = psycopg.connect(
            DATABASE_URL,
            row_factory=dict_row,
            autocommit=True,
        )
    except psycopg.OperationalError as error:
        # The scrubbed message reaches the server log; the caller gets a
        # plain 500. Neither carries the connection string.
        raise RuntimeError(
            f"Could not connect to PostgreSQL: {scrub(error, DATABASE_URL)}"
        ) from None

    try:
        yield connection
    finally:
        connection.close()


# ---------------------------------------------------------------------------
# On connection pooling
# ---------------------------------------------------------------------------
# Opening a connection to Supabase costs about 800 ms: TCP, then TLS, then
# authentication, across the internet. A query on an already-open connection
# costs about 150 ms. So with one connection per request, roughly 80% of each
# API call is spent getting connected rather than fetching data.
#
# A pool (psycopg_pool.ConnectionPool) keeps a few connections open and hands
# them out, removing that 800 ms from every request. It is NOT added here yet
# because it needs a lifecycle - opened when the application starts, closed
# when it stops - which belongs in main.py, and this step covers the
# connection layer only.
#
# Because `get_connection()` keeps its shape, introducing it later changes
# this file and main.py, and no router: the body becomes
#
#     with pool.connection() as connection:
#         yield connection
#
# Add it when request latency starts to matter, or as soon as more than a
# couple of requests can arrive at the same time.

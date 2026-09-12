"""Access to the Orialis CRM PostgreSQL database.

This is the only module that knows how to reach the database. Every router
goes through `get_connection()` instead of calling psycopg directly, so the
connection is always configured the same way.

Connections come from a pool that is opened when the application starts and
closed when it stops - see `open_pool()` / `close_pool()`, wired into
FastAPI's lifespan in `main.py`. Nothing connects at import time.

The connection string itself lives in `CRM/config.py`, shared with the
scripts, and is never printed.
"""

from contextlib import contextmanager

from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from ..config import get_database_url, scrub

# Read and validate ONCE, when the module is imported - not on every request.
#
# Two reasons. A bad or missing DATABASE_URL should stop the app at startup
# rather than turn every request into a 500. And the validation prints
# advisory notes (a missing sslmode, for instance); doing that per request
# would flood the log with the same line.
DATABASE_URL = get_database_url()


# ---------------------------------------------------------------------------
# Pool sizing
# ---------------------------------------------------------------------------
# Measured on this deployment: opening a connection to Supabase costs roughly
# 800 ms - TCP, then TLS, then authentication, across the internet - while a
# query on an already-open connection costs about 150 ms. Without a pool,
# most of every request is spent getting connected.
#
# The numbers below are chosen for what this actually is: a small read-only
# demonstration backend, running as ONE uvicorn worker on a small Render
# instance, against a Supabase project shared with the seeding scripts.

# Kept warm and ready. The first requests after a restart are the ones that
# would otherwise pay the full connection cost, so two spare connections
# remove that penalty for almost no price: two idle server-side sessions.
POOL_MIN_SIZE = 2

# The ceiling. FastAPI runs synchronous endpoints in a thread pool (40
# threads by default), so the API *could* ask for 40 connections at once.
# Capping at 5 is deliberate:
#   - a small Render instance has a fraction of a CPU; it cannot usefully
#     process more than a handful of queries in parallel anyway,
#   - a Supabase project has a modest connection ceiling shared with the
#     seed scripts, psql sessions and the dashboard, so a web backend should
#     take a small, polite share of it.
# Requests beyond the fifth wait for a free connection instead of opening a
# sixth, which is exactly the behaviour wanted here.
POOL_MAX_SIZE = 5

# How long a request waits for a free connection before giving up. Queries
# take ~150 ms, so with five connections this allows a very deep queue
# already. If it is ever reached, something is wrong and failing fast beats
# piling requests up.
POOL_TIMEOUT = 10.0

# Recycling. Supabase, and any network path with an idle timeout in between,
# can silently drop a long-lived idle connection; handing out a dead one
# turns into a failed request. Retiring connections proactively avoids that.
#
# The alternative would be `check=ConnectionPool.check_connection`, which
# validates every connection on checkout - but that costs a round trip
# (~150 ms) on EVERY request, which would erase a large part of what the
# pool just bought. Proactive recycling is the cheaper trade.
POOL_MAX_IDLE = 5 * 60          # idle connections above min_size retire after 5 min
POOL_MAX_LIFETIME = 30 * 60     # every connection is replaced after 30 min

# How long `open_pool()` waits for the first connections at startup.
POOL_OPEN_TIMEOUT = 30.0


# The pool is created by `open_pool()`, never at import. A module-level
# `ConnectionPool(...)` would try to reach the database as soon as anything
# imported this file - including test collection and `--help`.
_pool: ConnectionPool | None = None


def open_pool():
    """Open the connection pool. Called once, from the FastAPI lifespan."""
    global _pool

    if _pool is not None:
        return

    pool = ConnectionPool(
        DATABASE_URL,
        min_size=POOL_MIN_SIZE,
        max_size=POOL_MAX_SIZE,
        timeout=POOL_TIMEOUT,
        max_idle=POOL_MAX_IDLE,
        max_lifetime=POOL_MAX_LIFETIME,
        # Passed to psycopg.connect() for every connection the pool creates.
        # dict_row replaces sqlite3.Row: rows behave like dictionaries, so
        # `row["client_id"]` and `dict(row)` keep working in the routers.
        # autocommit: the API only reads, and without it psycopg would hold a
        # transaction open for the length of every request.
        kwargs={"row_factory": dict_row, "autocommit": True},
        name="orialis-crm",
        open=False,
    )

    try:
        # wait=True makes startup fail loudly if the database is unreachable,
        # rather than letting every request discover it one by one.
        pool.open(wait=True, timeout=POOL_OPEN_TIMEOUT)
    except Exception as error:
        pool.close()
        raise RuntimeError(
            f"Could not open the PostgreSQL pool: {scrub(error, DATABASE_URL)}"
        ) from None

    _pool = pool


def close_pool():
    """Close the pool and every connection it holds. Called on shutdown."""
    global _pool

    if _pool is None:
        return

    # Waits for connections currently checked out to be returned, then closes
    # them all. Without this, shutting the process down would leave sessions
    # lingering on the server until it times them out.
    _pool.close()
    _pool = None


@contextmanager
def get_connection():
    """Borrow a connection from the pool, and always give it back.

    Used as a context manager, exactly as before:

        with get_connection() as connection:
            rows = connection.execute("SELECT ...", params).fetchall()

    The interface is unchanged, which is why no router had to be touched:
    what used to open and close a connection now takes one from the pool and
    returns it. The context manager guarantees the return even if the query
    raises, so a failed request cannot leak a connection.

    Rows must still be materialised inside the block (`fetchall()`,
    `fetchone()`): once the block exits, the connection belongs to the pool
    again and may already be serving another request.
    """
    if _pool is None:
        raise RuntimeError(
            "The connection pool is not open. It is opened by the FastAPI "
            "lifespan in CRM/app/main.py; call open_pool() first when using "
            "this module outside the application."
        )

    with _pool.connection() as connection:
        yield connection

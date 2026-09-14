"""Access to the Orialis ERP PostgreSQL database.

The only module that knows how to reach the database. Every router goes
through `get_connection()` rather than calling psycopg directly, so the
connection is always configured the same way.

---------------------------------------------------------------------------
WHY THIS IS A SEPARATE FILE FROM THE CRM'S
---------------------------------------------------------------------------

The pooling REASONING below is taken from `CRM/app/database.py`, which had
already measured the numbers that matter on this deployment. The CODE is not:
there is no import, and the connection string comes from `ERP/config.py`,
which reads `ERP_DATABASE_URL` and never touches `DATABASE_URL`.

Two source systems do not share a connection layer. If they did, the ERP
would stop serving the day the CRM package moved, and a reader of the ERP
would learn that a system called "CRM" exists. Borrowing a technique is not
the same as creating a dependency.

Connections come from a pool opened at application startup and closed at
shutdown - see `open_pool()` / `close_pool()`, wired into the FastAPI
lifespan. Nothing connects at import time.
"""

from contextlib import contextmanager

from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from ..config import get_database_url, scrub

# Read and validate ONCE, at import - not on every request. A bad or missing
# ERP_DATABASE_URL should stop the application at startup rather than turn
# every request into a 500, and the validation prints advisory notes that
# would otherwise repeat on every call.
DATABASE_URL = get_database_url()


# ---------------------------------------------------------------------------
# Pool sizing
# ---------------------------------------------------------------------------
# Opening a connection to a managed PostgreSQL costs roughly 800 ms - TCP,
# then TLS, then authentication, across the internet - while a query on an
# already-open connection costs about 150 ms. Without a pool, most of every
# request is spent getting connected.
#
# These numbers are chosen for what this is: a small read-only API over a
# source system, expected to run as one worker, against a database shared
# with the seed and the simulator.

# Kept warm. The first requests after a restart are the ones that would
# otherwise pay the full connection cost.
POOL_MIN_SIZE = 2

# The ceiling. FastAPI runs synchronous endpoints in a thread pool, so the
# API *could* ask for dozens of connections at once. Capping at 5 keeps the
# ERP a polite tenant of a database it shares with the simulator - which
# writes, and must not be starved of connections by a reader.
POOL_MAX_SIZE = 5

# How long a request waits for a free connection before giving up. With five
# connections and ~150 ms queries this already allows a deep queue; if it is
# ever reached, failing fast beats piling requests up.
POOL_TIMEOUT = 10.0

# Recycling. Any network path with an idle timeout in between can silently
# drop a long-lived idle connection, and handing out a dead one turns into a
# failed request.
#
# The alternative - validating every connection on checkout - costs a round
# trip on EVERY request, erasing much of what the pool just bought. Proactive
# recycling is the cheaper trade.
POOL_MAX_IDLE = 5 * 60          # idle connections above min_size retire
POOL_MAX_LIFETIME = 30 * 60     # every connection is replaced after 30 min

POOL_OPEN_TIMEOUT = 30.0


# Created by `open_pool()`, never at import. A module-level ConnectionPool
# would try to reach the database as soon as anything imported this file -
# including test collection and `--help`.
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
        # dict_row: rows behave like dictionaries, so `row["invoice_id"]` and
        # `dict(row)` work in the routers.
        # autocommit: this API only reads. Without it psycopg would hold a
        # transaction open for the length of every request - and an idle
        # transaction on a database the simulator is writing to is a good way
        # to block it.
        kwargs={"row_factory": dict_row, "autocommit": True},
        name="orialis-erp",
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

    # Waits for checked-out connections to come back, then closes them all.
    # Without this, stopping the process would leave sessions lingering on
    # the server until PostgreSQL timed them out.
    _pool.close()
    _pool = None


def pool_stats():
    """What the pool is doing right now, for the health endpoint.

    Deliberately reports SIZES, never the connection string.
    """
    if _pool is None:
        return {"open": False}
    stats = _pool.get_stats()
    return {
        "open": True,
        "min_size": POOL_MIN_SIZE,
        "max_size": POOL_MAX_SIZE,
        "size": stats.get("pool_size"),
        "available": stats.get("pool_available"),
        "waiting": stats.get("requests_waiting"),
    }


@contextmanager
def get_connection():
    """Borrow a connection from the pool, and always give it back.

        with get_connection() as connection:
            rows = connection.execute("SELECT ...", params).fetchall()

    The context manager guarantees the return even if the query raises, so a
    failed request cannot leak a connection.

    Rows must be materialised inside the block (`fetchall()`, `fetchone()`):
    once it exits, the connection belongs to the pool again and may already
    be serving another request.
    """
    if _pool is None:
        raise RuntimeError(
            "The connection pool is not open. It is opened by the FastAPI "
            "lifespan in ERP/app/main.py; call open_pool() first when using "
            "this module outside the application."
        )

    with _pool.connection() as connection:
        yield connection

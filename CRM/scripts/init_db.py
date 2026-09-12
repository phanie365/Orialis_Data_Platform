"""
Initialise the Orialis CRM schema in PostgreSQL (Supabase).

Creates (if missing) the 4 CRM tables:

    branches  ->  advisors  ->  clients  ->  interactions

The whole technical model is in English: table names, column names,
business values and documentation.

Geographic scope of the CRM (used later, when data is generated):
France, Belgium, Switzerland, Italy.

The script is idempotent: `CREATE TABLE IF NOT EXISTS` means it can be
re-run as many times as needed without recreating or erasing anything.
It creates NO data.

Connection: the DATABASE_URL variable is read from the `.env` file at the
repository root. That value is never printed.

Usage:
    python CRM/scripts/init_db.py
"""

import sys
from pathlib import Path

import psycopg

# Shared PostgreSQL configuration: .env loading, DATABASE_URL retrieval and
# validation, credential scrubbing. It lives at CRM/config.py, used by both
# the API and these scripts. The repository root goes on the import path so
# that `CRM.config` resolves the same way it does for the API.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
from CRM.config import get_database_url, scrub  # noqa: E402


# ---------------------------------------------------------------------------
# 1. Table definitions (ordered by dependency)
# ---------------------------------------------------------------------------
# Order matters: a table referenced by a foreign key must be created before
# the table that references it. PostgreSQL enforces this - unlike SQLite, it
# refuses to create a table referencing one that does not exist yet.
#
# Temporal types follow the MEANING of each column:
#
#   DATE        a calendar day, with no time of day
#               -> advisors.hire_date, clients.birth_date
#
#   TIMESTAMPTZ an instant in time, stored in UTC and returned in the
#               session's time zone
#               -> every created_at / updated_at, and interactions.
#                  interaction_date, which despite its name carries a time of
#                  day (a meeting at 17:50, not just "that day")
#
# TIMESTAMPTZ rather than TIMESTAMP: the CRM spans four countries and three
# time zones (Europe/Paris, Europe/Brussels, Europe/Zurich, Europe/Rome). A
# timestamp without a zone would make any "per day" aggregation wrong at the
# day boundaries.

CREATE_BRANCHES = """
CREATE TABLE IF NOT EXISTS branches (
    branch_id     TEXT PRIMARY KEY,
    branch_name   TEXT NOT NULL,
    city          TEXT NOT NULL,
    country       TEXT NOT NULL,
    region        TEXT,
    timezone      TEXT,
    branch_status TEXT NOT NULL
);
"""

CREATE_ADVISORS = """
CREATE TABLE IF NOT EXISTS advisors (
    advisor_id       TEXT PRIMARY KEY,
    first_name       TEXT NOT NULL,
    last_name        TEXT NOT NULL,
    email            TEXT NOT NULL,
    phone            TEXT,
    branch_id        TEXT,
    job_title        TEXT,
    specialization   TEXT,
    spoken_languages TEXT,
    hire_date        DATE,
    advisor_status   TEXT,
    updated_at       TIMESTAMPTZ,

    -- An advisor belongs to an existing branch.
    FOREIGN KEY (branch_id) REFERENCES branches (branch_id)
);
"""

CREATE_CLIENTS = """
CREATE TABLE IF NOT EXISTS clients (
    client_id            TEXT PRIMARY KEY,
    first_name           TEXT NOT NULL,
    last_name            TEXT NOT NULL,
    email                TEXT,
    phone                TEXT,
    birth_date           DATE,
    country_of_residence TEXT,
    city_of_residence    TEXT,
    nationality          TEXT,
    preferred_language   TEXT,
    client_segment       TEXT,
    risk_profile         TEXT,
    advisor_id           TEXT,
    created_at           TIMESTAMPTZ,
    updated_at           TIMESTAMPTZ,
    client_status        TEXT,

    -- A client is followed by an existing advisor.
    FOREIGN KEY (advisor_id) REFERENCES advisors (advisor_id)
);
"""

CREATE_INTERACTIONS = """
CREATE TABLE IF NOT EXISTS interactions (
    interaction_id   TEXT PRIMARY KEY,
    client_id        TEXT,
    advisor_id       TEXT,
    interaction_date TIMESTAMPTZ,
    interaction_type TEXT,
    channel          TEXT,
    subject          TEXT,
    outcome          TEXT,
    created_at       TIMESTAMPTZ,

    -- An interaction links an existing client and an existing advisor.
    FOREIGN KEY (client_id) REFERENCES clients (client_id),
    FOREIGN KEY (advisor_id) REFERENCES advisors (advisor_id)
);
"""

# Iterated in order: branches first, interactions last.
TABLES = [
    ("branches", CREATE_BRANCHES),
    ("advisors", CREATE_ADVISORS),
    ("clients", CREATE_CLIENTS),
    ("interactions", CREATE_INTERACTIONS),
]

TABLE_NAMES = [name for name, _ in TABLES]


# ---------------------------------------------------------------------------
# 2. Schema creation
# ---------------------------------------------------------------------------

def init_db():
    """Create the 4 CRM tables in PostgreSQL. Creates no data."""

    database_url = get_database_url()

    # No PRAGMA here: PostgreSQL always enforces foreign keys. The SQLite
    # `PRAGMA foreign_keys = ON` had to be repeated on every connection; it
    # has no equivalent and no purpose any more.
    try:
        connection = psycopg.connect(database_url)
    except psycopg.OperationalError as error:
        raise SystemExit(
            "Could not connect to PostgreSQL.\n"
            f"  {scrub(error, database_url)}\n"
            "Check that DATABASE_URL is correct, that it ends with "
            "'?sslmode=require', and that your network can reach the host "
            "(the Supabase direct endpoint is IPv6-only on recent projects)."
        )

    try:
        with connection.cursor() as cursor:
            for table_name, create_statement in TABLES:
                cursor.execute(create_statement)
                print(f"  [ok] table '{table_name}' ready")

            # DDL is transactional in PostgreSQL: the four tables are created
            # together or not at all. Nothing above is visible to anyone else
            # until this commit.
            connection.commit()

            report(cursor)
    except psycopg.Error as error:
        connection.rollback()
        raise SystemExit(f"Schema creation failed: {scrub(error, database_url)}")
    finally:
        # "finally": the connection is closed even if an error occurs.
        connection.close()


def report(cursor):
    """Read the schema back from the catalogue and print what exists."""

    # Server identity, taken from the server itself rather than from the
    # connection string, so nothing sensitive is displayed.
    cursor.execute("SELECT current_database(), version()")
    database_name, version = cursor.fetchone()
    server_version = version.split(",")[0]

    cursor.execute(
        """
        SELECT table_name, COUNT(*)
        FROM information_schema.columns
        WHERE table_schema = 'public' AND table_name = ANY(%s)
        GROUP BY table_name
        ORDER BY table_name
        """,
        (TABLE_NAMES,),
    )
    columns_per_table = cursor.fetchall()

    cursor.execute(
        """
        SELECT COUNT(*)
        FROM information_schema.table_constraints
        WHERE table_schema = 'public'
          AND constraint_type = 'FOREIGN KEY'
          AND table_name = ANY(%s)
        """,
        (TABLE_NAMES,),
    )
    foreign_keys = cursor.fetchone()[0]

    print()
    print("Orialis CRM schema initialised successfully.")
    print(f"Server   : {server_version}")
    print(f"Database : {database_name} (schema: public)")
    print(f"Tables   : {len(columns_per_table)} of {len(TABLES)} expected")
    for table_name, column_count in columns_per_table:
        print(f"    {table_name:14} {column_count:>2} columns")
    print(f"Foreign keys : {foreign_keys}")


if __name__ == "__main__":
    init_db()

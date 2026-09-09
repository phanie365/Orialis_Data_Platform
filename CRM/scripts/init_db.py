"""
Initialise the Orialis CRM database.

Creates (if missing) a SQLite database holding the 4 CRM tables:

    branches  ->  advisors  ->  clients  ->  interactions

The whole technical model is in English: table names, column names,
business values and documentation.

Geographic scope of the CRM (used later, when data is generated):
France, Belgium, Switzerland, Italy.

The script is idempotent: it can be re-run as many times as needed
without breaking or erasing anything.

Usage:
    python CRM/scripts/init_db.py
"""

import sqlite3
from pathlib import Path

# ---------------------------------------------------------------------------
# 1. Database location
# ---------------------------------------------------------------------------
# __file__        = .../CRM/scripts/init_db.py
# .parent         = .../CRM/scripts
# .parent.parent  = .../CRM
#
# Paths are derived from the file itself (not from the current working
# directory), so the script works no matter where it is launched from.

CRM_DIR = Path(__file__).parent.parent
DATA_DIR = CRM_DIR / "data"
DB_PATH = DATA_DIR / "orialis_crm.db"


# ---------------------------------------------------------------------------
# 2. Table definitions (ordered by dependency)
# ---------------------------------------------------------------------------
# Order matters: a table referenced by a foreign key must be created
# before the table that references it.

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
    updated_at       TIMESTAMP,

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
    created_at           TIMESTAMP,
    updated_at           TIMESTAMP,
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
    interaction_date TIMESTAMP,
    interaction_type TEXT,
    channel          TEXT,
    subject          TEXT,
    outcome          TEXT,
    created_at       TIMESTAMP,

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


# ---------------------------------------------------------------------------
# 3. Database creation
# ---------------------------------------------------------------------------

def init_db():
    """Create the data/ folder, the SQLite database and the 4 CRM tables."""

    # Create CRM/data/ if needed. exist_ok=True: no error if it already exists.
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    # sqlite3.connect() creates the .db file if it does not exist.
    connection = sqlite3.connect(DB_PATH)
    try:
        cursor = connection.cursor()

        # By default SQLite does NOT enforce foreign keys.
        # This must be enabled on every connection, before any operation.
        cursor.execute("PRAGMA foreign_keys = ON;")

        for table_name, create_statement in TABLES:
            cursor.execute(create_statement)
            print(f"  [ok] table '{table_name}' ready")

        # Persist the changes to the file.
        connection.commit()
    finally:
        # "finally": the connection is closed even if an error occurs.
        connection.close()

    print()
    print("Orialis CRM database initialised successfully.")
    print(f"File   : {DB_PATH}")
    print(f"Tables : {len(TABLES)} (branches, advisors, clients, interactions)")


if __name__ == "__main__":
    init_db()

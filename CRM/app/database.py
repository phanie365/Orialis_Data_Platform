"""Access to the Orialis CRM SQLite database.

This is the only module that knows where the database file lives and how to
open it. Every router goes through `get_connection()` instead of calling
sqlite3 directly, so the connection is always configured the same way.
"""

import sqlite3
from contextlib import contextmanager
from pathlib import Path

# The path is derived from THIS file, not from the current working directory.
#
#   __file__              .../CRM/app/database.py
#   .resolve().parent     .../CRM/app
#   .parent               .../CRM
#
# That is what makes the API work whether it is launched from the repository
# root, from CRM/, or from anywhere else.
CRM_DIR = Path(__file__).resolve().parent.parent
DB_PATH = CRM_DIR / "data" / "orialis_crm.db"


@contextmanager
def get_connection():
    """Open a connection to the CRM database, and always close it.

    Used as a context manager:

        with get_connection() as connection:
            rows = connection.execute("SELECT ...").fetchall()

    The `finally` block runs even if the query raises, so a failed request
    can never leave a connection open behind it.
    """
    connection = sqlite3.connect(DB_PATH)
    try:
        # sqlite3.Row makes each row behave like a dictionary: instead of
        # row[0] you can write row["client_id"], and dict(row) converts the
        # whole row in one step. Without it, rows are plain tuples and column
        # names are lost.
        connection.row_factory = sqlite3.Row

        # SQLite does not enforce foreign keys unless asked, on every
        # connection. The API only reads for now, but the setting stays
        # consistent with the seeding scripts.
        connection.execute("PRAGMA foreign_keys = ON;")

        yield connection
    finally:
        connection.close()

"""
Seed the `branches` table of the Orialis CRM (PostgreSQL / Supabase).

Inserts the 7 Orialis branches across the CRM geographic scope:
France, Belgium, Switzerland, Italy.

This script is safe to re-run: it uses an UPSERT keyed on branch_id, so
running it twice never creates duplicates. If a branch already exists,
its values are refreshed instead of being inserted again.

Connection: the DATABASE_URL variable is read from the `.env` file at the
repository root. That value is never printed.

Prerequisite:
    python CRM/scripts/init_db.py   (creates the tables)

Usage:
    python CRM/scripts/seed_branches.py
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
# Reference data
# ---------------------------------------------------------------------------
# One tuple per branch, in the column order used by the INSERT statement:
#
#   branch_id, branch_name, city, country, region, timezone, branch_status
#
# Note: the status column is named `branch_status` (not `status`) since the
# schema rename. All values are in English; timezones use IANA identifiers.
# Unchanged from the SQLite version - the same 7 branches, same values.

BRANCHES = [
    ("BR001", "Orialis Paris",     "Paris",     "France",      "Ile-de-France",           "Europe/Paris",    "Active"),
    ("BR002", "Orialis Lyon",      "Lyon",      "France",      "Auvergne-Rhone-Alpes",    "Europe/Paris",    "Active"),
    ("BR003", "Orialis Brussels",  "Brussels",  "Belgium",     "Brussels-Capital Region", "Europe/Brussels", "Active"),
    ("BR004", "Orialis Geneva",    "Geneva",    "Switzerland", "Geneva",                  "Europe/Zurich",   "Active"),
    ("BR005", "Orialis Lausanne",  "Lausanne",  "Switzerland", "Vaud",                    "Europe/Zurich",   "Active"),
    ("BR006", "Orialis Milan",     "Milan",     "Italy",       "Lombardy",                "Europe/Rome",     "Active"),
    ("BR007", "Orialis Rome",      "Rome",      "Italy",       "Lazio",                   "Europe/Rome",     "Active"),
]


# ---------------------------------------------------------------------------
# SQL
# ---------------------------------------------------------------------------
# "INSERT ... ON CONFLICT DO UPDATE" is an UPSERT: insert the row, but if a
# row with the same branch_id already exists, update it instead of failing.
# This is what makes the script re-runnable without duplicates.
#
# This is native PostgreSQL syntax - SQLite borrowed it from PostgreSQL, so
# the statement carries over unchanged apart from the placeholders.
#
# The "%s" are placeholders: values are passed separately to the server
# rather than glued into the SQL string. This is the correct way to pass
# data. (SQLite used "?"; psycopg uses "%s", whatever the value's type.)

UPSERT_BRANCH = """
INSERT INTO branches (
    branch_id, branch_name, city, country, region, timezone, branch_status
)
VALUES (%s, %s, %s, %s, %s, %s, %s)
ON CONFLICT (branch_id) DO UPDATE SET
    branch_name   = excluded.branch_name,
    city          = excluded.city,
    country       = excluded.country,
    region        = excluded.region,
    timezone      = excluded.timezone,
    branch_status = excluded.branch_status;
"""


def seed_branches():
    """Insert or refresh the Orialis branches."""

    database_url = get_database_url()

    # No PRAGMA here: PostgreSQL always enforces foreign keys. And no check
    # that a database file exists - there is no file any more; a missing
    # table is reported below instead.
    try:
        connection = psycopg.connect(database_url)
    except psycopg.OperationalError as error:
        raise SystemExit(
            "Could not connect to PostgreSQL.\n"
            f"  {scrub(error, database_url)}"
        )

    try:
        with connection.cursor() as cursor:
            # Read the ids already present, so we can report what was newly
            # inserted versus what was refreshed.
            try:
                cursor.execute("SELECT branch_id FROM branches")
            except psycopg.errors.UndefinedTable:
                connection.rollback()
                raise SystemExit(
                    "The `branches` table does not exist.\n"
                    "Run 'python CRM/scripts/init_db.py' first."
                )
            existing_ids = {row[0] for row in cursor.fetchall()}

            inserted, updated = 0, 0
            for branch in BRANCHES:
                branch_id, branch_name = branch[0], branch[1]

                cursor.execute(UPSERT_BRANCH, branch)

                if branch_id in existing_ids:
                    updated += 1
                    print(f"  [=] {branch_id}  {branch_name:20} already present, refreshed")
                else:
                    inserted += 1
                    print(f"  [+] {branch_id}  {branch_name:20} inserted")

            connection.commit()

            # Read the table back to confirm what is actually stored.
            cursor.execute("""
                SELECT branch_id, branch_name, city, country, timezone, branch_status
                FROM branches
                ORDER BY branch_id
            """)
            rows = cursor.fetchall()
    except psycopg.Error as error:
        connection.rollback()
        raise SystemExit(f"Seeding failed: {scrub(error, database_url)}")
    finally:
        # "finally": the connection is closed even if an error occurs.
        connection.close()

    print()
    print(f"Branches seeded: {inserted} inserted, {updated} refreshed, {len(rows)} total in table.")
    print()
    print(f"  {'ID':6} {'NAME':20} {'CITY':10} {'COUNTRY':13} {'TIMEZONE':17} STATUS")
    print(f"  {'-' * 6} {'-' * 20} {'-' * 10} {'-' * 13} {'-' * 17} ------")
    for branch_id, name, city, country, timezone, status in rows:
        print(f"  {branch_id:6} {name:20} {city:10} {country:13} {timezone:17} {status}")


if __name__ == "__main__":
    seed_branches()

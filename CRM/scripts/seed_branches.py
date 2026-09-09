"""
Seed the `branches` table of the Orialis CRM.

Inserts the 7 Orialis branches across the CRM geographic scope:
France, Belgium, Switzerland, Italy.

This script is safe to re-run: it uses an UPSERT keyed on branch_id, so
running it twice never creates duplicates. If a branch already exists,
its values are refreshed instead of being inserted again.

Prerequisite:
    python CRM/scripts/init_db.py   (creates the database and its tables)

Usage:
    python CRM/scripts/seed_branches.py
"""

import sqlite3
from pathlib import Path

# Same path logic as init_db.py: derived from this file, not from the
# current working directory.
CRM_DIR = Path(__file__).parent.parent
DB_PATH = CRM_DIR / "data" / "orialis_crm.db"


# ---------------------------------------------------------------------------
# Reference data
# ---------------------------------------------------------------------------
# One tuple per branch, in the column order used by the INSERT statement:
#
#   branch_id, branch_name, city, country, region, timezone, branch_status
#
# Note: the status column is named `branch_status` (not `status`) since the
# schema rename. All values are in English; timezones use IANA identifiers.

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
# The "?" are placeholders: values are passed separately to SQLite rather
# than glued into the SQL string. This is the correct way to pass data.

UPSERT_BRANCH = """
INSERT INTO branches (
    branch_id, branch_name, city, country, region, timezone, branch_status
)
VALUES (?, ?, ?, ?, ?, ?, ?)
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

    if not DB_PATH.exists():
        raise SystemExit(
            f"Database not found: {DB_PATH}\n"
            f"Run 'python CRM/scripts/init_db.py' first."
        )

    connection = sqlite3.connect(DB_PATH)
    try:
        cursor = connection.cursor()

        # SQLite does not enforce foreign keys unless asked, on every
        # connection. `branches` has none, but we stay consistent.
        cursor.execute("PRAGMA foreign_keys = ON;")

        # Read the ids already present, so we can report what was newly
        # inserted versus what was refreshed.
        existing_ids = {row[0] for row in cursor.execute("SELECT branch_id FROM branches")}

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
        rows = list(cursor.execute("""
            SELECT branch_id, branch_name, city, country, timezone, branch_status
            FROM branches
            ORDER BY branch_id
        """))
    finally:
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

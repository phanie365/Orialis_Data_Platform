"""Prove that the Orialis CRM supports incremental extraction.

The script does three things:

    1. reads the current WATERMARKS  (latest interaction created_at,
       latest client updated_at)
    2. runs ONE day of simulated activity
    3. replays the exact filters the API uses, from those watermarks, and
       checks that the new activity is what comes back

It changes nothing by itself: the only writes come from the simulator it
calls. Run it as many times as you like.

Note on column names: `interactions.created_at` records when a row was
written, and `clients.updated_at` when a client last changed. Those are the
two columns an ingestion pipeline follows.

Prerequisites: a seeded database.

Usage:
    python CRM/scripts/test_incremental_behavior.py
"""

import io
import sqlite3
import sys
from contextlib import redirect_stdout
from pathlib import Path

SCRIPTS_DIR = Path(__file__).parent
CRM_DIR = SCRIPTS_DIR.parent
DB_PATH = CRM_DIR / "data" / "orialis_crm.db"

# Reuse the simulator rather than copying its logic. `scripts/` is not a
# package, so its folder is put on the import path first.
sys.path.insert(0, str(SCRIPTS_DIR))
from simulate_daily_activity import simulate_daily_activity  # noqa: E402


def connect():
    """Open a read-only-by-convention connection with dict-like rows."""
    connection = sqlite3.connect(DB_PATH)
    connection.row_factory = sqlite3.Row
    return connection


def capture_state(connection):
    """Read the counters and watermarks that describe the current CRM."""
    cursor = connection.cursor()
    return {
        "interactions": cursor.execute(
            "SELECT COUNT(*) FROM interactions"
        ).fetchone()[0],
        "clients": cursor.execute(
            "SELECT COUNT(*) FROM clients"
        ).fetchone()[0],
        # The two watermarks: the high-water mark of what has been seen.
        "max_interaction_created_at": cursor.execute(
            "SELECT MAX(created_at) FROM interactions"
        ).fetchone()[0],
        "max_client_updated_at": cursor.execute(
            "SELECT MAX(updated_at) FROM clients"
        ).fetchone()[0],
    }


def to_iso(timestamp):
    """Turn a stored timestamp into the ISO form the API expects.

    The database stores "2026-09-09 20:27:37" (space); the API takes
    "2026-09-09T20:27:37" (T). Same instant, two spellings.
    """
    return timestamp.replace(" ", "T")


def main():
    if not DB_PATH.exists():
        raise SystemExit(
            f"Database not found: {DB_PATH}\n"
            f"Run 'python CRM/scripts/init_db.py' first."
        )

    # ---------------------------------------------------------------------
    # 1. BEFORE - capture the watermarks
    # ---------------------------------------------------------------------
    # This MUST happen before the simulation. Once new rows exist, the
    # maximum has moved and there is no way to ask "what changed since?".
    connection = connect()
    try:
        before = capture_state(connection)
    finally:
        connection.close()

    print("=" * 66)
    print("BEFORE")
    print("=" * 66)
    print(f"  Interactions stored            {before['interactions']:>8}")
    print(f"  Clients stored                 {before['clients']:>8}")
    print(f"  Watermark - interaction        {before['max_interaction_created_at']}")
    print(f"  Watermark - client             {before['max_client_updated_at']}")
    print()

    # ---------------------------------------------------------------------
    # 2. One simulated business day
    # ---------------------------------------------------------------------
    # The simulator prints its own detailed report; it is captured here so
    # this script's output stays readable. Run it directly to see it.
    simulator_output = io.StringIO()
    with redirect_stdout(simulator_output):
        simulate_daily_activity()

    # ---------------------------------------------------------------------
    # 3. AFTER - what actually changed
    # ---------------------------------------------------------------------
    connection = connect()
    try:
        after = capture_state(connection)
        cursor = connection.cursor()

        new_interactions = after["interactions"] - before["interactions"]

        print("=" * 66)
        print("SIMULATION")
        print("=" * 66)
        print(f"  + {new_interactions} interactions inserted")

        # -----------------------------------------------------------------
        # 4. Incremental extraction, exactly as the API does it
        # -----------------------------------------------------------------
        # Same SQL as GET /api/v1/interactions?created_since=...
        found_interactions = cursor.execute(
            "SELECT interaction_id, created_at FROM interactions "
            "WHERE created_at >= ? ORDER BY interaction_id",
            (before["max_interaction_created_at"],),
        ).fetchall()

        # Same SQL as GET /api/v1/clients?updated_since=...
        found_clients = cursor.execute(
            "SELECT client_id, updated_at FROM clients "
            "WHERE updated_at >= ? ORDER BY client_id",
            (before["max_client_updated_at"],),
        ).fetchall()

        # Strictly after the watermark = what THIS run changed. The ">="
        # figure above also carries rows sitting exactly on the boundary,
        # which earlier runs had already produced.
        new_clients = cursor.execute(
            "SELECT COUNT(*) FROM clients WHERE updated_at > ?",
            (before["max_client_updated_at"],),
        ).fetchone()[0]

        print(f"  + {new_clients} clients updated")
        print()

        print("=" * 66)
        print("INCREMENTAL EXTRACTION (from the watermarks captured above)")
        print("=" * 66)
        print(f"  Interactions detected          {len(found_interactions):>8}")
        print(f"      sample ids                 "
              f"{', '.join(row['interaction_id'] for row in found_interactions[:5])}")
        print(f"  Clients detected               {len(found_clients):>8}")
        print(f"      sample ids                 "
              f"{', '.join(row['client_id'] for row in found_clients[:5]) or '(none)'}")
        print()

        # The API filter is ">=", which is inclusive: any row sitting exactly
        # ON the watermark is returned again. That overlap is deliberate -
        # re-reading a row is harmless, missing one is not - but it is worth
        # seeing, so both counts are shown.
        boundary_interactions = cursor.execute(
            "SELECT COUNT(*) FROM interactions WHERE created_at = ?",
            (before["max_interaction_created_at"],),
        ).fetchone()[0]
        strictly_after = cursor.execute(
            "SELECT COUNT(*) FROM interactions WHERE created_at > ?",
            (before["max_interaction_created_at"],),
        ).fetchone()[0]

        print("  Boundary effect of the inclusive '>=' filter")
        print(f"      rows exactly on the watermark      {boundary_interactions:>4}")
        print(f"      rows strictly after it (>)         {strictly_after:>4}")
        print(f"      rows returned by the API (>=)      {len(found_interactions):>4}")
        print()

        # -----------------------------------------------------------------
        # 5. AFTER state
        # -----------------------------------------------------------------
        print("=" * 66)
        print("AFTER")
        print("=" * 66)
        print(f"  Interactions stored            {after['interactions']:>8}")
        print(f"  Clients stored                 {after['clients']:>8}")
        print(f"  Watermark - interaction        {after['max_interaction_created_at']}")
        print(f"  Watermark - client             {after['max_client_updated_at']}")
        print()

        # -----------------------------------------------------------------
        # 6. The equivalent API calls, to replay by hand in Swagger
        # -----------------------------------------------------------------
        print("=" * 66)
        print("EQUIVALENT API REQUESTS (try them in /docs)")
        print("=" * 66)
        print("  From the OLD watermarks - returns the new activity:")
        print(f"      GET /api/v1/interactions?created_since="
              f"{to_iso(before['max_interaction_created_at'])}")
        print(f"      GET /api/v1/clients?updated_since="
              f"{to_iso(before['max_client_updated_at'])}")
        print()
        print("  From the NEW watermarks - should return (almost) nothing,")
        print("  until the next simulated day:")
        print(f"      GET /api/v1/interactions?created_since="
              f"{to_iso(after['max_interaction_created_at'])}")
        print(f"      GET /api/v1/clients?updated_since="
              f"{to_iso(after['max_client_updated_at'])}")
        print()

        # -----------------------------------------------------------------
        # 7. Verdict
        # -----------------------------------------------------------------
        # The guarantee an incremental pipeline needs is AT-LEAST-ONCE: the
        # ">=" filter must return every new row. Returning a few boundary
        # rows twice is acceptable; missing one is not.
        checks = [
            ("new interactions were created",
             new_interactions > 0),
            ("at least one client was updated",
             new_clients > 0),
            ("the '>=' filter returns every new interaction",
             len(found_interactions) >= new_interactions),
            ("the '>=' filter returns every updated client",
             len(found_clients) >= new_clients),
            ("no new interaction fell below the watermark",
             strictly_after >= new_interactions),
            ("the interaction watermark moved forward",
             after["max_interaction_created_at"] > before["max_interaction_created_at"]),
            ("the client watermark moved forward",
             after["max_client_updated_at"] > before["max_client_updated_at"]),
        ]

        print("=" * 66)
        print("VERDICT")
        print("=" * 66)
        for label, passed in checks:
            print(f"  {label:52} [{'OK' if passed else 'FAILED'}]")
        print()

        if all(passed for _, passed in checks):
            print("  Incremental extraction works: the CRM exposes both new")
            print("  records and changed records through their timestamps.")
        else:
            raise SystemExit("Incremental behaviour could not be verified.")
    finally:
        connection.close()


if __name__ == "__main__":
    main()

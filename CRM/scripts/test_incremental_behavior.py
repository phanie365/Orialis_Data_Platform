"""
Prove that the Orialis CRM supports incremental extraction (PostgreSQL).

The script does four things:

    1. reads the current WATERMARKS
    2. runs ONE day of simulated activity
    3. replays three extraction strategies from those watermarks
    4. checks which one returns exactly the rows the simulator produced

It changes nothing by itself: the only writes come from the simulator it
calls once. The simulator is deliberately NOT idempotent, so this script
must not be run in a loop expecting a stable database.

Connection: DATABASE_URL is read from the `.env` file at the repository
root. That value is never printed.

Prerequisites: a seeded database.

Usage:
    python CRM/scripts/test_incremental_behavior.py
"""

import io
import sys
from contextlib import redirect_stdout
from pathlib import Path

import psycopg
from psycopg.rows import dict_row

SCRIPTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS_DIR))
sys.path.insert(0, str(SCRIPTS_DIR.parent.parent))
from CRM.config import get_database_url, scrub  # noqa: E402
from simulate_daily_activity import simulate_daily_activity  # noqa: E402


# ---------------------------------------------------------------------------
# The three extraction strategies
# ---------------------------------------------------------------------------
# A pipeline stores a watermark and asks "what changed since?". How that
# question is written decides whether rows come back twice, or not at all.
#
#   A. timestamp >= watermark      inclusive: every row sharing the watermark
#                                  timestamp comes back on EVERY run
#   B. timestamp >  watermark      strict: silently DROPS the rows that share
#                                  the watermark timestamp but were not yet
#                                  extracted
#   C. (timestamp, id) > (ts, id)  composite: strict on the pair, so it skips
#                                  only what was really taken, and keeps the
#                                  rest of that same second
#
# Only C is both complete and duplicate-free, and only because the pair is
# unique - the id is a primary key.

EXTRACT_INTERACTIONS = {
    "A": (
        "SELECT interaction_id, created_at FROM interactions "
        "WHERE created_at >= %(ts)s "
        "ORDER BY created_at, interaction_id"
    ),
    "B": (
        "SELECT interaction_id, created_at FROM interactions "
        "WHERE created_at > %(ts)s "
        "ORDER BY created_at, interaction_id"
    ),
    "C": (
        "SELECT interaction_id, created_at FROM interactions "
        "WHERE (created_at, interaction_id) > (%(ts)s, %(id)s) "
        "ORDER BY created_at, interaction_id"
    ),
}

EXTRACT_CLIENTS = {
    "A": (
        "SELECT client_id, updated_at FROM clients "
        "WHERE updated_at >= %(ts)s "
        "ORDER BY updated_at, client_id"
    ),
    "B": (
        "SELECT client_id, updated_at FROM clients "
        "WHERE updated_at > %(ts)s "
        "ORDER BY updated_at, client_id"
    ),
    "C": (
        "SELECT client_id, updated_at FROM clients "
        "WHERE (updated_at, client_id) > (%(ts)s, %(id)s) "
        "ORDER BY updated_at, client_id"
    ),
}


def connect():
    """Open a connection whose rows behave like dictionaries."""
    url = get_database_url()
    try:
        return psycopg.connect(url, row_factory=dict_row), url
    except psycopg.OperationalError as error:
        raise SystemExit(
            "Could not connect to PostgreSQL.\n"
            f"  {scrub(error, url)}"
        )


# ---------------------------------------------------------------------------
# Reading the watermarks
# ---------------------------------------------------------------------------

def read_watermark(cursor, table, timestamp_column, id_column):
    """The composite high-water mark: the LAST row in (timestamp, id) order.

    Returned as a dict ready to be bound to the extraction queries. The
    timestamp comes back as a timezone-aware datetime - the column is
    TIMESTAMPTZ - so nothing has to be parsed.
    """
    row = cursor.execute(
        f"SELECT {timestamp_column} AS ts, {id_column} AS id "
        f"FROM {table} "
        f"ORDER BY {timestamp_column} DESC, {id_column} DESC "
        f"LIMIT 1"
    ).fetchone()
    return {"ts": row["ts"], "id": row["id"]} if row else None


def capture_state(cursor):
    """Counters and watermarks describing the current CRM."""
    return {
        "interactions": cursor.execute(
            "SELECT COUNT(*) AS n FROM interactions"
        ).fetchone()["n"],
        "clients": cursor.execute(
            "SELECT COUNT(*) AS n FROM clients"
        ).fetchone()["n"],
        "max_interaction_id": cursor.execute(
            "SELECT MAX(interaction_id) AS id FROM interactions"
        ).fetchone()["id"],
        "interaction_watermark": read_watermark(
            cursor, "interactions", "created_at", "interaction_id"
        ),
        "client_watermark": read_watermark(
            cursor, "clients", "updated_at", "client_id"
        ),
    }


def show_watermark(label, watermark):
    print(f"  {label}")
    print(f"      timestamp                  {watermark['ts']}")
    print(f"      id                         {watermark['id']}")


# ---------------------------------------------------------------------------
# Comparing the three strategies
# ---------------------------------------------------------------------------

def compare_strategies(cursor, queries, watermark, expected, id_key, label):
    """Run the three extractions and report what each one returns."""

    print(f"  {label}")
    print(f"      rows actually produced by the run   {expected:>6}")
    print()
    print(f"      {'strategy':40} {'rows':>6}  {'verdict'}")
    print(f"      {'-' * 40} {'-' * 6}  {'-' * 28}")

    results = {}
    for key, query in queries.items():
        rows = cursor.execute(query, watermark).fetchall()
        results[key] = rows

        extra = len(rows) - expected
        if extra == 0:
            verdict = "exact"
        elif extra > 0:
            verdict = f"{extra} duplicate(s) re-delivered"
        else:
            verdict = f"{-extra} row(s) LOST"

        name = {
            "A": "A. timestamp >= watermark",
            "B": "B. timestamp >  watermark",
            "C": "C. (timestamp, id) > (ts, id)",
        }[key]
        print(f"      {name:40} {len(rows):>6}  {verdict}")

    print()
    sample = [row[id_key] for row in results["C"][:5]]
    print(f"      sample from strategy C     {', '.join(sample) or '(none)'}")
    print()
    return results


# ---------------------------------------------------------------------------
# Why the composite watermark is needed, shown on the real data
# ---------------------------------------------------------------------------

def demonstrate_tie(cursor):
    """Take the most crowded timestamp and cut it in half.

    This is the situation every incremental pipeline eventually meets: an
    extraction stops in the MIDDLE of a group of rows sharing one timestamp.
    """
    crowded = cursor.execute("""
        SELECT created_at AS ts, COUNT(*) AS n
        FROM interactions
        GROUP BY created_at
        ORDER BY n DESC
        LIMIT 1
    """).fetchone()

    if crowded["n"] < 2:
        print("  No timestamp is shared by several rows; nothing to show.")
        return

    ids = [
        row["interaction_id"]
        for row in cursor.execute(
            "SELECT interaction_id FROM interactions "
            "WHERE created_at = %s ORDER BY interaction_id",
            (crowded["ts"],),
        ).fetchall()
    ]
    cut = len(ids) // 2
    taken, remaining = ids[:cut], ids[cut:]

    print(f"  The busiest second in the table: {crowded['ts']}")
    print(f"      rows sharing it            {crowded['n']:>6}")
    print(f"      suppose a run stopped after {cut} of them, on {taken[-1]}")
    print()

    counts = {}
    for key, query in EXTRACT_INTERACTIONS.items():
        counts[key] = len(cursor.execute(
            query, {"ts": crowded["ts"], "id": taken[-1]}
        ).fetchall())

    after_group = cursor.execute(
        "SELECT COUNT(*) AS n FROM interactions WHERE created_at > %s",
        (crowded["ts"],),
    ).fetchone()["n"]
    ideal = len(remaining) + after_group

    print(f"      {'strategy':40} {'rows':>6}  {'verdict'}")
    print(f"      {'-' * 40} {'-' * 6}  {'-' * 28}")
    print(f"      {'A. timestamp >= watermark':40} {counts['A']:>6}  "
          f"{counts['A'] - ideal} duplicate(s) re-delivered")
    print(f"      {'B. timestamp >  watermark':40} {counts['B']:>6}  "
          f"{ideal - counts['B']} row(s) LOST")
    print(f"      {'C. (timestamp, id) > (ts, id)':40} {counts['C']:>6}  "
          f"{'exact' if counts['C'] == ideal else 'WRONG'}")
    print()
    print(f"      the correct answer is {ideal}: the {len(remaining)} rows left in "
          f"that second,")
    print(f"      plus the {after_group} rows written later.")
    print()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    connection, url = connect()
    try:
        with connection.cursor() as cursor:
            connection.read_only = True
            before = capture_state(cursor)
    finally:
        connection.close()

    print("=" * 72)
    print("BEFORE")
    print("=" * 72)
    print(f"  Interactions stored            {before['interactions']:>8}")
    print(f"  Clients stored                 {before['clients']:>8}")
    print(f"  Highest interaction_id         {before['max_interaction_id']}")
    print()
    show_watermark("Interaction watermark (created_at, interaction_id)",
                   before["interaction_watermark"])
    show_watermark("Client watermark (updated_at, client_id)",
                   before["client_watermark"])
    print()

    # -----------------------------------------------------------------------
    # ONE simulated business day. The simulator prints its own report; it is
    # captured here so this script's output stays readable.
    # -----------------------------------------------------------------------
    simulator_output = io.StringIO()
    with redirect_stdout(simulator_output):
        simulate_daily_activity()

    connection, url = connect()
    try:
        with connection.cursor() as cursor:
            after = capture_state(cursor)

            # Ground truth, independent of any timestamp: interaction ids are
            # sequential and append-only, so anything above the previous
            # maximum is new. Clients get the simulation instant, which is
            # guaranteed to sit strictly past the previous watermark.
            new_interactions = cursor.execute(
                "SELECT COUNT(*) AS n FROM interactions WHERE interaction_id > %s",
                (before["max_interaction_id"],),
            ).fetchone()["n"]
            updated_clients = cursor.execute(
                "SELECT COUNT(*) AS n FROM clients WHERE updated_at > %s",
                (before["client_watermark"]["ts"],),
            ).fetchone()["n"]

            print("=" * 72)
            print("SIMULATION (one run, deliberately not idempotent)")
            print("=" * 72)
            print(f"  + {new_interactions} interactions inserted")
            print(f"  + {updated_clients} clients updated")
            print()

            print("=" * 72)
            print("INCREMENTAL EXTRACTION FROM THE WATERMARKS ABOVE")
            print("=" * 72)
            compare_strategies(
                cursor, EXTRACT_INTERACTIONS, before["interaction_watermark"],
                new_interactions, "interaction_id",
                "Interactions (append-only table)",
            )
            compare_strategies(
                cursor, EXTRACT_CLIENTS, before["client_watermark"],
                updated_clients, "client_id",
                "Clients (mutable table)",
            )

            print("=" * 72)
            print("WHY THE COMPOSITE WATERMARK IS NEEDED")
            print("=" * 72)
            demonstrate_tie(cursor)

            print("=" * 72)
            print("AFTER")
            print("=" * 72)
            print(f"  Interactions stored            {after['interactions']:>8}")
            print(f"  Clients stored                 {after['clients']:>8}")
            print(f"  Highest interaction_id         {after['max_interaction_id']}")
            print()
            show_watermark("Interaction watermark (created_at, interaction_id)",
                           after["interaction_watermark"])
            show_watermark("Client watermark (updated_at, client_id)",
                           after["client_watermark"])
            print()

            print("=" * 72)
            print("WHAT AN INGESTION PIPELINE WOULD RUN")
            print("=" * 72)
            print("  Store the watermark as a PAIR, not a single timestamp:")
            print()
            print("      SELECT created_at, interaction_id")
            print("      FROM   interactions")
            print("      ORDER  BY created_at DESC, interaction_id DESC")
            print("      LIMIT  1;")
            print()
            print("  Then, on the next run:")
            print()
            print("      SELECT *")
            print("      FROM   interactions")
            print("      WHERE  (created_at, interaction_id) > (:ts, :id)")
            print("      ORDER  BY created_at, interaction_id;")
            print()
            print("      SELECT *")
            print("      FROM   clients")
            print("      WHERE  (updated_at, client_id) > (:ts, :id)")
            print("      ORDER  BY updated_at, client_id;")
            print()
            print("  Current values to pass:")
            print(f"      interactions  :ts = {after['interaction_watermark']['ts']}")
            print(f"                    :id = {after['interaction_watermark']['id']}")
            print(f"      clients       :ts = {after['client_watermark']['ts']}")
            print(f"                    :id = {after['client_watermark']['id']}")
            print()
            print("  The API exposes the single-timestamp form today:")
            print(f"      GET /api/v1/interactions?created_since="
                  f"{after['interaction_watermark']['ts'].isoformat()}")
            print(f"      GET /api/v1/clients?updated_since="
                  f"{after['client_watermark']['ts'].isoformat()}")
            print("      -> at-least-once: correct, but re-delivers the rows")
            print("         sharing the watermark second.")
            print()

            verdict(new_interactions, updated_clients, cursor,
                    before["interaction_watermark"], before["client_watermark"])
    finally:
        connection.close()


def verdict(new_interactions, updated_clients, cursor, interaction_wm, client_wm):
    """Check that the composite strategy matches the run exactly."""

    found_interactions = len(cursor.execute(
        EXTRACT_INTERACTIONS["C"], interaction_wm
    ).fetchall())
    found_clients = len(cursor.execute(
        EXTRACT_CLIENTS["C"], client_wm
    ).fetchall())

    checks = [
        ("the run produced new interactions", new_interactions > 0),
        ("the run updated clients", updated_clients > 0),
        ("composite extraction finds every new interaction",
         found_interactions == new_interactions),
        ("composite extraction finds every updated client",
         found_clients == updated_clients),
        ("no duplicate delivered for interactions",
         found_interactions <= new_interactions),
        ("no duplicate delivered for clients",
         found_clients <= updated_clients),
    ]

    print("=" * 72)
    print("VERDICT")
    print("=" * 72)
    for label, passed in checks:
        print(f"  {label:58} [{'OK' if passed else 'FAILED'}]")
    print()

    if all(passed for _, passed in checks):
        print("  The composite watermark (timestamp, id) returns exactly the rows")
        print("  the simulator produced: none missing, none delivered twice.")
    else:
        raise SystemExit("Incremental behaviour could not be verified.")


if __name__ == "__main__":
    main()

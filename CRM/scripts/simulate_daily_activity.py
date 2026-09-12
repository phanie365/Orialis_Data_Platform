"""Simulate one business day of activity in the Orialis CRM.

Each run represents ONE day: it books new interactions and edits a handful of
client records, exactly as a real CRM would accumulate change overnight.

    - 35 to 80 new interactions, on existing clients, with their own advisor
    - 5 to 20 clients updated (phone, language, risk profile or status)
    - `updated_at` refreshed on every client touched

This script is deliberately NOT idempotent. Seed scripts describe a state and
converge to it; this one records events and appends to history. Running it
five times simulates five days, not one day five times. Nothing is deleted,
nothing is reset.

Its purpose is to give a downstream ingestion pipeline something to find:
new rows to pick up through `created_at`, and changed rows to pick up
through `updated_at`.

Note: the timestamp column on `clients` is `updated_at` (not `last_updated`),
and on `interactions` the two time columns are `interaction_date` (when the
meeting happened) and `created_at` (when the record was written).

Prerequisites: a seeded database.

Usage:
    python CRM/scripts/simulate_daily_activity.py
"""

import random
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import psycopg
from psycopg.rows import dict_row

# Shared PostgreSQL configuration: .env loading, DATABASE_URL retrieval and
# validation, credential scrubbing. It lives at CRM/config.py, used by both
# the API and these scripts. The repository root goes on the import path so
# that `CRM.config` resolves the same way it does for the API.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
from CRM.config import get_database_url, scrub  # noqa: E402

# No fixed seed: every run must differ. This is the opposite of the seed
# scripts, which pin a seed to stay reproducible.
rng = random.Random()

# How much activity a single day produces.
INTERACTIONS_PER_DAY = (35, 80)
CLIENT_UPDATES_PER_DAY = (5, 20)

# Working hours during which interactions can take place.
BUSINESS_START_HOUR = 8
BUSINESS_END_HOUR = 19


# ---------------------------------------------------------------------------
# 1. Business rules for the interactions (same logic as the historical data)
# ---------------------------------------------------------------------------

TYPE_WEIGHTS = [
    ("Portfolio Review", 28), ("Advisory", 22), ("Follow-up", 18),
    ("Client Meeting", 12), ("Administrative Update", 10),
    ("Prospecting", 7), ("Complaint", 3),
]

# A prospect has no portfolio to review yet.
PROSPECT_TYPE_WEIGHTS = [
    ("Prospecting", 52), ("Follow-up", 26), ("Client Meeting", 22),
]

CHANNEL_BY_TYPE = {
    "Portfolio Review": [("Video Call", 29), ("In Person", 24), ("Email", 22),
                         ("Phone", 21), ("Client Portal", 4)],
    "Advisory": [("Video Call", 26), ("Phone", 26), ("Email", 24),
                 ("In Person", 20), ("Client Portal", 4)],
    "Follow-up": [("Email", 48), ("Phone", 33), ("Video Call", 10),
                  ("Client Portal", 5), ("In Person", 4)],
    "Client Meeting": [("In Person", 58), ("Video Call", 34), ("Phone", 5),
                       ("Email", 3)],
    "Administrative Update": [("Email", 45), ("Client Portal", 40),
                              ("Phone", 10), ("In Person", 3), ("Video Call", 2)],
    "Prospecting": [("Phone", 42), ("Email", 38), ("Video Call", 14),
                    ("In Person", 6)],
    "Complaint": [("Phone", 45), ("Email", 40), ("In Person", 8),
                  ("Video Call", 7)],
}

SUBJECT_BY_TYPE = {
    "Portfolio Review": [("Annual portfolio review", 38), ("Portfolio rebalancing", 27),
                         ("Investment strategy review", 22), ("Risk profile update", 13)],
    "Advisory": [("Investment strategy review", 20), ("New investment opportunity", 18),
                 ("Investment proposal discussion", 16), ("Retirement planning", 14),
                 ("Tax planning discussion", 12), ("Estate planning discussion", 10),
                 ("Cross-border investment discussion", 10)],
    "Follow-up": [("Investment proposal discussion", 28), ("Portfolio rebalancing", 20),
                  ("New investment opportunity", 18), ("Retirement planning", 14),
                  ("Risk profile update", 12), ("Complaint follow-up", 8)],
    "Client Meeting": [("Annual portfolio review", 24), ("Retirement planning", 20),
                       ("Estate planning discussion", 18), ("Inheritance planning", 16),
                       ("Investment strategy review", 14), ("Tax planning discussion", 8)],
    "Administrative Update": [("Account information update", 52),
                              ("Risk profile update", 26), ("Client onboarding", 22)],
    "Prospecting": [("Client onboarding", 40), ("New investment opportunity", 34),
                    ("Investment proposal discussion", 26)],
    "Complaint": [("Complaint follow-up", 68), ("Account information update", 20),
                  ("Portfolio rebalancing", 12)],
}

OUTCOME_BY_TYPE = {
    "Portfolio Review": [("No action required", 34), ("Follow-up required", 26),
                         ("Meeting scheduled", 16), ("Investment decision pending", 14),
                         ("Investment completed", 10)],
    "Advisory": [("Proposal sent", 30), ("Investment decision pending", 26),
                 ("Follow-up required", 20), ("Investment completed", 14),
                 ("Meeting scheduled", 10)],
    "Follow-up": [("Meeting scheduled", 28), ("No action required", 26),
                  ("Follow-up required", 20), ("Investment completed", 14),
                  ("Proposal sent", 12)],
    "Client Meeting": [("Follow-up required", 30), ("Proposal sent", 24),
                       ("No action required", 20), ("Meeting scheduled", 14),
                       ("Investment decision pending", 12)],
    "Administrative Update": [("Client information updated", 72),
                              ("No action required", 20), ("Follow-up required", 8)],
    "Prospecting": [("Meeting scheduled", 34), ("Follow-up required", 30),
                    ("No action required", 20), ("Proposal sent", 16)],
    "Complaint": [("Issue resolved", 46), ("Follow-up required", 32),
                  ("Client information updated", 14), ("Meeting scheduled", 8)],
}

# How likely a client is to be contacted on any given day. Multiplied
# together, so an inactive Standard client is very rarely picked while an
# active Private Banking client comes up often.
SEGMENT_ACTIVITY = {"Private Banking": 3.4, "Patrimonial": 1.7, "Standard": 1.0}
STATUS_ACTIVITY = {"Active": 1.0, "Prospect": 0.5, "Inactive": 0.12}


# ---------------------------------------------------------------------------
# 2. Business rules for the client updates
# ---------------------------------------------------------------------------

# Risk profiles move one step at a time. A direct Conservative <-> Growth
# jump is possible but rare, as it would be in real life.
RISK_TRANSITIONS = {
    "Conservative": [("Balanced", 90), ("Growth", 10)],
    "Balanced": [("Conservative", 48), ("Growth", 52)],
    "Growth": [("Balanced", 90), ("Conservative", 10)],
}

# Status changes are the rarest kind of update.
STATUS_TRANSITIONS = {
    "Prospect": [("Active", 100)],
    "Active": [("Inactive", 100)],
    "Inactive": [("Active", 100)],
}

SUPPORTED_LANGUAGES = ["FR", "IT", "EN"]

PHONE_TEMPLATES = {
    "France": "+33 {:1d} {:02d} {:02d} {:02d} {:02d}",
    "Belgium": "+32 4{:02d} {:02d} {:02d} {:02d}",
    "Switzerland": "+41 7{:1d} {:03d} {:02d} {:02d}",
    "Italy": "+39 3{:02d} {:03d} {:04d}",
}

# Which field gets edited when a client is picked. Phone and risk profile are
# routine; a status change is a real business event and stays rare.
UPDATE_KIND_WEIGHTS = [
    ("phone", 38), ("risk_profile", 30), ("preferred_language", 20),
    ("client_status", 12),
]


# ---------------------------------------------------------------------------
# 3. Helpers
# ---------------------------------------------------------------------------

def weighted_pick(weighted_items):
    """Pick one value from a list of (value, weight) tuples."""
    values = [value for value, _ in weighted_items]
    weights = [weight for _, weight in weighted_items]
    return rng.choices(values, weights=weights, k=1)[0]


def business_window(now):
    """The stretch of today during which interactions may be booked.

    Timestamps must never be in the future, so the window stops at `now`.
    If the script runs before opening time, the previous business day is
    used instead of producing an empty window.
    """
    opening = now.replace(hour=BUSINESS_START_HOUR, minute=0, second=0, microsecond=0)
    closing = now.replace(hour=BUSINESS_END_HOUR, minute=0, second=0, microsecond=0)

    end = min(now, closing)
    if end <= opening:
        # Run outside business hours: simulate yesterday's working day.
        opening -= timedelta(days=1)
        end = closing - timedelta(days=1)

    return opening, end


def build_phone(country):
    """A new fictional mobile number for the client's country."""
    template = PHONE_TEMPLATES[country]
    if country == "France":
        return template.format(rng.choice([6, 7]), rng.randint(10, 99),
                               rng.randint(10, 99), rng.randint(10, 99),
                               rng.randint(10, 99))
    if country == "Belgium":
        return template.format(rng.randint(70, 99), rng.randint(10, 99),
                               rng.randint(10, 99), rng.randint(10, 99))
    if country == "Switzerland":
        return template.format(rng.choice([6, 8, 9]), rng.randint(100, 999),
                               rng.randint(10, 99), rng.randint(10, 99))
    return template.format(rng.randint(20, 49), rng.randint(100, 999),
                           rng.randint(1000, 9999))


def simulation_instant(cursor):
    """The instant this simulated day is stamped with.

    Normally the wall clock. But timestamps have ONE-SECOND resolution, and
    each run stands for a different day: two runs launched inside the same
    second would stamp identical `created_at` / `updated_at` values, their
    batches would be indistinguishable, and a pipeline reading "strictly
    after the watermark" would see the second batch as nothing at all.

    So the instant is pushed one second past everything already stored. When
    days are replayed faster than real time the stamps run slightly ahead of
    the clock - which is exactly what a compressed simulation should do.
    """
    # UTC-aware: both columns are TIMESTAMPTZ, so psycopg returns aware
    # datetimes. A naive `now` could not be compared with them.
    now = datetime.now(timezone.utc).replace(microsecond=0)

    stored = [
        cursor.execute(
            "SELECT MAX(created_at) AS latest FROM interactions"
        ).fetchone()["latest"],
        cursor.execute(
            "SELECT MAX(updated_at) AS latest FROM clients"
        ).fetchone()["latest"],
    ]
    for value in stored:
        if value:
            # No strptime: the driver already hands back a datetime.
            now = max(now, value + timedelta(seconds=1))

    return now


def next_interaction_ids(cursor, count):
    """Reserve `count` fresh interaction ids, continuing from the highest.

    The number of rows is NOT used: rows could have been deleted, or a
    previous simulation could already have pushed the sequence forward.
    Only the largest existing number matters.
    """
    highest = cursor.execute(
        "SELECT MAX(CAST(SUBSTR(interaction_id, 4) AS INTEGER)) AS highest "
        "FROM interactions"
    ).fetchone()["highest"] or 0

    return [f"INT{highest + offset:06d}" for offset in range(1, count + 1)]


# ---------------------------------------------------------------------------
# 4. New interactions
# ---------------------------------------------------------------------------

def generate_interactions(cursor, now):
    """Book the day's interactions and return the rows to insert."""

    clients = cursor.execute("""
        SELECT client_id, advisor_id, client_segment, client_status
        FROM clients
    """).fetchall()

    # `created_at` is when the CRM WRITES the row, so it can never be older
    # than the last row already written. Without this floor, a run launched
    # the same calendar day as the previous one would produce timestamps
    # BELOW the existing maximum: the watermark would stop moving forward
    # and an incremental pipeline would silently miss the whole batch.
    latest_written = cursor.execute(
        "SELECT MAX(created_at) AS latest FROM interactions"
    ).fetchone()["latest"]
    # One second PAST the last row, not level with it: a row written exactly
    # on the watermark would be re-read on every later extraction instead of
    # being counted as new. No strptime: the driver returns a datetime.
    floor = latest_written + timedelta(seconds=1) if latest_written else None

    # Soft weighting: nobody is excluded, but an active Private Banking
    # client is roughly 28 times more likely to come up than an inactive
    # Standard one.
    weights = [
        SEGMENT_ACTIVITY[row["client_segment"]] * STATUS_ACTIVITY[row["client_status"]]
        for row in clients
    ]

    count = rng.randint(*INTERACTIONS_PER_DAY)
    interaction_ids = next_interaction_ids(cursor, count)
    opening, closing = business_window(now)
    window_seconds = int((closing - opening).total_seconds())

    rows = []
    for interaction_id in interaction_ids:
        client = rng.choices(clients, weights=weights, k=1)[0]

        if client["client_status"] == "Prospect":
            interaction_type = weighted_pick(PROSPECT_TYPE_WEIGHTS)
        else:
            interaction_type = weighted_pick(TYPE_WEIGHTS)

        interaction_date = opening + timedelta(seconds=rng.randint(0, window_seconds))

        # The record is written now, give or take a few minutes of trickle.
        # It can never be in the future, never precede its own meeting, and
        # never fall below the last row already written.
        created_at = now - timedelta(seconds=rng.randint(0, 300))
        created_at = max(created_at, interaction_date)
        if floor is not None:
            created_at = max(created_at, floor)
        created_at = min(created_at, now)

        rows.append((
            interaction_id,
            client["client_id"],
            client["advisor_id"],          # always the client's own advisor
            interaction_date,                 # TIMESTAMPTZ, real datetime
            interaction_type,
            weighted_pick(CHANNEL_BY_TYPE[interaction_type]),
            weighted_pick(SUBJECT_BY_TYPE[interaction_type]),
            weighted_pick(OUTCOME_BY_TYPE[interaction_type]),
            created_at,                       # TIMESTAMPTZ, real datetime
        ))

    return rows


# ---------------------------------------------------------------------------
# 5. Client updates
# ---------------------------------------------------------------------------

def update_clients(cursor, now):
    """Edit a handful of clients.

    Returns the ids touched, a count per kind of change, and the timestamp
    actually written.
    """

    # `now` is the simulation instant, already guaranteed to sit past every
    # timestamp already stored (see simulation_instant).
    # A real datetime, not a formatted string: `updated_at` is TIMESTAMPTZ
    # and psycopg adapts the object directly.
    timestamp = now
    count = rng.randint(*CLIENT_UPDATES_PER_DAY)

    # A client is picked at most once per day, hence ORDER BY RANDOM() with
    # a LIMIT rather than repeated draws.
    candidates = cursor.execute("""
        SELECT c.client_id, c.country_of_residence, c.preferred_language,
               c.risk_profile, c.client_status, a.spoken_languages
        FROM clients c
        JOIN advisors a ON a.advisor_id = c.advisor_id
        ORDER BY RANDOM()
        LIMIT %s
    """, (count,)).fetchall()

    changes = {"phone": 0, "risk_profile": 0, "preferred_language": 0,
               "client_status": 0}
    updated_ids = []

    for client in candidates:
        kind = weighted_pick(UPDATE_KIND_WEIGHTS)

        if kind == "preferred_language":
            # The client can only switch to a language their advisor speaks,
            # otherwise the pair would become unable to communicate.
            advisor_languages = set(client["spoken_languages"].split(","))
            options = [
                language for language in SUPPORTED_LANGUAGES
                if language in advisor_languages
                and language != client["preferred_language"]
            ]
            if not options:
                # No valid alternative: change the phone number instead.
                kind = "phone"
            else:
                new_value = rng.choice(options)

        if kind == "phone":
            new_value = build_phone(client["country_of_residence"])
        elif kind == "risk_profile":
            new_value = weighted_pick(RISK_TRANSITIONS[client["risk_profile"]])
        elif kind == "client_status":
            new_value = weighted_pick(STATUS_TRANSITIONS[client["client_status"]])

        # The column name comes from UPDATE_KIND_WEIGHTS, which we control;
        # the value and the id are bound parameters.
        cursor.execute(
            f"UPDATE clients SET {kind} = %s, updated_at = %s WHERE client_id = %s",
            (new_value, timestamp, client["client_id"]),
        )

        changes[kind] += 1
        updated_ids.append(client["client_id"])

    # The stamp actually written is returned, since it may have been pushed
    # past `now` by the floor above. Check 6 compares against THIS value.
    return updated_ids, changes, timestamp


# ---------------------------------------------------------------------------
# 6. Integrity checks
# ---------------------------------------------------------------------------

def run_checks(cursor, new_ids, updated_ids, timestamp, client_timestamp):
    """Run the 7 integrity checks. Returns a list of (label, error count)."""

    # `= ANY(%s)` takes the whole id list as ONE array parameter. SQLite
    # needed a generated "?,?,?,..." string here.
    checks = [
        ("1. new interactions with an unknown client_id", """
            SELECT COUNT(*) AS n FROM interactions i
            LEFT JOIN clients c ON c.client_id = i.client_id
            WHERE i.interaction_id = ANY(%s) AND c.client_id IS NULL
        """, (list(new_ids),)),
        ("2. new interactions with an unknown advisor_id", """
            SELECT COUNT(*) AS n FROM interactions i
            LEFT JOIN advisors a ON a.advisor_id = i.advisor_id
            WHERE i.interaction_id = ANY(%s) AND a.advisor_id IS NULL
        """, (list(new_ids),)),
        ("3. new interactions not using the client's advisor", """
            SELECT COUNT(*) AS n FROM interactions i
            JOIN clients c ON c.client_id = i.client_id
            WHERE i.interaction_id = ANY(%s)
              AND i.advisor_id <> c.advisor_id
        """, (list(new_ids),)),
        # PostgreSQL requires an alias on a subquery in FROM; SQLite did not.
        ("4. duplicate interaction_id in the whole table", """
            SELECT COUNT(*) AS n FROM (
                SELECT interaction_id FROM interactions
                GROUP BY interaction_id HAVING COUNT(*) > 1
            ) AS duplicates
        """, None),
        ("5. interaction timestamps in the future", """
            SELECT COUNT(*) AS n FROM interactions
            WHERE interaction_date > %s OR created_at > %s
        """, (timestamp, timestamp)),
    ]

    results = []
    for label, query, parameters in checks:
        results.append((label, cursor.execute(query, parameters).fetchone()["n"]))

    # 6. Every client touched this run carries the simulation timestamp.
    if updated_ids:
        value = cursor.execute(
            "SELECT COUNT(*) AS n FROM clients "
            "WHERE client_id = ANY(%s) AND updated_at <> %s",
            (list(updated_ids), client_timestamp),
        ).fetchone()["n"]
    else:
        value = 0
    results.append(("6. updated clients missing the simulation timestamp", value))

    # 7. Nobody, anywhere, ended up with an advisor who cannot speak to them.
    # No parameters here, so the literal '%' of the LIKE pattern needs no
    # escaping: psycopg only looks for placeholders when values are passed.
    results.append((
        "7. clients whose language their advisor does not speak",
        cursor.execute("""
            SELECT COUNT(*) AS n FROM clients c
            JOIN advisors a ON a.advisor_id = c.advisor_id
            WHERE ',' || a.spoken_languages || ','
                  NOT LIKE '%,' || c.preferred_language || ',%'
        """).fetchone()["n"],
    ))

    return results


# ---------------------------------------------------------------------------
# 7. Main
# ---------------------------------------------------------------------------

def simulate_daily_activity():
    """Run one simulated business day, inside a single transaction."""

    database_url = get_database_url()

    # No PRAGMA here: PostgreSQL always enforces foreign keys.
    #
    # dict_row replaces sqlite3.Row for the named column access this module
    # relies on. Unlike sqlite3.Row it does NOT also support row[0], which is
    # why every aggregate below is given an explicit alias.
    try:
        connection = psycopg.connect(database_url, row_factory=dict_row)
    except psycopg.OperationalError as error:
        raise SystemExit(
            "Could not connect to PostgreSQL.\n"
            f"  {scrub(error, database_url)}"
        )

    try:
        with connection.cursor() as cursor:
            try:
                now = simulation_instant(cursor)
            except psycopg.errors.UndefinedTable:
                connection.rollback()
                raise SystemExit(
                    "The CRM tables do not exist.\n"
                    "Run 'python CRM/scripts/init_db.py' first."
                )

            # A real datetime, not a formatted string: every timestamp column
            # is TIMESTAMPTZ.
            timestamp = now

            # Everything below happens inside ONE transaction. psycopg opens
            # it on the first statement; nothing is visible to anyone else
            # until the commit, and any failure rolls the whole day back.
            try:
                interactions = generate_interactions(cursor, now)
                cursor.executemany("""
                    INSERT INTO interactions (
                        interaction_id, client_id, advisor_id, interaction_date,
                        interaction_type, channel, subject, outcome, created_at
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                """, interactions)

                updated_ids, changes, client_timestamp = update_clients(cursor, now)

                new_ids = [row[0] for row in interactions]
                checks = run_checks(
                    cursor, new_ids, updated_ids, timestamp, client_timestamp
                )

                failures = sum(count for _, count in checks)
                if failures:
                    raise psycopg.IntegrityError(
                        f"{failures} integrity error(s) detected; rolling back."
                    )

                connection.commit()
            except Exception:
                # A half-applied day is worse than no day at all.
                connection.rollback()
                raise

            report(cursor, now, timestamp, new_ids, updated_ids, changes, checks)
    finally:
        # "finally": the connection is closed even if an error occurs.
        connection.close()

def report(cursor, now, timestamp, new_ids, updated_ids, changes, checks):
    """Print what the simulated day changed."""

    total_interactions = cursor.execute(
        "SELECT COUNT(*) AS n FROM interactions"
    ).fetchone()["n"]
    latest_created = cursor.execute(
        "SELECT MAX(created_at) AS latest FROM interactions"
    ).fetchone()["latest"]
    latest_updated = cursor.execute(
        "SELECT MAX(updated_at) AS latest FROM clients"
    ).fetchone()["latest"]

    print(f"Simulated business day: {timestamp}")
    print()
    print("  Interactions")
    print(f"      inserted                    {len(new_ids):>8}")
    print(f"      id range                    {new_ids[0]} .. {new_ids[-1]}")
    print(f"      total now in the CRM        {total_interactions:>8}")
    print(f"      latest created_at           {latest_created}")
    print()
    print("  Clients")
    print(f"      updated                     {len(updated_ids):>8}")
    for field in ("risk_profile", "preferred_language", "phone", "client_status"):
        print(f"          {field:24} {changes[field]:>4}")
    print(f"      latest updated_at           {latest_updated}")
    print()
    print(f"  Sample new interactions : {', '.join(new_ids[:5])}")
    print(f"  Sample updated clients  : {', '.join(updated_ids[:5]) or '(none)'}")
    print()
    print("  Integrity checks (all must be 0)")
    for label, count in checks:
        verdict = "OK" if count == 0 else "FAILED"
        print(f"      {label:56} {count:>4}   [{verdict}]")


if __name__ == "__main__":
    simulate_daily_activity()

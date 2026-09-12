"""
Seed the `interactions` table of the Orialis CRM.

Generates exactly 30,000 fictional CRM interactions for the 5,000 clients and
100 advisors already stored in the database.

How the volume is shared out
----------------------------
Each client gets a weight built from its segment (Private Banking clients are
followed far more closely than Standard ones), its status (an inactive client
generates little, a prospect even less) and its tenure. The 30,000
interactions are then split with the LARGEST REMAINDER method, which turns
those fractional shares into whole numbers summing to exactly 30,000.

How the content is built
------------------------
Nothing is drawn independently: the channel depends on the interaction type
(an administrative update travels by email, a client meeting happens in
person), and so do the subject and the outcome. Dates are drawn inside each
client's own timeline, so an interaction can never predate its client.

The data comes from a SEEDED random generator, so the output is
deterministic. Combined with an UPSERT on interaction_id and the removal of
leftovers, re-running the script yields 30,000 rows, never 60,000.

Prerequisites:
    python CRM/scripts/init_db.py
    python CRM/scripts/seed_branches.py
    python CRM/scripts/seed_advisors.py
    python CRM/scripts/seed_clients.py

Usage:
    python CRM/scripts/seed_interactions.py
"""

import random
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import psycopg

# Shared PostgreSQL configuration: .env loading, DATABASE_URL retrieval and
# validation, credential scrubbing. It lives at CRM/config.py, used by both
# the API and these scripts. The repository root goes on the import path so
# that `CRM.config` resolves the same way it does for the API.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
from CRM.config import get_database_url, scrub  # noqa: E402

RNG_SEED = 20260911

# Reference "now". Hardcoded rather than read from the clock, so the dataset
# does not drift over time.
#
# UTC-aware, not naive: `clients.created_at` is a TIMESTAMPTZ, so psycopg
# hands it back as an aware datetime. Mixing aware and naive values raises
# TypeError on the first subtraction, so the whole module works in UTC -
# which is also the session time zone, so the wall-clock values are the same
# ones the SQLite version computed.
NOW = datetime(2026, 9, 9, 18, 0, 0, tzinfo=timezone.utc)

TOTAL_INTERACTIONS = 30000


# ---------------------------------------------------------------------------
# 1. How many interactions each client generates
# ---------------------------------------------------------------------------

# A Private Banking client is followed far more closely than a Standard one.
SEGMENT_ACTIVITY = {
    "Private Banking": 3.4,
    "Patrimonial": 1.7,
    "Standard": 1.0,
}

# An inactive client stopped generating activity; a prospect never really
# started.
STATUS_ACTIVITY = {
    "Active": 1.00,
    "Inactive": 0.55,
    "Prospect": 0.28,
}

# Minimum interactions per client: even a prospect was contacted once.
MIN_INTERACTIONS_PER_CLIENT = 1


# ---------------------------------------------------------------------------
# 2. Interaction types
# ---------------------------------------------------------------------------

TYPE_WEIGHTS = [
    ("Portfolio Review", 28),
    ("Advisory", 22),
    ("Follow-up", 18),
    ("Client Meeting", 12),
    ("Administrative Update", 10),
    ("Prospecting", 7),
    ("Complaint", 3),
]

# Prospects are not managed like existing clients: they are prospected, met
# and followed up, but they have no portfolio to review yet.
PROSPECT_TYPE_WEIGHTS = [
    ("Prospecting", 46),
    ("Follow-up", 22),
    ("Client Meeting", 18),
    ("Advisory", 9),
    ("Administrative Update", 5),
]


# ---------------------------------------------------------------------------
# 3. Channel, conditioned on the interaction type
# ---------------------------------------------------------------------------
# The channel is never drawn on its own: you do not settle a complaint through
# a client portal, and you do not run an annual review by email.

CHANNEL_BY_TYPE = {
    "Portfolio Review": [
        ("Video Call", 29), ("In Person", 24), ("Email", 22), ("Phone", 21),
        ("Client Portal", 4),
    ],
    "Advisory": [
        ("Video Call", 26), ("Phone", 26), ("Email", 24), ("In Person", 20),
        ("Client Portal", 4),
    ],
    "Follow-up": [
        ("Email", 48), ("Phone", 33), ("Video Call", 10), ("Client Portal", 5),
        ("In Person", 4),
    ],
    "Client Meeting": [
        ("In Person", 58), ("Video Call", 34), ("Phone", 5), ("Email", 3),
    ],
    "Administrative Update": [
        ("Email", 45), ("Client Portal", 40), ("Phone", 10), ("In Person", 3),
        ("Video Call", 2),
    ],
    "Prospecting": [
        ("Phone", 42), ("Email", 38), ("Video Call", 14), ("In Person", 6),
    ],
    "Complaint": [
        ("Phone", 45), ("Email", 40), ("In Person", 8), ("Video Call", 7),
    ],
}


# ---------------------------------------------------------------------------
# 4. Subject and outcome, conditioned on the interaction type
# ---------------------------------------------------------------------------

SUBJECT_BY_TYPE = {
    "Portfolio Review": [
        ("Annual portfolio review", 38), ("Portfolio rebalancing", 27),
        ("Investment strategy review", 22), ("Risk profile update", 13),
    ],
    "Advisory": [
        ("Investment strategy review", 20), ("New investment opportunity", 18),
        ("Investment proposal discussion", 16), ("Retirement planning", 14),
        ("Tax planning discussion", 12), ("Estate planning discussion", 10),
        ("Cross-border investment discussion", 10),
    ],
    "Follow-up": [
        ("Investment proposal discussion", 28), ("Portfolio rebalancing", 20),
        ("New investment opportunity", 18), ("Retirement planning", 14),
        ("Risk profile update", 12), ("Complaint follow-up", 8),
    ],
    "Client Meeting": [
        ("Annual portfolio review", 24), ("Retirement planning", 20),
        ("Estate planning discussion", 18), ("Inheritance planning", 16),
        ("Investment strategy review", 14), ("Tax planning discussion", 8),
    ],
    "Administrative Update": [
        ("Account information update", 52), ("Risk profile update", 26),
        ("Client onboarding", 22),
    ],
    "Prospecting": [
        ("Client onboarding", 40), ("New investment opportunity", 34),
        ("Investment proposal discussion", 26),
    ],
    "Complaint": [
        ("Complaint follow-up", 68), ("Account information update", 20),
        ("Portfolio rebalancing", 12),
    ],
}

OUTCOME_BY_TYPE = {
    "Portfolio Review": [
        ("No action required", 34), ("Follow-up required", 26),
        ("Meeting scheduled", 16), ("Investment decision pending", 14),
        ("Investment completed", 10),
    ],
    "Advisory": [
        ("Proposal sent", 30), ("Investment decision pending", 26),
        ("Follow-up required", 20), ("Investment completed", 14),
        ("Meeting scheduled", 10),
    ],
    "Follow-up": [
        ("Meeting scheduled", 28), ("No action required", 26),
        ("Follow-up required", 20), ("Investment completed", 14),
        ("Proposal sent", 12),
    ],
    "Client Meeting": [
        ("Follow-up required", 30), ("Proposal sent", 24),
        ("No action required", 20), ("Meeting scheduled", 14),
        ("Investment decision pending", 12),
    ],
    "Administrative Update": [
        ("Client information updated", 72), ("No action required", 20),
        ("Follow-up required", 8),
    ],
    "Prospecting": [
        ("Meeting scheduled", 34), ("Follow-up required", 30),
        ("No action required", 20), ("Proposal sent", 16),
    ],
    "Complaint": [
        ("Issue resolved", 46), ("Follow-up required", 32),
        ("Client information updated", 14), ("Meeting scheduled", 8),
    ],
}


# ---------------------------------------------------------------------------
# 5. Helpers
# ---------------------------------------------------------------------------

def weighted_pick(rng, weighted_items):
    """Pick one value from a list of (value, weight) tuples."""
    values = [value for value, _ in weighted_items]
    weights = [weight for _, weight in weighted_items]
    return rng.choices(values, weights=weights, k=1)[0]


def largest_remainder_allocation(shares, total, minimum):
    """Turn fractional shares into whole numbers summing exactly to `total`.

    Each client first gets the whole part of its share (never below
    `minimum`); the interactions still unallocated then go to the clients
    with the largest fractional remainders. This is the method used to
    allocate parliamentary seats - it is what guarantees an exact total
    while keeping the intended proportions.
    """
    counts = []
    remainders = []
    for index, share in enumerate(shares):
        whole = max(int(share), minimum)
        counts.append(whole)
        remainders.append((share - int(share), index))

    difference = total - sum(counts)

    if difference > 0:
        # Hand out the leftovers, largest remainder first.
        remainders.sort(reverse=True)
        position = 0
        while difference > 0:
            counts[remainders[position % len(remainders)][1]] += 1
            position += 1
            difference -= 1
    elif difference < 0:
        # Over-allocated: take back from the smallest remainders, never
        # dropping a client below the minimum.
        remainders.sort()
        position = 0
        while difference < 0:
            index = remainders[position % len(remainders)][1]
            if counts[index] > minimum:
                counts[index] -= 1
                difference += 1
            position += 1

    return counts


def interaction_offsets(rng, count, segment, status):
    """Where each interaction sits on the client's timeline.

    The value is a fraction "back in time": 0.0 means today, 1.0 means the
    day the client was created.
    """
    if segment == "Private Banking":
        # Regular contact: roughly one interaction per time slice, jittered.
        offsets = [(i + rng.uniform(0.15, 0.85)) / count for i in range(count)]
    else:
        # A power of a uniform draw biases towards recent (exponent > 1) or
        # towards the past (exponent < 1).
        exponent = {"Active": 1.8, "Inactive": 0.7, "Prospect": 2.2}[status]
        offsets = [rng.random() ** exponent for _ in range(count)]

    if status == "Inactive":
        # Nothing in the most recent stretch: their last contact is old.
        offsets = [0.28 + 0.72 * offset for offset in offsets]

    return offsets


def business_datetime(rng, moment, earliest, latest):
    """Move a timestamp to plausible business hours, staying inside bounds."""
    snapped = moment.replace(
        hour=rng.randint(8, 18),
        minute=rng.choice([0, 5, 10, 15, 20, 30, 40, 45, 50]),
        second=rng.randint(0, 59),
    )
    if snapped < earliest:
        snapped = earliest + timedelta(minutes=rng.randint(15, 600))
    if snapped > latest:
        snapped = latest - timedelta(minutes=rng.randint(15, 600))
    return snapped


# ---------------------------------------------------------------------------
# 6. Generation
# ---------------------------------------------------------------------------

def load_clients(cursor):
    """Read the clients already stored, with the advisor assigned to them."""
    rows = list(cursor.execute("""
        SELECT client_id, advisor_id, client_segment, client_status, created_at
        FROM clients
        ORDER BY client_id
    """))
    return [
        {
            "client_id": client_id,
            "advisor_id": advisor_id,
            "segment": segment,
            "status": status,
            # No strptime: the column is TIMESTAMPTZ, so psycopg returns an
            # aware datetime directly.
            "created_at": created_at,
        }
        for client_id, advisor_id, segment, status, created_at in rows
    ]


def allocate_interactions(rng, clients):
    """Decide how many interactions each client gets, summing to the total."""
    weights = []
    for client in clients:
        tenure_years = (NOW - client["created_at"]).days / 365.25
        # A client of ten years has more history than one of six months.
        tenure_factor = 0.45 + min(tenure_years, 11.0) * 0.16
        personal = rng.lognormvariate(0.0, 0.38)

        weights.append(
            SEGMENT_ACTIVITY[client["segment"]]
            * STATUS_ACTIVITY[client["status"]]
            * tenure_factor
            * personal
        )

    scale = TOTAL_INTERACTIONS / sum(weights)
    shares = [weight * scale for weight in weights]
    return largest_remainder_allocation(
        shares, TOTAL_INTERACTIONS, MIN_INTERACTIONS_PER_CLIENT
    )


def generate_interactions(rng, clients, counts):
    """Build every interaction row, then number them in chronological order."""

    rows = []

    for client, count in zip(clients, counts):
        created_at = client["created_at"]
        span_seconds = max((NOW - created_at).total_seconds(), 3600.0)

        for offset in interaction_offsets(rng, count, client["segment"], client["status"]):
            # offset is a fraction back in time from NOW.
            moment = NOW - timedelta(seconds=offset * span_seconds)
            interaction_date = business_datetime(rng, moment, created_at, NOW)

            if client["status"] == "Prospect":
                interaction_type = weighted_pick(rng, PROSPECT_TYPE_WEIGHTS)
            else:
                interaction_type = weighted_pick(rng, TYPE_WEIGHTS)

            channel = weighted_pick(rng, CHANNEL_BY_TYPE[interaction_type])
            subject = weighted_pick(rng, SUBJECT_BY_TYPE[interaction_type])
            outcome = weighted_pick(rng, OUTCOME_BY_TYPE[interaction_type])

            # The record is written up during or shortly after the meeting,
            # so created_at is at or after interaction_date - never before.
            logged_at = interaction_date + timedelta(minutes=rng.randint(0, 2160))
            if logged_at > NOW:
                logged_at = NOW

            rows.append({
                "client_id": client["client_id"],
                "advisor_id": client["advisor_id"],  # always the client's own advisor
                "interaction_date": interaction_date,
                "interaction_type": interaction_type,
                "channel": channel,
                "subject": subject,
                "outcome": outcome,
                "created_at": logged_at,
            })

    # Number the interactions chronologically, the way a real CRM would.
    rows.sort(key=lambda row: (row["interaction_date"], row["client_id"]))

    return [
        (
            f"INT{index + 1:06d}",
            row["client_id"],
            row["advisor_id"],
            # Real datetimes for the TIMESTAMPTZ columns, truncated to the
            # second. The offsets are computed in fractional seconds, so the
            # values carry microseconds; SQLite dropped them when formatting
            # to text, and the rest of the CRM is stored to the second.
            # Truncating HERE, after the sort above, keeps the chronological
            # numbering identical to the original dataset.
            row["interaction_date"].replace(microsecond=0),
            row["interaction_type"],
            row["channel"],
            row["subject"],
            row["outcome"],
            row["created_at"].replace(microsecond=0),
        )
        for index, row in enumerate(rows)
    ]


# ---------------------------------------------------------------------------
# 7. SQL
# ---------------------------------------------------------------------------

UPSERT_INTERACTION = """
INSERT INTO interactions (
    interaction_id, client_id, advisor_id, interaction_date,
    interaction_type, channel, subject, outcome, created_at
)
VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
ON CONFLICT (interaction_id) DO UPDATE SET
    client_id        = excluded.client_id,
    advisor_id       = excluded.advisor_id,
    interaction_date = excluded.interaction_date,
    interaction_type = excluded.interaction_type,
    channel          = excluded.channel,
    subject          = excluded.subject,
    outcome          = excluded.outcome,
    created_at       = excluded.created_at;
"""


def seed_interactions():
    """Generate and store the 30,000 interactions, then verify the result."""

    rng = random.Random(RNG_SEED)
    database_url = get_database_url()

    # No PRAGMA here: PostgreSQL always enforces foreign keys, so an
    # interaction pointing at a missing client or advisor is rejected by the
    # server.
    try:
        connection = psycopg.connect(database_url)
    except psycopg.OperationalError as error:
        raise SystemExit(
            "Could not connect to PostgreSQL.\n"
            f"  {scrub(error, database_url)}"
        )

    try:
        with connection.cursor() as cursor:
            try:
                clients = load_clients(cursor)
            except psycopg.errors.UndefinedTable:
                connection.rollback()
                raise SystemExit(
                    "The `clients` table does not exist.\n"
                    "Run 'python CRM/scripts/init_db.py' first."
                )

            if not clients:
                raise SystemExit(
                    "The `clients` table is empty.\n"
                    "Run 'python CRM/scripts/seed_clients.py' first."
                )

            counts = allocate_interactions(rng, clients)
            interactions = generate_interactions(rng, clients, counts)
            wanted_ids = [row[0] for row in interactions]

            # Remove interactions left over from an earlier dataset, so the
            # table ends up with exactly 30,000 rows and never 60,000.
            #
            # SQLite needed a temporary table here, because a
            # 30,000-placeholder "IN (?,?,?,...)" clause exceeds its variable
            # limit. PostgreSQL takes the whole list as ONE array parameter,
            # so the staging table is gone and the delete is one statement.
            cursor.execute(
                "DELETE FROM interactions WHERE NOT (interaction_id = ANY(%s))",
                (wanted_ids,),
            )
            removed = cursor.rowcount

            existing_ids = {
                row[0] for row in cursor.execute("SELECT interaction_id FROM interactions")
            }

            # executemany() sends the whole batch in pipeline mode on
            # PostgreSQL 14+, so the 30,000 rows do NOT cost 30,000 network
            # round trips.
            cursor.executemany(UPSERT_INTERACTION, interactions)

            inserted = sum(1 for row in interactions if row[0] not in existing_ids)
            updated = len(interactions) - inserted

            connection.commit()

            print(
                f"Interactions seeded: {inserted} inserted, {updated} refreshed, "
                f"{removed} removed."
            )
            print()
            report(cursor)
    except psycopg.Error as error:
        connection.rollback()
        raise SystemExit(f"Seeding failed: {scrub(error, database_url)}")
    finally:
        # "finally": the connection is closed even if an error occurs.
        connection.close()


# ---------------------------------------------------------------------------
# 8. Verification report
# ---------------------------------------------------------------------------

def print_breakdown(title, rows, total):
    """Print a labelled count table with percentages."""
    print(f"  {title}")
    for label, count in rows:
        share = count / total * 100 if total else 0
        print(f"      {str(label):26} {count:>7}   {share:5.1f}%")
    print()


def report(cursor):
    """Read the stored data back and print the 10 required checks."""

    total = list(cursor.execute("SELECT COUNT(*) FROM interactions"))[0][0]

    # 1. Total
    print(f"  1. TOTAL INTERACTIONS: {total}")
    print()

    # 2. Per interaction type
    print_breakdown("2. Interactions per type", list(cursor.execute("""
        SELECT interaction_type, COUNT(*) FROM interactions
        GROUP BY interaction_type ORDER BY COUNT(*) DESC
    """)), total)

    # 3. Per channel
    print_breakdown("3. Interactions per channel", list(cursor.execute("""
        SELECT channel, COUNT(*) FROM interactions
        GROUP BY channel ORDER BY COUNT(*) DESC
    """)), total)

    # 4. Average per client segment
    print("  4. Average interactions per client, by segment")
    rows = list(cursor.execute("""
        SELECT c.client_segment,
               COUNT(i.interaction_id) * 1.0 / COUNT(DISTINCT c.client_id),
               COUNT(DISTINCT c.client_id),
               COUNT(i.interaction_id)
        FROM clients c
        LEFT JOIN interactions i ON i.client_id = c.client_id
        GROUP BY c.client_segment
        ORDER BY 2 DESC
    """))
    for segment, average, client_count, interaction_count in rows:
        print(f"      {segment:26} {average:6.2f}   "
              f"({interaction_count} interactions / {client_count} clients)")
    print()

    # 5. Interactions per client
    stats = list(cursor.execute("""
        SELECT MIN(n), MAX(n), AVG(n) FROM (
            SELECT c.client_id, COUNT(i.interaction_id) AS n
            FROM clients c
            LEFT JOIN interactions i ON i.client_id = c.client_id
            GROUP BY c.client_id
        )
    """))[0]
    print("  5. Interactions per client")
    print(f"      minimum                    {stats[0]:>7}")
    print(f"      maximum                    {stats[1]:>7}")
    print(f"      average                    {stats[2]:>10.2f}")
    print()

    # 6. Per client status
    print_breakdown("6. Interactions per client status", list(cursor.execute("""
        SELECT c.client_status, COUNT(i.interaction_id)
        FROM clients c
        LEFT JOIN interactions i ON i.client_id = c.client_id
        GROUP BY c.client_status
        ORDER BY COUNT(i.interaction_id) DESC
    """)), total)

    # 7-10. Integrity checks, all of which must return zero.
    checks = [
        ("7. interaction_date before the client's created_at", """
            SELECT COUNT(*) FROM interactions i
            JOIN clients c ON c.client_id = i.client_id
            WHERE i.interaction_date < c.created_at
        """),
        ("8. advisor_id different from the client's advisor", """
            SELECT COUNT(*) FROM interactions i
            JOIN clients c ON c.client_id = i.client_id
            WHERE i.advisor_id <> c.advisor_id
        """),
        ("9. invalid client_id", """
            SELECT COUNT(*) FROM interactions i
            LEFT JOIN clients c ON c.client_id = i.client_id
            WHERE c.client_id IS NULL
        """),
        ("10. invalid advisor_id", """
            SELECT COUNT(*) FROM interactions i
            LEFT JOIN advisors a ON a.advisor_id = i.advisor_id
            WHERE a.advisor_id IS NULL
        """),
    ]

    failures = 0
    print("  Integrity checks (all must be 0)")
    for label, query in checks:
        value = list(cursor.execute(query))[0][0]
        verdict = "OK" if value == 0 else "FAILED"
        failures += value
        print(f"      {label:52} {value:>6}   [{verdict}]")
    print()

    if total != TOTAL_INTERACTIONS:
        raise SystemExit(f"Expected {TOTAL_INTERACTIONS} interactions, found {total}.")
    if failures:
        raise SystemExit("Integrity checks failed.")


if __name__ == "__main__":
    seed_interactions()

"""
Seed the `advisors` table of the Orialis CRM.

Generates exactly 100 fictional advisors spread across the 7 Orialis branches,
with a fixed distribution:

    BR001 Paris      24        BR004 Geneva     18
    BR002 Lyon       16        BR005 Lausanne   10
    BR003 Brussels   12        BR006 Milan      12
                               BR007 Rome        8
                               ----------------------
                               total           100

The data is generated from name pools with a SEEDED random generator, so the
output is deterministic: running the script twice produces exactly the same
100 advisors. Combined with an UPSERT on advisor_id, the script is idempotent.

Any advisor left over from a previous run (for instance the older 25-advisor
dataset) is removed, so the table always ends up with exactly 100 rows.

Requires `branches` to be seeded first: every advisor references an existing
branch_id, and PostgreSQL enforces that foreign key.

Connection: the DATABASE_URL variable is read from the `.env` file at the
repository root. That value is never printed.

Prerequisites:
    python CRM/scripts/init_db.py
    python CRM/scripts/seed_branches.py

Usage:
    python CRM/scripts/seed_advisors.py
"""

import random
import sys
import unicodedata
from datetime import date, datetime, timedelta
from pathlib import Path

import psycopg

# Shared PostgreSQL configuration: .env loading, DATABASE_URL retrieval and
# validation, credential scrubbing. It lives at CRM/config.py, used by both
# the API and these scripts. The repository root goes on the import path so
# that `CRM.config` resolves the same way it does for the API.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
from CRM.config import get_database_url, scrub  # noqa: E402

EMAIL_DOMAIN = "orialis.com"

# The schema column is `advisor_status` (not `status`) since the rename.
DEFAULT_STATUS = "Active"

# Fixed seed => same 100 advisors on every run. This is what makes a
# generated dataset reproducible instead of changing at each execution.
RNG_SEED = 20260909

# Reference "today" for the generated dates. Hardcoded rather than read from
# the clock, so the dataset does not drift over time.
TODAY = date(2026, 9, 9)


# ---------------------------------------------------------------------------
# 1. Branch plan
# ---------------------------------------------------------------------------
# (branch_id, city, country, advisor_count, phone_template)
#
# The phone template carries the real local area code of each city; the two
# {} slots are filled with generated digits.

BRANCH_PLAN = [
    ("BR001", "Paris",    "France",      24, "+33 1 42 {:02d} {:02d} {:02d}"),
    ("BR002", "Lyon",     "France",      16, "+33 4 72 {:02d} {:02d} {:02d}"),
    ("BR003", "Brussels", "Belgium",     12, "+32 2 5{:02d} {:02d} {:02d}"),
    ("BR004", "Geneva",   "Switzerland", 18, "+41 22 3{:02d} {:02d} {:02d}"),
    ("BR005", "Lausanne", "Switzerland", 10, "+41 21 6{:02d} {:02d} {:02d}"),
    ("BR006", "Milan",    "Italy",       12, "+39 02 76{:02d} {:02d}{:02d}"),
    ("BR007", "Rome",     "Italy",        8, "+39 06 48{:02d} {:02d}{:02d}"),
]

TOTAL_ADVISORS = sum(plan[3] for plan in BRANCH_PLAN)  # 100


# ---------------------------------------------------------------------------
# 2. Name pools, per country
# ---------------------------------------------------------------------------
# Names are written without accents, consistent with the rest of the CRM
# values. Belgian and Swiss pools mix the naming traditions really found in
# Brussels and French-speaking Switzerland.

NAME_POOLS = {
    "France": {
        "first": [
            "Camille", "Julien", "Sophie", "Nicolas", "Claire", "Antoine",
            "Marc", "Manon", "Thomas", "Laurent", "Alice", "Baptiste",
            "Damien", "Fabien", "Hugo", "Justine", "Louis", "Marion",
            "Pauline", "Quentin", "Romain", "Vincent", "Xavier", "Adrien",
            "Melanie", "Julie", "Nathan", "Lucie", "Emma", "Paul",
        ],
        "last": [
            "Laurent", "Moreau", "Bertrand", "Girard", "Fontaine", "Chevalier",
            "Dubois", "Rousseau", "Perrin", "Marchand", "Lefevre", "Garnier",
            "Faure", "Mercier", "Blanchard", "Roux", "Vidal", "Colin",
            "Barbier", "Renard", "Leroy", "Morel", "Fournier", "Bonnet",
            "Dupont", "Caron", "Guerin", "Robin", "Noel", "Masson",
        ],
    },
    "Belgium": {
        "first": [
            "Olivier", "Sarah", "Maxime", "Lucas", "Elise", "Gilles",
            "Charlotte", "Arnaud", "Aurore", "Simon", "Nathalie", "Bruno",
            "Valerie", "Kevin", "Axelle", "Denis", "Marine", "Thibault",
        ],
        "last": [
            "Willems", "Peeters", "Dumont", "Lambert", "Claes", "Wouters",
            "Michiels", "Jacobs", "Dupuis", "Delvaux", "Vandenberg", "Maes",
            "Verhoeven", "Goossens", "Servais", "Hendrickx", "Lejeune", "Simons",
        ],
    },
    "Switzerland": {
        "first": [
            "Philippe", "Sandrine", "Vincent", "Nathalie", "Guillaume", "Laura",
            "Yann", "Anouk", "Sebastien", "Isabelle", "Fabrice", "Melissa",
            "Ludovic", "Carole", "Didier", "Sylvie", "Jonas", "Aline",
            "Patrick", "Corinne", "Michel", "Sophie", "Gregoire", "Delphine",
        ],
        "last": [
            "Baumann", "Favre", "Rochat", "Blanc", "Perret", "Meylan",
            "Dubath", "Girod", "Chappuis", "Bovet", "Nicolet", "Gaillard",
            "Jaquet", "Monnier", "Berger", "Zbinden", "Currat", "Pittet",
            "Rey", "Grosjean", "Aebischer", "Duperret", "Vionnet", "Terrier",
        ],
    },
    "Italy": {
        "first": [
            "Marco", "Giulia", "Alessandro", "Francesca", "Lorenzo", "Chiara",
            "Matteo", "Elena", "Davide", "Sara", "Andrea", "Valentina",
            "Luca", "Martina", "Stefano", "Silvia", "Riccardo", "Beatrice",
            "Giovanni", "Federica",
        ],
        "last": [
            "Ferrari", "Romano", "Conti", "Greco", "Bianchi", "Rossi",
            "Russo", "Esposito", "Colombo", "Ricci", "Marino", "Bruno",
            "Gallo", "Costa", "Fontana", "Rizzo", "Moretti", "Barbieri",
            "Lombardi", "Villa",
        ],
    },
}


# ---------------------------------------------------------------------------
# 3. Business values
# ---------------------------------------------------------------------------

SPECIALIZATIONS = [
    "Wealth Management",
    "Investment Advisory",
    "Retirement Planning",
    "Estate Planning",
    "International Clients",
]

# (job_title, weight). Exactly one Branch Manager per branch is assigned
# separately, so it is not part of this weighted pool.
JOB_TITLES = [
    ("Senior Wealth Advisor", 3),
    ("Wealth Advisor", 6),
    ("Investment Advisor", 4),
    ("Private Banker", 2),
    ("Client Relationship Manager", 2),
    ("Junior Advisor", 2),
]

# Language profiles per country: (primary_language, other_languages, weight).
# Languages stay consistent with where the advisor works: French in France,
# Belgium and French-speaking Switzerland, Italian in Italy. English is the
# common business language; Geneva and Milan are the most international.
LANGUAGE_PROFILES = {
    "France": [
        ("FR", ["EN"], 6),
        ("FR", [], 2),
        ("FR", ["EN", "IT"], 2),
        ("FR", ["IT"], 1),
    ],
    "Belgium": [
        ("FR", ["EN"], 7),
        ("FR", [], 2),
        ("FR", ["EN", "IT"], 1),
    ],
    "Switzerland": [
        ("FR", ["EN"], 6),
        ("FR", ["EN", "IT"], 3),
        ("FR", [], 1),
    ],
    "Italy": [
        ("IT", ["EN"], 6),
        ("IT", ["EN", "FR"], 3),
        ("IT", [], 2),
    ],
}

# Order used for the secondary languages, so the same set of languages is
# always written the same way. Without a rule like this, "FR,EN,IT" and
# "FR,IT,EN" would both appear and break any equality filter.
LANGUAGE_ORDER = ["EN", "FR", "IT"]


# ---------------------------------------------------------------------------
# 4. Helpers
# ---------------------------------------------------------------------------

def build_email(first_name, last_name, taken):
    """Build a unique professional address: first.last@orialis.com

    NFKD normalisation strips diacritics so the address stays valid ASCII.
    If two advisors share a name, a number is appended (first.last2@...),
    which is what a real company directory does.
    """
    raw = f"{first_name}.{last_name}".lower()
    decomposed = unicodedata.normalize("NFKD", raw)
    local_part = "".join(c for c in decomposed if not unicodedata.combining(c))

    candidate = f"{local_part}@{EMAIL_DOMAIN}"
    suffix = 1
    while candidate in taken:
        suffix += 1
        candidate = f"{local_part}{suffix}@{EMAIL_DOMAIN}"
    return candidate


def format_languages(primary, others):
    """Write a language list: primary first, the rest in a fixed order."""
    ordered_others = sorted(others, key=LANGUAGE_ORDER.index)
    return ",".join([primary] + ordered_others)


def weighted_choice(rng, weighted_items):
    """Pick one item from a list of (value, weight) tuples."""
    values = [value for value, _ in weighted_items]
    weights = [weight for _, weight in weighted_items]
    return rng.choices(values, weights=weights, k=1)[0]


def random_date(rng, start_year, end_year):
    """Pick a date between 1 January start_year and 31 December end_year,
    never later than TODAY (hire dates must be in the past)."""
    start = date(start_year, 1, 1)
    end = min(date(end_year, 12, 31), TODAY - timedelta(days=1))
    span = (end - start).days
    return start + timedelta(days=rng.randint(0, span))


# ---------------------------------------------------------------------------
# 5. Dataset generation
# ---------------------------------------------------------------------------

def generate_advisors():
    """Build the list of 100 advisor rows, ready to be inserted."""

    rng = random.Random(RNG_SEED)

    advisors = []
    used_names = set()
    used_emails = set()
    used_phones = set()
    advisor_number = 0

    for branch_id, _city, country, headcount, phone_template in BRANCH_PLAN:
        pool = NAME_POOLS[country]

        for position in range(headcount):
            advisor_number += 1
            advisor_id = f"ADV{advisor_number:03d}"

            # --- name: resample until the full name is unique -------------
            while True:
                first_name = rng.choice(pool["first"])
                last_name = rng.choice(pool["last"])
                if (first_name, last_name) not in used_names:
                    used_names.add((first_name, last_name))
                    break

            email = build_email(first_name, last_name, used_emails)
            used_emails.add(email)

            # --- phone: unique within the whole dataset -------------------
            while True:
                phone = phone_template.format(
                    rng.randint(10, 99), rng.randint(10, 99), rng.randint(10, 99)
                )
                if phone not in used_phones:
                    used_phones.add(phone)
                    break

            # --- job title: exactly one Branch Manager per branch ---------
            if position == 0:
                job_title = "Branch Manager"
                # Managers are the most senior people in the branch.
                hire_date = random_date(rng, 2010, 2016)
            else:
                job_title = weighted_choice(rng, JOB_TITLES)
                if job_title in ("Senior Wealth Advisor", "Private Banker"):
                    hire_date = random_date(rng, 2011, 2019)
                elif job_title == "Junior Advisor":
                    hire_date = random_date(rng, 2022, 2025)
                else:
                    hire_date = random_date(rng, 2014, 2024)

            # --- specialization ------------------------------------------
            specialization = rng.choice(SPECIALIZATIONS)

            # --- spoken languages, consistent with the country ------------
            primary, others, _ = weighted_choice(
                rng,
                [(profile, profile[2]) for profile in LANGUAGE_PROFILES[country]],
            )
            spoken_languages = format_languages(primary, others)

            # --- updated_at: a plausible recent CRM edit -------------------
            # Kept as a real datetime, not a formatted string: the column is
            # TIMESTAMPTZ and psycopg adapts the object directly. The session
            # runs in UTC, so a naive value is read as UTC.
            updated_day = random_date(rng, 2026, 2026)
            updated_at = datetime(
                updated_day.year, updated_day.month, updated_day.day,
                rng.randint(8, 18), rng.randint(0, 59), rng.randint(0, 59),
            )

            advisors.append((
                advisor_id, first_name, last_name, email, phone, branch_id,
                job_title, specialization, spoken_languages,
                hire_date, DEFAULT_STATUS, updated_at,
            ))

    return advisors


# ---------------------------------------------------------------------------
# 6. SQL
# ---------------------------------------------------------------------------

UPSERT_ADVISOR = """
INSERT INTO advisors (
    advisor_id, first_name, last_name, email, phone, branch_id,
    job_title, specialization, spoken_languages, hire_date,
    advisor_status, updated_at
)
VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
ON CONFLICT (advisor_id) DO UPDATE SET
    first_name       = excluded.first_name,
    last_name        = excluded.last_name,
    email            = excluded.email,
    phone            = excluded.phone,
    branch_id        = excluded.branch_id,
    job_title        = excluded.job_title,
    specialization   = excluded.specialization,
    spoken_languages = excluded.spoken_languages,
    hire_date        = excluded.hire_date,
    advisor_status   = excluded.advisor_status,
    updated_at       = excluded.updated_at;
"""


def seed_advisors():
    """Insert or refresh the 100 Orialis advisors."""

    advisors = generate_advisors()
    database_url = get_database_url()

    # No PRAGMA here: PostgreSQL always enforces foreign keys. An advisor
    # pointing at a branch that does not exist is rejected by the server,
    # with no per-connection setting to remember.
    try:
        connection = psycopg.connect(database_url)
    except psycopg.OperationalError as error:
        raise SystemExit(
            "Could not connect to PostgreSQL.\n"
            f"  {scrub(error, database_url)}"
        )

    try:
        with connection.cursor() as cursor:
            # Advisors depend on branches, so check the parent table first and
            # fail with a clear message rather than a raw constraint error.
            try:
                cursor.execute("SELECT branch_id FROM branches")
            except psycopg.errors.UndefinedTable:
                connection.rollback()
                raise SystemExit(
                    "The `branches` table does not exist.\n"
                    "Run 'python CRM/scripts/init_db.py' first."
                )
            known_branches = {row[0] for row in cursor.fetchall()}

            if not known_branches:
                raise SystemExit(
                    "The `branches` table is empty.\n"
                    "Run 'python CRM/scripts/seed_branches.py' first."
                )

            missing = sorted({advisor[5] for advisor in advisors} - known_branches)
            if missing:
                raise SystemExit(f"Unknown branch_id referenced by advisors: {missing}")

            cursor.execute("SELECT advisor_id FROM advisors")
            existing_ids = {row[0] for row in cursor.fetchall()}
            wanted_ids = [advisor[0] for advisor in advisors]

            # Drop advisors left over from an earlier, different dataset, so
            # the table ends up with exactly the generated headcount and not
            # a mix of the two.
            #
            # `= ANY(%s)` passes the whole list as ONE parameter, which is the
            # PostgreSQL way. SQLite needed a generated "?,?,?,..." string -
            # 100 placeholders here, and 5,000 in the clients seed.
            cursor.execute(
                "DELETE FROM advisors WHERE NOT (advisor_id = ANY(%s))",
                (wanted_ids,),
            )
            removed = cursor.rowcount

            inserted = updated = 0
            for advisor in advisors:
                cursor.execute(UPSERT_ADVISOR, advisor)
                if advisor[0] in existing_ids:
                    updated += 1
                else:
                    inserted += 1

            connection.commit()

            # Read the data back, to report on what is actually stored.
            cursor.execute("SELECT COUNT(*) FROM advisors")
            total = cursor.fetchone()[0]

            cursor.execute("""
                SELECT b.branch_id, b.branch_name, b.country, COUNT(a.advisor_id)
                FROM branches b
                LEFT JOIN advisors a ON a.branch_id = b.branch_id
                GROUP BY b.branch_id, b.branch_name, b.country
                ORDER BY b.branch_id
            """)
            per_branch = cursor.fetchall()

            cursor.execute("""
                SELECT spoken_languages, COUNT(*)
                FROM advisors
                GROUP BY spoken_languages
                ORDER BY COUNT(*) DESC, spoken_languages
            """)
            languages = cursor.fetchall()
    except psycopg.Error as error:
        connection.rollback()
        raise SystemExit(f"Seeding failed: {scrub(error, database_url)}")
    finally:
        # "finally": the connection is closed even if an error occurs.
        connection.close()

    print(
        f"Advisors seeded: {inserted} inserted, {updated} refreshed, "
        f"{removed} removed."
    )
    print()
    print(f"  TOTAL ADVISORS: {total}")
    print()
    print("  Advisors per branch")
    print(f"  {'ID':6} {'BRANCH':20} {'COUNTRY':13} ADVISORS")
    print(f"  {'-' * 6} {'-' * 20} {'-' * 13} --------")
    for branch_id, branch_name, country, count in per_branch:
        print(f"  {branch_id:6} {branch_name:20} {country:13} {count:>5}")
    print(f"  {'-' * 6} {'-' * 20} {'-' * 13} --------")
    print(f"  {'':6} {'TOTAL':20} {'':13} {total:>5}")
    print()
    print("  Spoken languages")
    for combination, count in languages:
        print(f"      {combination:12} {count:>4}")

    if total != TOTAL_ADVISORS:
        raise SystemExit(f"Expected {TOTAL_ADVISORS} advisors, found {total}.")


if __name__ == "__main__":
    seed_advisors()

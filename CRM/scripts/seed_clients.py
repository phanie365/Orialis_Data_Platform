"""
Seed the `clients` table of the Orialis CRM.

Generates exactly 5,000 fictional clients, attached to the advisors already
stored in the database.

Target distributions (exact counts, not approximations):

    country_of_residence   France 2500 | Switzerland 1100 | Belgium 750 | Italy 650
    client_segment         Standard 3250 | Patrimonial 1350 | Private Banking 400
    risk_profile           Conservative 1500 | Balanced 2500 | Growth 1000
    client_status          Active 4400 | Inactive 400 | Prospect 200

The exact totals AND the realistic correlations (older clients lean
Conservative, Paris/Geneva concentrate Private Banking, ...) are obtained with
weighted sampling WITHOUT replacement: the quota fixes how many rows get a
value, the weights decide which rows are the most likely to get it.

The data is generated from a SEEDED random generator, so the output is
deterministic: running the script twice produces exactly the same 5,000
clients. Any client left over from a previous run is removed, so the table
always ends up with exactly 5,000 rows.

Requires `branches` and `advisors` to be seeded first.

Prerequisites:
    python CRM/scripts/init_db.py
    python CRM/scripts/seed_branches.py
    python CRM/scripts/seed_advisors.py

Usage:
    python CRM/scripts/seed_clients.py
"""

import random
import sqlite3
import unicodedata
from datetime import date, datetime, timedelta
from pathlib import Path

CRM_DIR = Path(__file__).parent.parent
DB_PATH = CRM_DIR / "data" / "orialis_crm.db"

# Fixed seed => same 5,000 clients on every run.
RNG_SEED = 20260910

# Reference "today". Hardcoded rather than read from the clock, so the
# dataset does not drift over time.
TODAY = date(2026, 9, 9)

TOTAL_CLIENTS = 5000


# ---------------------------------------------------------------------------
# 1. Target distributions
# ---------------------------------------------------------------------------
# The schema columns are `client_segment` (not `segment`) and `updated_at`
# (not `last_updated`).

COUNTRY_QUOTAS = {
    "France": 2500,
    "Switzerland": 1100,
    "Belgium": 750,
    "Italy": 650,
}

SEGMENT_QUOTAS = {
    "Standard": 3250,
    "Patrimonial": 1350,
    "Private Banking": 400,
}

RISK_QUOTAS = {
    "Conservative": 1500,
    "Balanced": 2500,
    "Growth": 1000,
}

STATUS_QUOTAS = {
    "Active": 4400,
    "Inactive": 400,
    "Prospect": 200,
}


# ---------------------------------------------------------------------------
# 2. Geography
# ---------------------------------------------------------------------------
# (city, weight) - bigger cities receive proportionally more clients.

CITIES = {
    "France": [
        ("Paris", 30), ("Lyon", 12), ("Marseille", 8), ("Toulouse", 7),
        ("Bordeaux", 7), ("Lille", 6), ("Nice", 6), ("Nantes", 6),
        ("Strasbourg", 5), ("Montpellier", 4), ("Rennes", 4), ("Grenoble", 3),
        ("Annecy", 2),
    ],
    "Switzerland": [
        ("Geneva", 26), ("Lausanne", 20), ("Zurich", 15), ("Basel", 8),
        ("Bern", 7), ("Lugano", 7), ("Fribourg", 5), ("Neuchatel", 5),
        ("Sion", 4), ("Montreux", 3),
    ],
    "Belgium": [
        ("Brussels", 34), ("Antwerp", 13), ("Liege", 12), ("Ghent", 9),
        ("Charleroi", 8), ("Namur", 8), ("Bruges", 5), ("Leuven", 5),
        ("Mons", 4), ("Wavre", 2),
    ],
    "Italy": [
        ("Milan", 26), ("Rome", 22), ("Turin", 11), ("Bologna", 8),
        ("Florence", 8), ("Naples", 7), ("Genoa", 5), ("Verona", 5),
        ("Venice", 4), ("Padua", 4),
    ],
}

# Nationality of residents, per country of residence. Nationality is NOT
# automatically the country of residence: these regions have large resident
# populations from neighbouring and southern European countries.
NATIONALITY_MIX = {
    "France": [
        ("French", 84), ("Portuguese", 4), ("Italian", 3), ("Spanish", 2),
        ("Belgian", 2), ("British", 2), ("German", 1), ("Swiss", 2),
    ],
    "Switzerland": [
        ("Swiss", 72), ("French", 8), ("Italian", 7), ("German", 5),
        ("Portuguese", 4), ("British", 2), ("Spanish", 1), ("Belgian", 1),
    ],
    "Belgium": [
        ("Belgian", 80), ("French", 7), ("Italian", 4), ("Dutch", 3),
        ("Portuguese", 2), ("Spanish", 2), ("British", 1), ("German", 1),
    ],
    "Italy": [
        ("Italian", 85), ("Swiss", 3), ("French", 3), ("German", 3),
        ("Spanish", 2), ("British", 2), ("Belgian", 1), ("Portuguese", 1),
    ],
}

# preferred_language is driven by geography, never drawn at random.
# Switzerland is the most mixed (Geneva/Lausanne are French-speaking, Lugano
# is Italian-speaking, Zurich/Basel lean towards English for banking).
LANGUAGE_MIX = {
    "France": [("FR", 88), ("EN", 8), ("IT", 4)],
    "Belgium": [("FR", 90), ("EN", 10)],
    "Switzerland": [("FR", 70), ("EN", 18), ("IT", 12)],
    "Italy": [("IT", 88), ("EN", 8), ("FR", 4)],
}

PHONE_TEMPLATES = {
    "France": "+33 {:1d} {:02d} {:02d} {:02d} {:02d}",
    "Belgium": "+32 4{:02d} {:02d} {:02d} {:02d}",
    "Switzerland": "+41 7{:1d} {:03d} {:02d} {:02d}",
    "Italy": "+39 3{:02d} {:03d} {:04d}",
}

EMAIL_DOMAINS = {
    "France": ["gmail.com", "orange.fr", "free.fr", "sfr.fr", "outlook.fr", "laposte.net"],
    "Belgium": ["gmail.com", "proximus.be", "telenet.be", "skynet.be", "outlook.com"],
    "Switzerland": ["gmail.com", "bluewin.ch", "swisscom.ch", "sunrise.ch", "outlook.com"],
    "Italy": ["gmail.com", "libero.it", "virgilio.it", "alice.it", "outlook.it"],
}


# ---------------------------------------------------------------------------
# 3. Name pools, per nationality
# ---------------------------------------------------------------------------
# The name follows the NATIONALITY, not the country of residence: an Italian
# living in Geneva keeps an Italian name. Written without accents, consistent
# with the rest of the CRM values.

NAME_POOLS = {
    "French": {
        "first": [
            "Camille", "Julien", "Sophie", "Nicolas", "Claire", "Antoine", "Marc",
            "Manon", "Thomas", "Laurent", "Alice", "Baptiste", "Damien", "Fabien",
            "Hugo", "Justine", "Louis", "Marion", "Pauline", "Quentin", "Romain",
            "Vincent", "Xavier", "Adrien", "Melanie", "Julie", "Nathan", "Lucie",
            "Emma", "Paul", "Sandrine", "Olivier", "Amandine", "Guillaume",
            "Caroline", "Sebastien", "Aurelie", "Mathieu", "Christelle", "David",
        ],
        "last": [
            "Martin", "Bernard", "Thomas", "Petit", "Robert", "Richard", "Durand",
            "Dubois", "Moreau", "Laurent", "Simon", "Michel", "Lefevre", "Leroy",
            "Roux", "David", "Bertrand", "Morel", "Fournier", "Girard", "Bonnet",
            "Dupont", "Lambert", "Fontaine", "Rousseau", "Vincent", "Muller",
            "Mercier", "Boyer", "Blanc", "Guerin", "Chevalier", "Francois",
            "Legrand", "Garnier", "Faure", "Roussel", "Colin", "Perrin", "Renard",
        ],
    },
    "Belgian": {
        "first": [
            "Olivier", "Sarah", "Maxime", "Lucas", "Elise", "Gilles", "Charlotte",
            "Arnaud", "Aurore", "Simon", "Nathalie", "Bruno", "Valerie", "Kevin",
            "Axelle", "Denis", "Marine", "Thibault", "Geoffrey", "Laetitia",
            "Jonathan", "Celia", "Fabrice", "Noemie",
        ],
        "last": [
            "Peeters", "Janssens", "Maes", "Jacobs", "Willems", "Claes", "Wouters",
            "Goossens", "Michiels", "Hendrickx", "Dupont", "Dubois", "Lambert",
            "Dumont", "Delvaux", "Servais", "Lejeune", "Simons", "Verhoeven",
            "Vandenberg", "Dupuis", "Leclercq", "Hermans", "Segers",
        ],
    },
    "Swiss": {
        "first": [
            "Philippe", "Sandrine", "Vincent", "Nathalie", "Guillaume", "Laura",
            "Yann", "Anouk", "Sebastien", "Isabelle", "Fabrice", "Melissa",
            "Ludovic", "Carole", "Didier", "Sylvie", "Jonas", "Aline", "Patrick",
            "Corinne", "Michel", "Gregoire", "Delphine", "Raphael", "Chantal",
            "Nicole", "Pascal", "Valentin",
        ],
        "last": [
            "Mueller", "Meier", "Schmid", "Keller", "Weber", "Favre", "Rochat",
            "Blanc", "Perret", "Meylan", "Chappuis", "Bovet", "Nicolet",
            "Gaillard", "Jaquet", "Monnier", "Berger", "Zbinden", "Currat",
            "Pittet", "Rey", "Grosjean", "Aebischer", "Vionnet", "Terrier",
            "Baumann", "Girod", "Dubath",
        ],
    },
    "Italian": {
        "first": [
            "Marco", "Giulia", "Alessandro", "Francesca", "Lorenzo", "Chiara",
            "Matteo", "Elena", "Davide", "Sara", "Andrea", "Valentina", "Luca",
            "Martina", "Stefano", "Silvia", "Riccardo", "Beatrice", "Giovanni",
            "Federica", "Antonio", "Paola", "Simone", "Alessia", "Fabio",
            "Cristina", "Emanuele", "Ilaria",
        ],
        "last": [
            "Rossi", "Russo", "Ferrari", "Esposito", "Bianchi", "Romano",
            "Colombo", "Ricci", "Marino", "Greco", "Bruno", "Gallo", "Conti",
            "De Luca", "Costa", "Giordano", "Mancini", "Rizzo", "Lombardi",
            "Moretti", "Barbieri", "Fontana", "Santoro", "Mariani", "Rinaldi",
            "Caruso", "Ferrara", "Villa",
        ],
    },
    "Portuguese": {
        "first": [
            "Joao", "Maria", "Pedro", "Ana", "Tiago", "Ines", "Ricardo", "Sofia",
            "Bruno", "Catarina", "Miguel", "Beatriz",
        ],
        "last": [
            "Silva", "Santos", "Ferreira", "Pereira", "Oliveira", "Costa",
            "Rodrigues", "Martins", "Sousa", "Fernandes", "Goncalves", "Lopes",
        ],
    },
    "Spanish": {
        "first": [
            "Carlos", "Lucia", "Javier", "Marta", "Alberto", "Elena", "Sergio",
            "Carmen", "Pablo", "Nuria", "Diego", "Rocio",
        ],
        "last": [
            "Garcia", "Fernandez", "Lopez", "Martinez", "Sanchez", "Perez",
            "Gomez", "Ruiz", "Diaz", "Alvarez", "Romero", "Navarro",
        ],
    },
    "German": {
        "first": [
            "Stefan", "Anja", "Markus", "Petra", "Thomas", "Sabine", "Andreas",
            "Claudia", "Jonas", "Katrin", "Lukas", "Nadine",
        ],
        "last": [
            "Schneider", "Fischer", "Wagner", "Becker", "Hoffmann", "Schulz",
            "Koch", "Bauer", "Richter", "Klein", "Wolf", "Neumann",
        ],
    },
    "British": {
        "first": [
            "James", "Emily", "Oliver", "Charlotte", "William", "Sophie",
            "Henry", "Olivia", "George", "Hannah", "Edward", "Rachel",
        ],
        "last": [
            "Smith", "Jones", "Taylor", "Brown", "Wilson", "Davies", "Evans",
            "Thomas", "Roberts", "Walker", "Wright", "Hughes",
        ],
    },
    "Dutch": {
        "first": [
            "Sven", "Anouk", "Bram", "Femke", "Joost", "Sanne", "Ruben", "Lotte",
        ],
        "last": [
            "De Vries", "Van Dijk", "Bakker", "Visser", "Smit", "Meijer",
            "Mulder", "Bos",
        ],
    },
}


# ---------------------------------------------------------------------------
# 4. Advisor workload model
# ---------------------------------------------------------------------------
# Each client consumes "capacity units" from its advisor. A Private Banking
# client is far more demanding than a Standard one, so an advisor with many
# Private Banking clients naturally ends up with a SMALLER total portfolio.
# The variation in workload is an emergent property of this model, not a
# number written by hand.

SEGMENT_COST = {
    "Standard": 1.0,
    "Patrimonial": 2.4,
    "Private Banking": 7.0,
}

# Each advisor runs a portfolio in one of three styles. This is the real
# driver of workload: a private banker and a volume advisor do not do the same
# job. `capacity` is the time they have; the segment weights say which kind of
# client they attract. Because a Private Banking client costs 7 units against
# 1 for a Standard one, a private-banking heavy portfolio ends up holding far
# FEWER clients - which is exactly the business rule.
PORTFOLIO_STYLES = {
    "Private": {
        "capacity": 1.35,
        "segments": {"Private Banking": 8.0, "Patrimonial": 1.0, "Standard": 0.15},
    },
    "Patrimonial": {
        "capacity": 1.00,
        "segments": {"Private Banking": 1.0, "Patrimonial": 2.5, "Standard": 0.70},
    },
    "Volume": {
        "capacity": 1.00,
        "segments": {"Private Banking": 0.10, "Patrimonial": 0.70, "Standard": 1.80},
    },
}

# Which style an advisor is likely to run, given their job title.
STYLE_BY_JOB = {
    "Branch Manager":              [("Private", 3), ("Patrimonial", 4), ("Volume", 2)],
    "Private Banker":              [("Private", 9), ("Patrimonial", 2), ("Volume", 1)],
    "Senior Wealth Advisor":       [("Private", 3), ("Patrimonial", 5), ("Volume", 3)],
    "Client Relationship Manager": [("Private", 1), ("Patrimonial", 3), ("Volume", 5)],
    "Wealth Advisor":              [("Private", 1), ("Patrimonial", 4), ("Volume", 6)],
    "Investment Advisor":          [("Private", 1), ("Patrimonial", 3), ("Volume", 6)],
    "Junior Advisor":              [("Private", 0), ("Patrimonial", 1), ("Volume", 8)],
}

# Specializations that pull an advisor towards a private-banking practice.
PRIVATE_SPECIALIZATIONS = {"International Clients", "Estate Planning"}

# Managers spend part of their time managing; juniors are still ramping up.
JOB_CAPACITY_FACTOR = {
    "Branch Manager": 0.45,
    "Junior Advisor": 0.70,
    "Private Banker": 0.85,
    "Senior Wealth Advisor": 1.10,
    "Client Relationship Manager": 1.05,
    "Wealth Advisor": 1.20,
    "Investment Advisor": 1.15,
}

# How well an advisor's specialization matches a client's segment.
SEGMENT_AFFINITY = {
    "Private Banking": {
        "International Clients": 2.4,
        "Estate Planning": 2.0,
        "Wealth Management": 1.6,
        "Investment Advisory": 1.0,
        "Retirement Planning": 0.7,
    },
    "Patrimonial": {
        "Estate Planning": 1.8,
        "Retirement Planning": 1.7,
        "Wealth Management": 1.6,
        "Investment Advisory": 1.1,
        "International Clients": 0.9,
    },
    "Standard": {
        "Retirement Planning": 1.3,
        "Investment Advisory": 1.2,
        "Wealth Management": 1.1,
        "Estate Planning": 0.9,
        "International Clients": 0.6,
    },
}

# Paris and Geneva are the private-banking hubs of the network.
PRIVATE_BANKING_HUBS = {"BR001", "BR004"}
HUB_PRIVATE_BANKING_BONUS = 3.0

# Share of clients handled by an advisor outside their country of residence.
CROSS_BORDER_RATE = 0.10


# ---------------------------------------------------------------------------
# 5. Sampling helpers
# ---------------------------------------------------------------------------

def weighted_pick(rng, weighted_items):
    """Pick one value from a list of (value, weight) tuples."""
    values = [value for value, _ in weighted_items]
    weights = [weight for _, weight in weighted_items]
    return rng.choices(values, weights=weights, k=1)[0]


def weighted_sample_indices(rng, weights, k):
    """Pick exactly k indices, without replacement, favouring high weights.

    Uses the Efraimidis-Spirakis method: give each candidate the key
    U^(1/weight) and keep the k largest. A candidate with twice the weight is
    twice as likely to be picked, but the total drawn is exactly k - which is
    what lets us hit an exact quota AND keep a realistic bias.
    """
    keys = []
    for index, weight in enumerate(weights):
        weight = max(weight, 1e-9)
        keys.append((rng.random() ** (1.0 / weight), index))
    keys.sort(reverse=True)
    return [index for _, index in keys[:k]]


def build_quota_list(rng, quotas):
    """Turn {value: count} into a shuffled flat list of that exact length."""
    values = []
    for value, count in quotas.items():
        values.extend([value] * count)
    rng.shuffle(values)
    return values


def strip_accents(text):
    """Fold text down to plain ASCII, for email addresses."""
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(c for c in decomposed if not unicodedata.combining(c))


def random_datetime_between(rng, start_date, end_date):
    """A timestamp uniformly drawn between two dates (business hours)."""
    span = max((end_date - start_date).days, 0)
    day = start_date + timedelta(days=rng.randint(0, span))
    return datetime(
        day.year, day.month, day.day,
        rng.randint(8, 19), rng.randint(0, 59), rng.randint(0, 59),
    )


# ---------------------------------------------------------------------------
# 6. Advisor loading
# ---------------------------------------------------------------------------

def load_advisors(cursor):
    """Read the advisors already stored, with their branch context."""
    rows = list(cursor.execute("""
        SELECT a.advisor_id, a.branch_id, a.job_title, a.specialization,
               a.spoken_languages, b.country, b.city
        FROM advisors a
        JOIN branches b ON b.branch_id = a.branch_id
        ORDER BY a.advisor_id
    """))

    advisors = []
    for advisor_id, branch_id, job_title, specialization, languages, country, city in rows:
        advisors.append({
            "advisor_id": advisor_id,
            "branch_id": branch_id,
            "job_title": job_title,
            "specialization": specialization,
            "languages": set(languages.split(",")),
            "country": country,
            "city": city,
            "capacity": 0.0,
            "load": 0.0,
            "client_count": 0,
        })
    return advisors


def assign_capacities(rng, advisors):
    """Give each advisor a personal capacity, so workloads vary naturally."""
    for advisor in advisors:
        # Portfolio style, nudged by the advisor's specialization.
        style_weights = list(STYLE_BY_JOB[advisor["job_title"]])
        if advisor["specialization"] in PRIVATE_SPECIALIZATIONS:
            style_weights = [
                (style, weight * (2.5 if style == "Private" else 1.0))
                for style, weight in style_weights
            ]
        style = weighted_pick(rng, style_weights)
        advisor["style"] = style

        # Lognormal spread: most advisors near the average, a few clearly
        # bigger, a few clearly smaller - the shape real portfolios have.
        personal = rng.lognormvariate(0.0, 0.32)
        advisor["capacity"] = (
            personal
            * PORTFOLIO_STYLES[style]["capacity"]
            * JOB_CAPACITY_FACTOR[advisor["job_title"]]
        )

    # Normalise per country, so each country's advisors can absorb the
    # clients living there (plus headroom for cross-border assignments).
    # Average cost of a client, given the segment mix:
    # (3250*1 + 1350*2.4 + 400*7) / 5000 = 1.86 units.
    average_cost = sum(
        SEGMENT_COST[segment] * count for segment, count in SEGMENT_QUOTAS.items()
    ) / TOTAL_CLIENTS
    demand_units = {
        country: quota * average_cost
        for country, quota in COUNTRY_QUOTAS.items()
    }
    for country, units in demand_units.items():
        local = [a for a in advisors if a["country"] == country]
        total = sum(a["capacity"] for a in local)
        scale = (units * 1.25) / total
        for advisor in local:
            advisor["capacity"] *= scale


# ---------------------------------------------------------------------------
# 7. Client generation
# ---------------------------------------------------------------------------

def generate_clients(advisors, rng):
    """Build the list of 5,000 client rows, ready to be inserted."""

    # --- countries, exact quotas ------------------------------------------
    countries = build_quota_list(rng, COUNTRY_QUOTAS)

    # --- ages first: the risk profile depends on them ---------------------
    ages = []
    for _ in range(TOTAL_CLIENTS):
        age = int(rng.gauss(52, 15))
        ages.append(min(max(age, 18), 92))  # every client is an adult

    # --- segment: exact quotas, biased towards France and Switzerland -----
    # Private Banking stays a small minority (400 of 5,000), concentrated
    # where the wealth-management hubs are.
    pb_country_weight = {"France": 1.4, "Switzerland": 1.9, "Belgium": 0.6, "Italy": 0.7}
    pb_weights = [
        pb_country_weight[countries[i]] * (1.0 + max(ages[i] - 45, 0) / 60.0)
        for i in range(TOTAL_CLIENTS)
    ]
    segments = [None] * TOTAL_CLIENTS
    for index in weighted_sample_indices(rng, pb_weights, SEGMENT_QUOTAS["Private Banking"]):
        segments[index] = "Private Banking"

    remaining = [i for i in range(TOTAL_CLIENTS) if segments[i] is None]
    patrimonial_weights = [1.0 + max(ages[i] - 40, 0) / 70.0 for i in remaining]
    chosen = weighted_sample_indices(rng, patrimonial_weights, SEGMENT_QUOTAS["Patrimonial"])
    for position in chosen:
        segments[remaining[position]] = "Patrimonial"

    for index in range(TOTAL_CLIENTS):
        if segments[index] is None:
            segments[index] = "Standard"

    # --- risk profile: exact quotas, softly correlated with age -----------
    # Older -> more likely Conservative, younger -> more likely Growth, and
    # Balanced stays the most common profile at every age (2,500 of 5,000).
    risk_profiles = [None] * TOTAL_CLIENTS

    conservative_weights = [0.5 + (age - 18) / 74.0 * 1.6 for age in ages]
    for index in weighted_sample_indices(rng, conservative_weights, RISK_QUOTAS["Conservative"]):
        risk_profiles[index] = "Conservative"

    remaining = [i for i in range(TOTAL_CLIENTS) if risk_profiles[i] is None]
    growth_weights = [2.1 - (ages[i] - 18) / 74.0 * 1.6 for i in remaining]
    for position in weighted_sample_indices(rng, growth_weights, RISK_QUOTAS["Growth"]):
        risk_profiles[remaining[position]] = "Growth"

    for index in range(TOTAL_CLIENTS):
        if risk_profiles[index] is None:
            risk_profiles[index] = "Balanced"

    # --- status: exact quotas --------------------------------------------
    statuses = build_quota_list(rng, STATUS_QUOTAS)

    # --- per-client details ----------------------------------------------
    assign_capacities(rng, advisors)

    advisors_by_country = {}
    for advisor in advisors:
        advisors_by_country.setdefault(advisor["country"], []).append(advisor)

    used_emails = set()
    used_phones = set()
    pending = []

    for index in range(TOTAL_CLIENTS):
        client_id = f"CLT{index + 1:04d}"
        country = countries[index]
        segment = segments[index]
        age = ages[index]

        city = weighted_pick(rng, CITIES[country])
        nationality = weighted_pick(rng, NATIONALITY_MIX[country])
        preferred_language = weighted_pick(rng, LANGUAGE_MIX[country])

        # Name follows the nationality, not the country of residence.
        pool = NAME_POOLS[nationality]
        first_name = rng.choice(pool["first"])
        last_name = rng.choice(pool["last"])

        email = build_client_email(rng, first_name, last_name, country, used_emails)
        used_emails.add(email)

        phone = build_phone(rng, country, used_phones)
        used_phones.add(phone)

        # Birth date: counted backwards in DAYS from today, not by subtracting
        # years. Subtracting years then picking a random day would produce
        # someone "aged 18" born in December, who is still 17 today.
        days_lived = int(age * 365.25) + rng.randint(0, 364)
        birth_date = TODAY - timedelta(days=days_lived)

        created_at, updated_at = build_timestamps(rng, statuses[index])

        pending.append({
            "client_id": client_id,
            "first_name": first_name,
            "last_name": last_name,
            "email": email,
            "phone": phone,
            "birth_date": birth_date.isoformat(),
            "country": country,
            "city": city,
            "nationality": nationality,
            "language": preferred_language,
            "segment": segment,
            "risk_profile": risk_profiles[index],
            "created_at": created_at,
            "updated_at": updated_at,
            "status": statuses[index],
        })

    # --- advisor assignment, most demanding clients first -----------------
    # Private Banking clients are placed BEFORE the rest. The advisors who
    # take them have then already consumed a large share of their capacity
    # when the bulk of Standard clients is distributed, so a private-banking
    # heavy portfolio naturally ends up being a SMALLER portfolio.
    order = list(range(TOTAL_CLIENTS))
    rng.shuffle(order)
    order.sort(key=lambda i: -SEGMENT_COST[pending[i]["segment"]])

    for index in order:
        client = pending[index]
        advisor = pick_advisor(
            rng, advisors, advisors_by_country,
            client["country"], client["city"], client["language"], client["segment"],
        )
        advisor["load"] += SEGMENT_COST[client["segment"]]
        advisor["client_count"] += 1
        client["advisor_id"] = advisor["advisor_id"]

    return [
        (
            c["client_id"], c["first_name"], c["last_name"], c["email"], c["phone"],
            c["birth_date"], c["country"], c["city"], c["nationality"],
            c["language"], c["segment"], c["risk_profile"],
            c["advisor_id"], c["created_at"], c["updated_at"], c["status"],
        )
        for c in pending
    ]


def build_client_email(rng, first_name, last_name, country, used_emails):
    """A realistic personal address, unique across the whole dataset."""
    first = strip_accents(first_name).lower().replace(" ", "")
    last = strip_accents(last_name).lower().replace(" ", "")
    domain = rng.choice(EMAIL_DOMAINS[country])

    # Real people use several shapes of address, not one rigid pattern.
    shape = rng.choice([
        f"{first}.{last}",
        f"{first}.{last}",
        f"{first[0]}.{last}",
        f"{first}{last}",
        f"{first}.{last}{rng.randint(60, 99)}",
    ])

    candidate = f"{shape}@{domain}"
    suffix = 1
    while candidate in used_emails:
        suffix += 1
        candidate = f"{shape}{suffix}@{domain}"
    return candidate


def build_phone(rng, country, used_phones):
    """A mobile number with the right country calling code, unique."""
    template = PHONE_TEMPLATES[country]
    while True:
        if country == "France":
            phone = template.format(
                rng.choice([6, 7]), rng.randint(10, 99), rng.randint(10, 99),
                rng.randint(10, 99), rng.randint(10, 99),
            )
        elif country == "Belgium":
            phone = template.format(
                rng.randint(70, 99), rng.randint(10, 99),
                rng.randint(10, 99), rng.randint(10, 99),
            )
        elif country == "Switzerland":
            phone = template.format(
                rng.choice([6, 8, 9]), rng.randint(100, 999),
                rng.randint(10, 99), rng.randint(10, 99),
            )
        else:  # Italy
            phone = template.format(
                rng.randint(20, 49), rng.randint(100, 999), rng.randint(1000, 9999),
            )
        if phone not in used_phones:
            return phone


def build_timestamps(rng, status):
    """created_at in the past, updated_at at or after created_at.

    A large share of long-standing clients get a RECENT updated_at, so a
    future incremental ingestion has something to detect.
    """
    if status == "Prospect":
        # Prospects are recent leads.
        created = random_datetime_between(rng, date(2025, 9, 1), TODAY)
    else:
        created = random_datetime_between(rng, date(2016, 1, 1), TODAY - timedelta(days=30))

    if rng.random() < 0.38:
        # Recently touched record, whatever its age.
        earliest = max(created.date(), TODAY - timedelta(days=90))
        updated = random_datetime_between(rng, earliest, TODAY)
    else:
        updated = random_datetime_between(rng, created.date(), TODAY)

    if updated < created:
        updated = created

    return (
        created.strftime("%Y-%m-%d %H:%M:%S"),
        updated.strftime("%Y-%m-%d %H:%M:%S"),
    )


def pick_advisor(rng, advisors, advisors_by_country, country, city, language, segment):
    """Choose the advisor who will follow this client.

    Hard rule : the advisor MUST speak the client's preferred language.
    Soft rules: same country most of the time, same city is a plus, Paris and
                Geneva attract Private Banking, specialization matches the
                segment, and the least loaded advisors are favoured.
    """
    cross_border = rng.random() < CROSS_BORDER_RATE
    pool = advisors if cross_border else advisors_by_country[country]

    candidates = [a for a in pool if language in a["languages"]]
    if not candidates:
        # No local advisor speaks that language: widen to the whole network.
        candidates = [a for a in advisors if language in a["languages"]]

    weights = []
    for advisor in candidates:
        headroom = advisor["capacity"] - advisor["load"]
        if headroom > 0:
            weight = headroom
        else:
            # Over capacity: still reachable (a language may leave no other
            # option) but heavily penalised, so load really does saturate.
            weight = 0.05 / (1.0 + advisor["load"])

        # What kind of clients this advisor's practice attracts.
        weight *= PORTFOLIO_STYLES[advisor["style"]]["segments"][segment]
        weight *= SEGMENT_AFFINITY[segment].get(advisor["specialization"], 1.0)

        if segment == "Private Banking" and advisor["branch_id"] in PRIVATE_BANKING_HUBS:
            weight *= HUB_PRIVATE_BANKING_BONUS

        if advisor["city"] == city:
            weight *= 1.6

        weights.append(max(weight, 1e-6))

    return rng.choices(candidates, weights=weights, k=1)[0]


# ---------------------------------------------------------------------------
# 8. SQL
# ---------------------------------------------------------------------------

UPSERT_CLIENT = """
INSERT INTO clients (
    client_id, first_name, last_name, email, phone, birth_date,
    country_of_residence, city_of_residence, nationality, preferred_language,
    client_segment, risk_profile, advisor_id, created_at, updated_at,
    client_status
)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
ON CONFLICT (client_id) DO UPDATE SET
    first_name           = excluded.first_name,
    last_name            = excluded.last_name,
    email                = excluded.email,
    phone                = excluded.phone,
    birth_date           = excluded.birth_date,
    country_of_residence = excluded.country_of_residence,
    city_of_residence    = excluded.city_of_residence,
    nationality          = excluded.nationality,
    preferred_language   = excluded.preferred_language,
    client_segment       = excluded.client_segment,
    risk_profile         = excluded.risk_profile,
    advisor_id           = excluded.advisor_id,
    created_at           = excluded.created_at,
    updated_at           = excluded.updated_at,
    client_status        = excluded.client_status;
"""


def seed_clients():
    """Generate and store the 5,000 Orialis clients, then verify the result."""

    if not DB_PATH.exists():
        raise SystemExit(
            f"Database not found: {DB_PATH}\n"
            f"Run 'python CRM/scripts/init_db.py' first."
        )

    rng = random.Random(RNG_SEED)

    connection = sqlite3.connect(DB_PATH)
    try:
        cursor = connection.cursor()
        cursor.execute("PRAGMA foreign_keys = ON;")

        advisors = load_advisors(cursor)
        if not advisors:
            raise SystemExit(
                "The `advisors` table is empty.\n"
                "Run 'python CRM/scripts/seed_advisors.py' first."
            )

        clients = generate_clients(advisors, rng)

        # Remove clients left over from an earlier, different dataset, so the
        # table ends up with exactly 5,000 rows and never 10,000.
        # A temp table avoids a 5,000-placeholder IN (...) clause.
        cursor.execute("CREATE TEMP TABLE wanted_ids (client_id TEXT PRIMARY KEY)")
        cursor.executemany(
            "INSERT INTO wanted_ids (client_id) VALUES (?)",
            [(client[0],) for client in clients],
        )
        cursor.execute(
            "DELETE FROM clients WHERE client_id NOT IN (SELECT client_id FROM wanted_ids)"
        )
        removed = cursor.rowcount

        existing_ids = {row[0] for row in cursor.execute("SELECT client_id FROM clients")}
        cursor.executemany(UPSERT_CLIENT, clients)

        inserted = sum(1 for client in clients if client[0] not in existing_ids)
        updated = len(clients) - inserted

        connection.commit()

        print(
            f"Clients seeded: {inserted} inserted, {updated} refreshed, "
            f"{removed} removed."
        )
        print()
        report(cursor)
    finally:
        connection.close()


# ---------------------------------------------------------------------------
# 9. Verification report
# ---------------------------------------------------------------------------

def print_breakdown(title, rows, total):
    """Print a labelled count table with percentages."""
    print(f"  {title}")
    for label, count in rows:
        share = count / total * 100 if total else 0
        print(f"      {str(label):24} {count:>6}   {share:5.1f}%")
    print()


def report(cursor):
    """Read the stored data back and print the 9 required checks."""

    total = list(cursor.execute("SELECT COUNT(*) FROM clients"))[0][0]

    # 1. Total
    print(f"  1. TOTAL CLIENTS: {total}")
    print()

    # 2. Per country
    print_breakdown("2. Clients per country", list(cursor.execute("""
        SELECT country_of_residence, COUNT(*) FROM clients
        GROUP BY country_of_residence ORDER BY COUNT(*) DESC
    """)), total)

    # 3. Per segment
    print_breakdown("3. Clients per segment", list(cursor.execute("""
        SELECT client_segment, COUNT(*) FROM clients
        GROUP BY client_segment ORDER BY COUNT(*) DESC
    """)), total)

    # 4. Per risk profile
    print_breakdown("4. Clients per risk profile", list(cursor.execute("""
        SELECT risk_profile, COUNT(*) FROM clients
        GROUP BY risk_profile ORDER BY COUNT(*) DESC
    """)), total)

    # 5. Per status
    print_breakdown("5. Clients per status", list(cursor.execute("""
        SELECT client_status, COUNT(*) FROM clients
        GROUP BY client_status ORDER BY COUNT(*) DESC
    """)), total)

    # 6. Portfolio size per advisor
    stats = list(cursor.execute("""
        SELECT MIN(client_count), MAX(client_count), AVG(client_count)
        FROM (
            SELECT a.advisor_id, COUNT(c.client_id) AS client_count
            FROM advisors a
            LEFT JOIN clients c ON c.advisor_id = a.advisor_id
            GROUP BY a.advisor_id
        )
    """))[0]
    advisors_with_clients = list(cursor.execute("""
        SELECT COUNT(DISTINCT advisor_id) FROM clients
    """))[0][0]
    print("  6. Clients per advisor")
    print(f"      minimum                  {stats[0]:>6}")
    print(f"      maximum                  {stats[1]:>6}")
    print(f"      average                  {stats[2]:>9.1f}")
    print(f"      advisors with clients    {advisors_with_clients:>6} / 100")
    print()

    # 7. Per branch
    print_breakdown("7. Clients per branch", list(cursor.execute("""
        SELECT b.branch_id || '  ' || b.branch_name, COUNT(c.client_id)
        FROM branches b
        LEFT JOIN advisors a ON a.branch_id = b.branch_id
        LEFT JOIN clients c ON c.advisor_id = a.advisor_id
        GROUP BY b.branch_id, b.branch_name
        ORDER BY b.branch_id
    """)), total)

    # 8. Private Banking clients per branch
    pb_total = list(cursor.execute("""
        SELECT COUNT(*) FROM clients WHERE client_segment = 'Private Banking'
    """))[0][0]
    print_breakdown("8. Private Banking clients per branch", list(cursor.execute("""
        SELECT b.branch_id || '  ' || b.branch_name, COUNT(c.client_id)
        FROM branches b
        LEFT JOIN advisors a ON a.branch_id = b.branch_id
        LEFT JOIN clients c ON c.advisor_id = a.advisor_id
                           AND c.client_segment = 'Private Banking'
        GROUP BY b.branch_id, b.branch_name
        ORDER BY b.branch_id
    """)), pb_total)

    # 9. Language mismatches - must be zero
    mismatches = list(cursor.execute("""
        SELECT COUNT(*)
        FROM clients c
        JOIN advisors a ON a.advisor_id = c.advisor_id
        WHERE ',' || a.spoken_languages || ',' NOT LIKE
              '%,' || c.preferred_language || ',%'
    """))[0][0]
    verdict = "OK" if mismatches == 0 else "FAILED"
    print(f"  9. Clients whose language is not spoken by their advisor: "
          f"{mismatches}   [{verdict}]")
    print()

    if total != TOTAL_CLIENTS:
        raise SystemExit(f"Expected {TOTAL_CLIENTS} clients, found {total}.")
    if mismatches:
        raise SystemExit(f"{mismatches} clients cannot be served in their language.")


if __name__ == "__main__":
    seed_clients()

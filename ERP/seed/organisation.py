"""
The two organisational registers: suppliers (~180) and employees (~185).

Both are built before anything else, because every transaction in the ERP
hangs off one of them, and because their DATES are what make the rest of the
history chronologically honest: an invoice cannot predate its supplier's
onboarding, an expense cannot predate its author's hire date.
"""

import unicodedata
from datetime import date, datetime, timedelta, timezone

from .people import (ERP_ONLY_ADVISORS, FIRST_NAMES, LAST_NAMES,
                     SHARED_ADVISORS)
from .reference import OFFICE_TO_COST_CENTRE, FUNCTION_COST_CENTRES
from .toolkit import (PERIOD_END, PERIOD_START, labels_for, new_rng,
                      split_exactly)

EMAIL_DOMAIN = "orialis.com"


def _instant(day, hour=8, minute=0):
    return datetime(day.year, day.month, day.day, hour, minute,
                    tzinfo=timezone.utc)


# ===========================================================================
# Suppliers
# ===========================================================================
# THE PARETO TIERS ARE THE BUSINESS RULE, AND THE INVOICE COUNT IS THEIR
# CONSEQUENCE.
#
# A uniform 44 invoices per supplier would be indefensible: in any firm a
# handful of suppliers - the landlord, the market-data vendor, the travel
# agency - issue most of the paper, and a long tail issues one invoice ever.
# Each tier carries a MONTHLY RATE; the ~8 000 invoices are what those rates
# produce across 36 months, not a target the generator aims at.
#
#   tier  suppliers  invoices/month each   share of the ledger
#   A         10            9.0                  ~40%
#   B         22            3.0                  ~30%
#   C         45            1.0                  ~20%
#   D         50            0.333 (quarterly)     ~7%
#   E         53            0.085 (1-4 ever)      ~2%
#             ---
#             180

SUPPLIER_TIERS = [
    ("A", 10, 9.70),
    ("B", 22, 3.23),
    ("C", 45, 1.08),
    ("D", 50, 0.358),
    ("E", 53, 0.092),
]

# The rates above are slightly higher than the "9 / 3 / 1 / quarterly" of the
# specification, and deliberately so. Roughly one supplier in eight is
# onboarded during the window or deactivated inside it, so it bills for less
# than the full 36 months. Setting the rates at the nominal figures produced
# 7 450 invoices rather than 8 000: the attrition is real, and the rate is
# what compensates for it. The alternative - generating to a counter - would
# have hidden the effect instead of paying for it.

# Categories that fit each tier. A landlord bills monthly for ever; a training
# provider bills when a course happens.
TIER_CATEGORIES = {
    "A": ["Market Data", "Real Estate & Facilities", "IT & Software",
          "Travel", "Professional Services"],
    "B": ["IT & Software", "Professional Services", "Travel", "Telecom",
          "Marketing", "Market Data"],
    "C": ["IT & Software", "Professional Services", "Marketing", "Telecom",
          "Office Supplies", "Insurance", "Travel"],
    "D": ["Insurance", "Training", "Professional Services", "Marketing",
          "Office Supplies", "Real Estate & Facilities"],
    "E": ["Office Supplies", "Training", "Marketing", "Professional Services",
          "Travel"],
}

# Weighted so that about a fifth of the invoice VOLUME ends up in CHF. Swiss
# suppliers invoice in CHF; everyone else in EUR.
SUPPLIER_COUNTRIES = {
    "FR": 33, "CH": 26, "IT": 15, "BE": 13, "DE": 4, "LU": 4, "GB": 3, "US": 2,
}

SUPPLIER_STATUS_MIX = {"Active": 88, "Inactive": 9, "Blocked": 3}

# Payment terms are assigned PER TIER, not once across all 180 suppliers.
# The distribution the specification states is invoice-weighted, and a
# supplier-weighted draw does not reproduce it: a tier-E supplier billing
# three times in three years contributes almost nothing to the ledger, so
# putting the unusual terms (15 days, 90 days) on the long tail is what makes
# the invoice-level mix come out right. It is also how it works in practice -
# a landlord or a data vendor is on standard 30-day terms; a one-off consultant
# is paid on the spot or after a long negotiation.
PAYMENT_TERMS_BY_TIER = {
    "A": {30: 58, 45: 20, 60: 14, 15: 5, 90: 3},
    "B": {30: 56, 45: 20, 60: 15, 15: 6, 90: 3},
    "C": {30: 54, 45: 20, 60: 15, 15: 7, 90: 3, 0: 1},
    "D": {30: 50, 45: 20, 60: 16, 15: 8, 90: 4, 0: 2},
    "E": {30: 44, 15: 18, 45: 16, 60: 12, 90: 6, 0: 4},
}

_NAME_STEMS = [
    "Alpine", "Meridian", "Cortex", "Northstar", "Helvetia", "Lumen",
    "Quadrant", "Vireo", "Basalt", "Corvus", "Delta", "Equinox", "Falcon",
    "Granite", "Horizon", "Ionic", "Juniper", "Kestrel", "Lattice", "Monarch",
    "Nimbus", "Obsidian", "Pinnacle", "Quartz", "Riverstone", "Sable",
    "Tessera", "Umbra", "Verdant", "Westford", "Axiom", "Beacon", "Citadel",
    "Draco", "Ember", "Forge", "Gallica", "Hexa", "Indigo", "Javelin",
]

_NAME_SUFFIX = {
    "Market Data": ["Analytics", "Data Services", "Research"],
    "IT & Software": ["Systems", "Software", "Digital", "Technologies"],
    "Real Estate & Facilities": ["Properties", "Facilities", "Immobilier"],
    "Professional Services": ["Advisory", "Consulting", "Partners", "Audit"],
    "Travel": ["Travel", "Voyages", "Mobility"],
    "Marketing": ["Communication", "Media", "Studio"],
    "Telecom": ["Telecom", "Networks", "Connect"],
    "Training": ["Academy", "Formation", "Institute"],
    "Insurance": ["Assurances", "Insurance", "Risk"],
    "Office Supplies": ["Fournitures", "Office", "Supplies"],
}

_LEGAL_FORM = {"FR": "SAS", "BE": "SPRL", "CH": "AG", "IT": "SRL",
               "LU": "SARL", "DE": "GmbH", "GB": "Ltd", "US": "Inc"}


def build_suppliers():
    """~180 suppliers, tiered, with their onboarding and deactivation dates."""
    rng = new_rng("suppliers")

    countries = labels_for(sum(count for _, count, _ in SUPPLIER_TIERS),
                           SUPPLIER_COUNTRIES, rng)
    statuses = labels_for(len(countries), SUPPLIER_STATUS_MIX, rng)
    terms_by_tier = {tier: labels_for(count, PAYMENT_TERMS_BY_TIER[tier], rng)
                     for tier, count, _ in SUPPLIER_TIERS}

    rows, index = [], 0
    used_names = {}

    for tier, count, monthly_rate in SUPPLIER_TIERS:
        tier_terms = terms_by_tier[tier]
        for position in range(count):
            country = countries[index]
            status = statuses[index]
            category = rng.choice(TIER_CATEGORIES[tier])

            stem = rng.choice(_NAME_STEMS)
            suffix = rng.choice(_NAME_SUFFIX[category])
            name = f"{stem} {suffix}"
            while name in used_names:
                stem = rng.choice(_NAME_STEMS)
                name = f"{stem} {suffix}"
            used_names[name] = True

            # 85% were already suppliers before the window opened; the rest
            # are onboarded during it, which is why their first invoice
            # appears mid-history rather than in October 2023.
            if rng.random() < 0.85:
                onboarded = PERIOD_START - timedelta(days=rng.randint(200, 2600))
            else:
                onboarded = PERIOD_START + timedelta(
                    days=rng.randint(30, 800))

            deactivated = None
            if status != "Active":
                earliest = max(onboarded + timedelta(days=90),
                               PERIOD_START + timedelta(days=120))
                latest = PERIOD_END - timedelta(days=20)
                if earliest >= latest:
                    # Too recent to have been deactivated inside the window.
                    status = "Active"
                else:
                    deactivated = earliest + timedelta(
                        days=rng.randint(0, (latest - earliest).days))

            currency = "CHF" if country == "CH" else "EUR"
            index += 1

            rows.append({
                "supplier_id": f"SUP-{index:05d}",
                "supplier_name": name,
                "supplier_legal_name": f"{name} {_LEGAL_FORM[country]}",
                "supplier_category": category,
                "country_code": country,
                # No EU VAT number outside the EU; a handful of small EU
                # suppliers have none recorded either.
                "vat_number": (None if country in ("GB", "US") or rng.random() < 0.06
                               else f"{country}{rng.randrange(10**10, 10**11)}"),
                "iban_masked": _masked_iban(rng, country),
                "default_currency": currency,
                "payment_terms_days": tier_terms[position],
                "supplier_status": status,
                "onboarded_on": onboarded,
                "deactivated_on": deactivated,
                "created_at": _instant(onboarded),
                "updated_at": _instant(deactivated or onboarded),
                # Not a column: carried alongside for the invoice generator.
                "_tier": tier,
                "_monthly_rate": monthly_rate,
            })

    return rows


def _masked_iban(rng, country):
    """A synthetic IBAN that is masked BEFORE it exists.

    The full number is never built, not even in memory: only the country
    prefix, the check digits and the last four digits are ever generated. The
    schema enforces the shape; this makes sure there is nothing to enforce it
    against.
    """
    prefix = country if country in ("FR", "BE", "CH", "IT", "LU", "DE", "GB") else "US"
    return f"{prefix}{rng.randint(10, 99)}{'*' * 10}{rng.randint(1000, 9999)}"


# ===========================================================================
# Employees
# ===========================================================================
# Four populations, and the split is what produces the reconciliation
# friction:
#
#    93  shared advisors      also in the CRM, hire dates taken from it
#     5  ERP-only advisors    hired late, no CRM existence yet
#    14  departed advisors    left during the window; kept on payroll for ever
#    73  support staff        back office, IT, compliance, management
#   ---
#   185  of which ~160 still Active on 2026-09-30
#
# Support staff are the decisive population. Without them the ERP would
# describe a firm with no back office, and 12 000 expense claims would all
# come from advisors - which is neither realistic nor what the specification
# describes.

SUPPORT_MIX = {
    "Back Office": 30, "IT": 12, "Support": 13, "Management": 10, "Compliance": 8,
}

DEPARTED_ADVISORS = 14
SUPPORT_HEADCOUNT = 73
DEPARTED_SUPPORT = 11

# A minority of advisors are on a fixed-salary arrangement - branch managers
# and a few senior hires - and earn no commission. This is why the commission
# count is not simply "advisors x months": eligibility is a contractual fact,
# and it is the honest way the ~5 900 figure arises.
FIXED_SALARY_SHARE = 0.08

COUNTRY_OF_OFFICE = {
    "FR-PAR-01": "FR", "FR-LYO-01": "FR", "BE-BRU-01": "BE",
    "CH-GVA-01": "CH", "CH-LAU-01": "CH", "IT-MIL-01": "IT", "IT-ROM-01": "IT",
}

OFFICES = list(COUNTRY_OF_OFFICE)


TOTAL_EMPLOYEES = len(SHARED_ADVISORS) + len(ERP_ONLY_ADVISORS) \
    + DEPARTED_ADVISORS + SUPPORT_HEADCOUNT

# Exactly one employee in twenty has no work email on file. Drawn as an exact
# share rather than a 5% coin flip per person: on 185 rows a coin flip lands
# anywhere between 3% and 9%, and this figure is a stated property of the
# dataset - it is what forbids solving the CRM/ERP reconciliation with an
# email join.
MISSING_EMAIL_SHARE = 0.05


def build_employees():
    """~185 employees, with hire and departure dates inside a coherent history."""
    rng = new_rng("employees")
    rows = []
    emails = {}
    counter = 0

    missing_email = set(rng.sample(range(TOTAL_EMPLOYEES),
                                   round(TOTAL_EMPLOYEES * MISSING_EMAIL_SHARE)))

    def add(first, last, office, hire, employee_type, departure=None,
            eligible=False, function_cost_centre=None):
        nonlocal counter
        counter += 1
        country = COUNTRY_OF_OFFICE[office]
        cost_centre = function_cost_centre or OFFICE_TO_COST_CENTRE[office]
        status = "Inactive" if departure else "Active"
        email = (None if (counter - 1) in missing_email
                 else _unique_email(first, last, emails))
        rows.append({
            "employee_ref": f"EMP-{counter:04d}",
            "first_name": first,
            "last_name": last,
            "work_email": email,
            "employee_type": employee_type,
            "cost_center_id": cost_centre,
            "office_code": office,
            "country_code": country,
            "hire_date": hire,
            "departure_date": departure,
            "is_commission_eligible": eligible,
            "employee_status": status,
            "created_at": _instant(hire),
            "updated_at": _instant(departure or hire),
        })

    # -- 1. the 93 shared advisors ------------------------------------------
    # Their hire dates come from the CRM snapshot, unchanged. That is the one
    # attribute a future reconciliation can lean on when the spelling of a
    # name disagrees, so inventing new dates here would quietly remove the
    # only reliable anchor.
    shared_count = len(SHARED_ADVISORS)
    fixed_salary = set(rng.sample(range(shared_count),
                                  int(shared_count * FIXED_SALARY_SHARE)))
    for position, (first, last, office, hire) in enumerate(SHARED_ADVISORS):
        add(first, last, office, date.fromisoformat(hire), "Advisor",
            eligible=position not in fixed_salary)

    # -- 2. the 5 ERP-only advisors -----------------------------------------
    # Hired in the last months of the window: already on payroll, not yet
    # carrying a portfolio, so the CRM does not know them.
    for first, last, office in ERP_ONLY_ADVISORS:
        hire = PERIOD_END - timedelta(days=rng.randint(40, 420))
        add(first, last, office, hire, "Advisor", eligible=rng.random() < 0.6)

    # -- 3. the 14 advisors who left ----------------------------------------
    # Absent from the CRM, which lists only active advisors, and kept here for
    # ever: payroll history is not deleted. The ERP never deletes.
    for _ in range(DEPARTED_ADVISORS):
        office = rng.choice(OFFICES)
        first, last = _invent_name(rng, COUNTRY_OF_OFFICE[office], emails)
        hire = PERIOD_START - timedelta(days=rng.randint(300, 3500))
        departure = PERIOD_START + timedelta(
            days=rng.randint(60, (PERIOD_END - PERIOD_START).days - 30))
        add(first, last, office, hire, "Advisor", departure=departure,
            eligible=rng.random() < 0.9)

    # -- 4. the 73 support staff --------------------------------------------
    types = labels_for(SUPPORT_HEADCOUNT, SUPPORT_MIX, rng)
    leavers = set(rng.sample(range(SUPPORT_HEADCOUNT), DEPARTED_SUPPORT))
    for position in range(SUPPORT_HEADCOUNT):
        office = rng.choice(OFFICES)
        first, last = _invent_name(rng, COUNTRY_OF_OFFICE[office], emails)
        employee_type = types[position]

        # Two thirds of support staff are charged to a functional cost centre
        # (IT, Compliance, Finance); the rest to the branch they sit in.
        cost_centre = None
        if rng.random() < 0.65:
            cost_centre = rng.choice(FUNCTION_COST_CENTRES)

        if rng.random() < 0.80:
            hire = PERIOD_START - timedelta(days=rng.randint(200, 3600))
        else:
            hire = PERIOD_START + timedelta(days=rng.randint(20, 900))

        departure = None
        if position in leavers:
            earliest = max(hire + timedelta(days=200),
                           PERIOD_START + timedelta(days=45))
            latest = PERIOD_END - timedelta(days=15)
            if earliest < latest:
                departure = earliest + timedelta(
                    days=rng.randint(0, (latest - earliest).days))

        add(first, last, office, hire, employee_type, departure=departure,
            function_cost_centre=cost_centre)

    return rows


def _invent_name(rng, country, taken):
    """A name from the ERP-only pools, with no CRM counterpart."""
    for _ in range(200):
        first = rng.choice(FIRST_NAMES[country])
        last = rng.choice(LAST_NAMES[country])
        if (first, last) not in taken:
            taken[(first, last)] = True
            return first, last
    return first, f"{last}-{rng.randint(10, 99)}"


def _unique_email(first, last, taken):
    """firstname.lastname@orialis.com, accents stripped, collisions numbered.

    The ERP keeps the accented civil-status spelling in `first_name` and
    `last_name` but not in the address - which is itself a small piece of
    reconciliation friction, since the email normalises away exactly the
    difference that makes the names hard to match.
    """
    base = f"{_ascii(first)}.{_ascii(last)}"
    candidate = f"{base}@{EMAIL_DOMAIN}"
    suffix = 1
    while candidate in taken:
        suffix += 1
        candidate = f"{base}{suffix}@{EMAIL_DOMAIN}"
    taken[candidate] = True
    return candidate


def _ascii(text):
    stripped = unicodedata.normalize("NFKD", text)
    stripped = "".join(c for c in stripped if not unicodedata.combining(c))
    return stripped.lower().replace(" ", "-").replace("'", "")

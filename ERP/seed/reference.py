"""
The two fixed reference tables: cost centres (25) and FX rates (36).

Neither is generated from a distribution. A cost-centre chart is a decision an
organisation makes, not a random draw, so the 25 rows are written out in full.
The 36 FX rates are a deterministic walk, one per month of the window.
"""

from datetime import date

from .toolkit import TODAY, months_in_period, new_rng

# ---------------------------------------------------------------------------
# Cost centres - exactly 25, on three levels
# ---------------------------------------------------------------------------
#   1  Group     Orialis Group                      1
#   2  Country   FR, BE, CH, IT                     4
#   3  Branch    the seven offices                  7
#      Function  IT, Compliance, Marketing, ...    13
#                                                  --
#                                                  25
#
# The seven branches correspond to the seven CRM branches - they are the same
# buildings. NOTHING SAYS SO. Only the ERP office code exists here
# (FR-PAR-01), and no rule derives it from BR001. That is the point.
#
# Functions hang off a country rather than off the group, because the schema
# requires a country on everything except the single root, and because a
# compliance team is in fact staffed in one country. Most sit in France, where
# the head office is.

_GROUP = [
    # (id, name, type, office_code, country, currency, parent, level)
    ("CC-GRP-000", "Orialis Group", "Group", None, None, "EUR", None, 1),
]

_COUNTRIES = [
    ("CC-FR-000", "Orialis France", "Country", None, "FR", "EUR", "CC-GRP-000", 2),
    ("CC-BE-000", "Orialis Belgium", "Country", None, "BE", "EUR", "CC-GRP-000", 2),
    ("CC-CH-000", "Orialis Switzerland", "Country", None, "CH", "CHF", "CC-GRP-000", 2),
    ("CC-IT-000", "Orialis Italy", "Country", None, "IT", "EUR", "CC-GRP-000", 2),
]

_BRANCHES = [
    ("CC-FR-001", "Orialis Paris", "Branch", "FR-PAR-01", "FR", "EUR", "CC-FR-000", 3),
    ("CC-FR-002", "Orialis Lyon", "Branch", "FR-LYO-01", "FR", "EUR", "CC-FR-000", 3),
    ("CC-BE-001", "Orialis Brussels", "Branch", "BE-BRU-01", "BE", "EUR", "CC-BE-000", 3),
    ("CC-CH-001", "Orialis Geneva", "Branch", "CH-GVA-01", "CH", "CHF", "CC-CH-000", 3),
    ("CC-CH-002", "Orialis Lausanne", "Branch", "CH-LAU-01", "CH", "CHF", "CC-CH-000", 3),
    ("CC-IT-001", "Orialis Milan", "Branch", "IT-MIL-01", "IT", "EUR", "CC-IT-000", 3),
    ("CC-IT-002", "Orialis Rome", "Branch", "IT-ROM-01", "IT", "EUR", "CC-IT-000", 3),
]

_FUNCTIONS = [
    ("CC-FR-101", "Information Technology", "Function", None, "FR", "EUR", "CC-FR-000", 3),
    ("CC-FR-102", "Compliance", "Function", None, "FR", "EUR", "CC-FR-000", 3),
    ("CC-FR-103", "Marketing", "Function", None, "FR", "EUR", "CC-FR-000", 3),
    ("CC-FR-104", "Human Resources", "Function", None, "FR", "EUR", "CC-FR-000", 3),
    ("CC-FR-105", "Finance", "Function", None, "FR", "EUR", "CC-FR-000", 3),
    ("CC-FR-106", "Legal", "Function", None, "FR", "EUR", "CC-FR-000", 3),
    ("CC-FR-107", "Executive Office", "Function", None, "FR", "EUR", "CC-FR-000", 3),
    ("CC-BE-101", "Operations", "Function", None, "BE", "EUR", "CC-BE-000", 3),
    ("CC-CH-101", "Risk Management", "Function", None, "CH", "CHF", "CC-CH-000", 3),
    ("CC-CH-102", "Client Services", "Function", None, "CH", "CHF", "CC-CH-000", 3),
    ("CC-IT-101", "Facilities", "Function", None, "IT", "EUR", "CC-IT-000", 3),
    ("CC-IT-102", "Training", "Function", None, "IT", "EUR", "CC-IT-000", 3),
    ("CC-IT-103", "Data & Reporting", "Function", None, "IT", "EUR", "CC-IT-000", 3),
]

COST_CENTRE_PLAN = _GROUP + _COUNTRIES + _BRANCHES + _FUNCTIONS

# Office code -> branch cost centre, used everywhere a person or a charge has
# to be attached to a place.
OFFICE_TO_COST_CENTRE = {
    row[3]: row[0] for row in _BRANCHES
}

FUNCTION_COST_CENTRES = [row[0] for row in _FUNCTIONS]

COUNTRY_OF_COST_CENTRE = {row[0]: row[4] for row in COST_CENTRE_PLAN}
CURRENCY_OF_COST_CENTRE = {row[0]: row[5] for row in COST_CENTRE_PLAN}


def build_cost_centres():
    """The 25 cost-centre rows, opened long before the window and all active."""
    opened = date(2015, 1, 1)
    created = _instant(opened)
    rows = []
    for (identifier, name, kind, office, country, currency,
         parent, level) in COST_CENTRE_PLAN:
        rows.append({
            "cost_center_id": identifier,
            "cost_center_name": name,
            "cost_center_type": kind,
            "office_code": office,
            "country_code": country,
            "default_currency": currency,
            "parent_cost_center_id": parent,
            "hierarchy_level": level,
            "cost_center_status": "Active",
            "opened_on": opened,
            "closed_on": None,
            "created_at": created,
            "updated_at": created,
        })
    return rows


# ---------------------------------------------------------------------------
# FX rates - exactly 36, one per month
# ---------------------------------------------------------------------------
# CHF -> EUR only. Orialis pays in EUR or CHF and in nothing else, so an
# invoice in EUR carries fx_rate_to_eur = 1.000000 and never consults this
# table.
#
# A random walk rather than independent draws: an exchange rate has memory.
# Independent monthly values would jump 1.04 -> 1.07 -> 1.03 in a way no
# currency does, and any month-on-month analysis built on it would be
# nonsense.
#
# The walk runs from about 1.035 to about 1.075 - the CHF strengthening
# against the euro across 2024-2026, which is the real direction of travel.

def build_fx_rates():
    rng = new_rng("fx")
    months = months_in_period()
    rows = []
    rate = 1.0365
    drift = (1.0745 - rate) / (len(months) - 1)
    for month in months:
        rate = rate + drift + rng.gauss(0, 0.0045)
        rows.append({
            "rate_month": month,
            "from_currency": "CHF",
            "to_currency": "EUR",
            "rate": round(rate, 6),
            "created_at": _instant(month),
        })
    return rows


def fx_lookup(fx_rows):
    """(year, month) -> rate, for freezing a rate onto a document."""
    return {(row["rate_month"].year, row["rate_month"].month): row["rate"]
            for row in fx_rows}


def _instant(day, hour=6, minute=0):
    from datetime import datetime, timezone
    return datetime(day.year, day.month, day.day, hour, minute,
                    tzinfo=timezone.utc)

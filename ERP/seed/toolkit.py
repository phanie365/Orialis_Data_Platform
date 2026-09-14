"""
Shared machinery for the ERP seed: determinism, calendar, distributions.

---------------------------------------------------------------------------
WHY THE VOLUMES ARE NOT COUNTERS
---------------------------------------------------------------------------

The specification gives target volumes (~8 000 invoices, ~12 000 expenses).
The tempting implementation is a `while len(rows) < 8000` loop. It would hit
the number exactly and produce a dataset with no internal logic: invoice
counts unrelated to which supplier issued them, expenses unrelated to who was
employed that month.

Everything here is built the other way round. The BUSINESS RULE is the input -
"a tier-A supplier invoices about nine times a month", "an advisor files about
2.9 expense claims a month" - and the row count is whatever those rules
produce once they are applied across 36 months, seasonality, hiring and
supplier deactivation. The totals land where the specification predicted
because the rates were derived from it, not because a loop stopped at a
threshold.

The one place an exact count IS imposed is a STATED DISTRIBUTION: when the
specification says "Approved 90%", that percentage is the rule itself, and
`split_exactly()` applies it by largest remainder rather than by repeated
sampling. Drawing 8 000 independent choices would land near 90% and vary run
to run for no reason - the target is not an average, it is the figure the
business reports.

---------------------------------------------------------------------------
DETERMINISM
---------------------------------------------------------------------------

One seed, fixed here, drives everything. Two runs from an empty database must
produce byte-identical data, which requires more discipline than calling
`random.seed()`:

    - every draw goes through a `random.Random` INSTANCE, never the module
      level functions, so nothing else in the process can perturb the stream
    - iteration is only ever over lists and dicts (insertion-ordered), never
      over sets, whose order varies between interpreter runs
    - `TODAY` is a constant, never `date.today()`, so the dataset does not
      drift as real time passes
"""

import math
import random
from datetime import date, timedelta

# ---------------------------------------------------------------------------
# The fixed points of the whole dataset
# ---------------------------------------------------------------------------

RNG_SEED = 20260914

# The 36-month window, inclusive. Everything is generated inside it.
PERIOD_START = date(2023, 10, 1)
PERIOD_END = date(2026, 9, 30)

# The reference "now". Hardcoded, never read from the clock: an invoice is
# "not yet due" relative to this date, and the dataset must say the same thing
# next year as it does today.
TODAY = date(2026, 9, 30)


def new_rng(stream):
    """A private random stream, derived from the master seed.

    Each generator gets its own stream, so adding a draw inside `expenses`
    cannot shift every invoice amount. Without this, any change anywhere
    rewrites the whole dataset and no diff is ever readable.
    """
    return random.Random(f"{RNG_SEED}:{stream}")


# ---------------------------------------------------------------------------
# Months
# ---------------------------------------------------------------------------

def months_in_period():
    """The 36 first-of-month dates, oldest first."""
    months = []
    year, month = PERIOD_START.year, PERIOD_START.month
    while date(year, month, 1) <= PERIOD_END:
        months.append(date(year, month, 1))
        month += 1
        if month == 13:
            year, month = year + 1, 1
    return months


def month_end(first_of_month):
    if first_of_month.month == 12:
        return date(first_of_month.year, 12, 31)
    return date(first_of_month.year, first_of_month.month + 1, 1) - timedelta(days=1)


# ---------------------------------------------------------------------------
# Public holidays
# ---------------------------------------------------------------------------
# Business activity stops at weekends and on the public holidays OF THE
# COUNTRY the cost centre belongs to - which is why this is per country rather
# than a single European calendar. An Italian office works on 14 July and a
# French one does not.

def easter_sunday(year):
    """Anonymous Gregorian algorithm."""
    a = year % 19
    b, c = divmod(year, 100)
    d, e = divmod(b, 4)
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = divmod(c, 4)
    ell = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * ell) // 451
    month, day = divmod(h + ell - 7 * m + 114, 31)
    return date(year, month, day + 1)


_HOLIDAY_CACHE = {}


def holidays(country, year):
    """The public holidays of one country in one year, as a frozenset."""
    key = (country, year)
    if key in _HOLIDAY_CACHE:
        return _HOLIDAY_CACHE[key]

    easter = easter_sunday(year)
    easter_monday = easter + timedelta(days=1)
    good_friday = easter - timedelta(days=2)
    ascension = easter + timedelta(days=39)
    whit_monday = easter + timedelta(days=50)

    common = [date(year, 1, 1), date(year, 5, 1), easter_monday, date(year, 12, 25)]

    extra = {
        "FR": [date(year, 5, 8), date(year, 7, 14), date(year, 8, 15),
               date(year, 11, 1), date(year, 11, 11), ascension, whit_monday],
        "BE": [date(year, 7, 21), date(year, 8, 15), date(year, 11, 1),
               date(year, 11, 11), ascension, whit_monday],
        "CH": [date(year, 8, 1), date(year, 12, 26), good_friday,
               ascension, whit_monday],
        "IT": [date(year, 1, 6), date(year, 4, 25), date(year, 6, 2),
               date(year, 8, 15), date(year, 11, 1), date(year, 12, 8),
               date(year, 12, 26)],
    }

    result = frozenset(common + extra.get(country, []))
    _HOLIDAY_CACHE[key] = result
    return result


def is_business_day(day, country="FR"):
    return day.weekday() < 5 and day not in holidays(country, day.year)


def business_days(start, end, country="FR"):
    """Every working day in [start, end], as a list."""
    days, current = [], start
    while current <= end:
        if is_business_day(current, country):
            days.append(current)
        current += timedelta(days=1)
    return days


def next_business_day(day, country="FR"):
    while not is_business_day(day, country):
        day += timedelta(days=1)
    return day


def add_business_days(day, count, country="FR"):
    current = day
    while count > 0:
        current += timedelta(days=1)
        if is_business_day(current, country):
            count -= 1
    return current


# ---------------------------------------------------------------------------
# Seasonality
# ---------------------------------------------------------------------------
# Multipliers by calendar month, applied to the monthly rates. Both series
# average close to 1.0, so they redistribute activity across the year without
# inflating or deflating the totals.
#
#   invoices   August collapses (-25%), December peaks (+30%, year-end
#              closing), January carries the annual renewals (+15%:
#              licences, insurance, market-data subscriptions)
#
#   expenses   August collapses much harder (-45%): the firm is on holiday,
#              nobody travels. Spring and autumn are the client-meeting
#              seasons.

INVOICE_SEASONALITY = {
    1: 1.15, 2: 0.98, 3: 1.02, 4: 0.98, 5: 0.95, 6: 1.02,
    7: 0.95, 8: 0.75, 9: 1.05, 10: 1.02, 11: 1.03, 12: 1.30,
}

EXPENSE_SEASONALITY = {
    1: 0.95, 2: 1.00, 3: 1.08, 4: 1.02, 5: 1.05, 6: 1.10,
    7: 0.90, 8: 0.55, 9: 1.10, 10: 1.10, 11: 1.10, 12: 1.05,
}


# ---------------------------------------------------------------------------
# Distributions
# ---------------------------------------------------------------------------

def split_exactly(total, weights):
    """Split `total` into integer counts proportional to `weights`.

    Largest-remainder method, so the parts sum to exactly `total` and the
    stated percentages are reproduced to the row rather than approached by
    sampling. `weights` is a dict; the result keeps its key order.

    This is the only place a target number is imposed, and it is imposed
    because the percentage IS the business rule - "Approved 90%" is a figure
    the business reports, not the mean of a random variable.
    """
    scale = sum(weights.values())
    exact = {key: total * weight / scale for key, weight in weights.items()}
    counts = {key: int(value) for key, value in exact.items()}
    remainder = total - sum(counts.values())
    if remainder:
        by_fraction = sorted(exact, key=lambda key: exact[key] - counts[key],
                             reverse=True)
        for key in by_fraction[:remainder]:
            counts[key] += 1
    return counts


def labels_for(total, weights, rng):
    """`total` labels drawn to match `weights` exactly, then shuffled."""
    counts = split_exactly(total, weights)
    labels = []
    for key, count in counts.items():
        labels.extend([key] * count)
    rng.shuffle(labels)
    return labels


def poisson(rng, mean):
    """A Poisson draw - the natural law for "how many events this month".

    Knuth's method; the means here are small (under 15), so the loop is short.
    A Poisson rather than a uniform because arrivals are independent: some
    months a supplier bills twice as often as usual, and that irregularity is
    what a real invoice ledger looks like.
    """
    if mean <= 0:
        return 0
    limit = math.exp(-mean)
    count, product = 0, rng.random()
    while product > limit:
        count += 1
        product *= rng.random()
    return count


def lognormal(rng, median, p90, maximum):
    """A money amount: many small ones, a long tail of large ones.

    Parameterised by the two figures the specification actually states - the
    median and the 90th percentile - rather than by mu and sigma, which say
    nothing to a reader. Capped, because an unbounded tail eventually produces
    a stationery invoice for 600 000 EUR.
    """
    mu = math.log(median)
    sigma = (math.log(p90) - mu) / 1.2815515655446004  # z(0.90)
    value = math.exp(rng.gauss(mu, sigma))
    return min(value, maximum)


def money(value):
    """Round to the cent, the way an accounting system stores an amount."""
    return round(value + 1e-9, 2)

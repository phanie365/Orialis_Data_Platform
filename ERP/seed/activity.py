"""
Employee-driven activity: expense claims (~12 000) and commissions (~5 900).

Both are generated per MONTH and per PERSON, which is what keeps them
chronologically honest: an employee hired in March 2025 files no claim in
2024, and a departed advisor earns no commission for the month after they
left. The volumes are the consequence of the headcount curve, not a target.
"""

from datetime import date, datetime, timedelta, timezone

from .toolkit import (EXPENSE_SEASONALITY, PERIOD_END, PERIOD_START, TODAY,
                      add_business_days, business_days, labels_for, lognormal,
                      money, month_end, months_in_period, new_rng, poisson,
                      split_exactly)

# ---------------------------------------------------------------------------
# Expenses
# ---------------------------------------------------------------------------
# Monthly claim rates by role. An advisor travels to see clients; a back-office
# analyst rarely leaves the building. This difference is the whole reason the
# `employees` table had to exist: 12 000 claims all filed by advisors would
# describe a firm with no support function.

CLAIM_RATE = {
    "Advisor": 2.71, "Management": 3.37, "Back Office": 0.89,
    "IT": 0.98, "Compliance": 0.84, "Support": 0.75,
}

EXPENSE_CATEGORY_MIX = {
    "Travel": 28, "Meals": 19, "Accommodation": 16, "Client Entertainment": 12,
    "Transport": 10, "Training": 7, "Telecom": 5, "Office Supplies": 3,
}

EXPENSE_STATUS_MIX = {
    "Reimbursed": 86.0, "Approved": 5.0, "Submitted": 4.0,
    "Rejected": 3.0, "Draft": 1.5, "Cancelled": 0.5,
}

# The base draw is deliberately BELOW the ~95 EUR median the specification
# states, because travel, hotels and training are scaled up afterwards (a
# hotel night is not a taxi fare) and those three categories are half the
# claims. Setting the base at 95 pushed the blended median to 158.
EXPENSE_MEDIAN, EXPENSE_P90, EXPENSE_MAX = 58.0, 380.0, 4500.0

# Domestic VAT. Meals and hotels carry reduced rates in most of the four
# countries, which is why a claim's rate is not simply its country's headline
# rate.
VAT_STANDARD = {"FR": 20.0, "BE": 21.0, "IT": 22.0, "CH": 8.1}
VAT_REDUCED = {"FR": 10.0, "BE": 12.0, "IT": 10.0, "CH": 2.6}
REDUCED_CATEGORIES = ("Meals", "Accommodation", "Transport")

# How long a claim can plausibly still be open. Anything older has been
# settled one way or another - see the note in `_assign_expense_status`.
OPEN_CLAIM_WINDOW_DAYS = 120


def _instant(day, hour, minute=0):
    return datetime(day.year, day.month, day.day, hour, minute,
                    tzinfo=timezone.utc)


def build_expenses(employees, approvers_by_country, fx_by_month):
    """~12 000 claims, one Poisson draw per employee per month."""
    rng = new_rng("expenses")
    months = months_in_period()

    drafts = []
    for month in months:
        finish = month_end(month)
        season = EXPENSE_SEASONALITY[month.month]

        for employee in employees:
            # Employment is a hard wall on both sides.
            if employee["hire_date"] > finish:
                continue
            departure = employee["departure_date"]
            if departure is not None and departure < month:
                continue

            rate = CLAIM_RATE.get(employee["employee_type"], 0.8) * season
            count = poisson(rng, rate)
            if not count:
                continue

            window_start = max(month, employee["hire_date"], PERIOD_START)
            window_end = min(finish, departure or finish, PERIOD_END)
            if window_start > window_end:
                continue

            days = business_days(window_start, window_end,
                                 employee["country_code"])
            if not days:
                continue

            for _ in range(count):
                drafts.append((rng.choice(days), employee))

    drafts.sort(key=lambda item: (item[0], item[1]["employee_ref"]))

    expenses, sequence = [], {}
    for expense_date, employee in drafts:
        year = expense_date.year
        sequence[year] = sequence.get(year, 0) + 1
        expenses.append(_build_one_expense(
            rng, f"EXP-{year}-{sequence[year]:05d}", expense_date, employee))

    # Freeze the group-currency amount, using the rate of the month the claim
    # was incurred - never a rate looked up later.
    for expense in expenses:
        if expense["currency_code"] == "CHF":
            incurred = expense["expense_date"]
            fx = fx_by_month[(incurred.year, incurred.month)]
            expense["fx_rate_to_eur"] = fx
            expense["gross_amount_eur"] = money(expense["gross_amount"] * fx)

    _assign_expense_status(rng, expenses, approvers_by_country)
    return expenses


def _build_one_expense(rng, expense_id, expense_date, employee):
    country = employee["country_code"]
    month = expense_date.month

    # December is the season for taking clients out; August is nobody's.
    mix = dict(EXPENSE_CATEGORY_MIX)
    if month == 12:
        mix["Client Entertainment"] = 26
        mix["Travel"] = 22
    elif month == 1:
        mix["Training"] = 12
    category = rng.choices(list(mix), weights=list(mix.values()))[0]

    rate = (VAT_REDUCED[country] if category in REDUCED_CATEGORIES
            else VAT_STANDARD[country])
    gross = lognormal(rng, EXPENSE_MEDIAN, EXPENSE_P90, EXPENSE_MAX)
    if category in ("Accommodation", "Travel", "Training"):
        gross *= 2.6                       # a hotel night is not a taxi fare
    gross = min(money(gross), EXPENSE_MAX)

    net = money(gross / (1 + rate / 100))
    tax = money(gross - net)
    gross = money(net + tax)

    currency = "CHF" if country == "CH" else "EUR"
    submitted = add_business_days(expense_date, rng.randint(0, 15), country)
    submitted = min(submitted, TODAY)

    # One instant, reused for both timestamps - see the note in purchases.py.
    recorded = _instant(submitted, 12, rng.randint(0, 59))

    return {
        "expense_id": expense_id,
        "employee_ref": employee["employee_ref"],
        "cost_center_id": employee["cost_center_id"],
        "expense_category": category,
        "expense_date": expense_date,
        "submitted_date": submitted,
        "currency_code": currency,
        "net_amount": net,
        "tax_rate": rate,
        "tax_amount": tax,
        "gross_amount": gross,
        "fx_rate_to_eur": 1.0,             # filled in by the caller for CHF
        "gross_amount_eur": gross,
        "expense_status": "Reimbursed",
        "approved_by_employee_ref": None,
        "approved_at": None,
        "rejection_reason": None,
        "reimbursed_on": None,
        "reimbursement_reference": None,
        # A missing receipt is what gets a claim rejected, so the two are
        # correlated rather than independent.
        "receipt_reference": (None if rng.random() < 0.08
                              else f"RCP-{expense_id[4:]}"),
        "created_at": recorded,
        "updated_at": recorded,
        "_country": country,
    }


def _assign_expense_status(rng, expenses, approvers_by_country):
    """Claim states, with chronology taking precedence over the distribution.

    The specification asks for 5% Approved and 4% Submitted. Applied blindly
    that would put ~1 080 claims in a non-terminal state, including claims
    filed in 2024 - a two-year-old expense report still "awaiting approval" is
    not a realistic firm, it is a broken one.

    So an OPEN state is only available to claims filed in the last 120 days.
    Anything older is terminal. The consequence is a deliberate and reported
    deviation: Approved and Submitted come out lower than the targets, because
    the window physically cannot hold that many open claims. Chronology was
    the harder requirement, so it wins.

    `Draft` is exempt: an abandoned capture stays a draft for ever, at any age.
    """
    total = len(expenses)
    counts = split_exactly(total, EXPENSE_STATUS_MIX)

    cutoff = TODAY - timedelta(days=OPEN_CLAIM_WINDOW_DAYS)
    order = sorted(range(total), key=lambda i: expenses[i]["submitted_date"])
    recent = [i for i in order if expenses[i]["submitted_date"] >= cutoff]

    assigned = {}
    pool = list(reversed(recent))          # newest first

    for label in ("Submitted", "Approved"):
        wanted = counts[label]
        while wanted and pool:
            assigned[pool.pop(0)] = label
            wanted -= 1

    remaining = [i for i in order if i not in assigned]
    rng.shuffle(remaining)
    for label in ("Draft", "Rejected", "Cancelled"):
        for _ in range(min(counts[label], len(remaining))):
            assigned[remaining.pop()] = label
    for index in remaining:
        assigned[index] = "Reimbursed"

    for index, expense in enumerate(expenses):
        status = assigned.get(index, "Reimbursed")
        expense["expense_status"] = status
        country = expense["_country"]
        submitted = expense["submitted_date"]

        if status in ("Draft", "Cancelled"):
            # A draft was never submitted; the schema requires the date to be
            # absent, not merely ignored.
            expense["submitted_date"] = None
            expense["created_at"] = _instant(expense["expense_date"], 12,
                                             rng.randint(0, 59))
            expense["updated_at"] = expense["created_at"]
            continue

        if status == "Submitted":
            continue

        # The approver is chosen for the day the decision is TAKEN, not the
        # day the claim was filed. Choosing on the submission date let 13
        # claims be signed off by managers who had left in between - caught by
        # the pre-commit check, which is what those checks exist for.
        decided = add_business_days(submitted, rng.randint(1, 12), country)
        decided = min(decided, TODAY)
        approver = _pick_approver(rng, approvers_by_country, country,
                                  expense["employee_ref"], decided)

        if status == "Rejected":
            expense["rejection_reason"] = (
                "Missing receipt" if expense["receipt_reference"] is None
                else rng.choice(["Outside policy", "Duplicate claim",
                                 "Personal expense", "Exceeds per-diem limit"]))
            expense["updated_at"] = _instant(min(decided, TODAY), 15,
                                             rng.randint(0, 59))
            continue

        if approver is None:
            expense["expense_status"] = "Submitted"
            continue

        expense["approved_by_employee_ref"] = approver
        expense["approved_at"] = _instant(decided, 15, rng.randint(0, 59))
        expense["updated_at"] = expense["approved_at"]

        if status == "Reimbursed":
            # Reimbursement rides the payroll run: 5 to 30 days after
            # approval, never before submission.
            paid = decided + timedelta(days=rng.randint(5, 30))
            if paid > TODAY:
                expense["expense_status"] = "Approved"
                continue
            expense["reimbursed_on"] = paid
            expense["reimbursement_reference"] = (
                f"PAY-{paid.strftime('%Y%m')}-{rng.randint(100, 999)}")
            expense["updated_at"] = _instant(paid, 17, rng.randint(0, 59))


def _pick_approver(rng, approvers_by_country, country, employee_ref, on_day):
    """Someone else, preferably in the same country, employed that day.

    Segregation of duties is a schema constraint, so this can never return the
    claimant. It also cannot return someone who had already left - an approval
    signed by a departed manager is the kind of detail that makes a dataset
    fail its own integrity checks.

    The country is a preference, not a rule: a small office may have no
    manager of its own on a given day, and the claim is then approved at group
    level. Returning None instead would leave the claim stuck in `Submitted`.
    """
    return _pick_from(rng, approvers_by_country, country, employee_ref, on_day)


def _pick_from(rng, pool_by_country, country, employee_ref, on_day):
    for key in (country, "*"):
        candidates = [row for row in pool_by_country.get(key, [])
                      if row["employee_ref"] != employee_ref
                      and row["hire_date"] <= on_day
                      and (row["departure_date"] is None
                           or row["departure_date"] >= on_day)]
        if candidates:
            return rng.choice(candidates)["employee_ref"]
    return None


# ---------------------------------------------------------------------------
# Commissions
# ---------------------------------------------------------------------------
# Three types, and no more. The specification is explicit that the ~5 900
# figure must come from the HEADCOUNT CURVE, not from inventing a fourth type
# to make up the numbers:
#
#   Recurring Management Fee   monthly, ~97% of eligible advisors
#   New Business               monthly, ~85% - not everyone wins a mandate
#   Performance Bonus          annual,  ~70% - three cycles in the window
#
# Eligibility is a contractual fact, not a formality: advisors on a fixed
# salary earn none, and an advisor must have been employed for the WHOLE
# period to be entitled to it.

COMMISSION_PARTICIPATION = {
    "Recurring Management Fee": 0.97,
    "New Business": 0.85,
}

BONUS_PARTICIPATION = 0.70

COMMISSION_BASIS_MEDIAN = 46000.0
COMMISSION_BASIS_P90 = 165000.0
COMMISSION_BASIS_MAX = 900000.0

COMMISSION_RATES = {
    "Recurring Management Fee": (0.0060, 0.0190),
    "New Business": (0.0080, 0.0250),
    "Performance Bonus": (0.0100, 0.0320),
}

CANCELLED_SHARE = 0.015


def build_commissions(employees, validators_by_country, fx_by_month):
    """~5 900 rows, produced by the headcount curve across 36 months."""
    rng = new_rng("commissions")
    advisors = [row for row in employees
                if row["employee_type"] == "Advisor" and row["is_commission_eligible"]]

    rows, sequence = [], {}

    def emit(advisor, kind, period_type, period_start, period_end, calculated_on):
        year = calculated_on.year
        sequence[year] = sequence.get(year, 0) + 1
        rows.append(_build_one_commission(
            rng, f"COM-{year}-{sequence[year]:05d}", advisor, kind, period_type,
            period_start, period_end, calculated_on, validators_by_country,
            fx_by_month))

    # -- monthly runs -------------------------------------------------------
    for month in months_in_period():
        finish = month_end(month)
        # The run happens on the last business day of the month.
        run_day = finish
        while run_day.weekday() >= 5:
            run_day -= timedelta(days=1)
        if run_day > TODAY:
            continue

        for advisor in advisors:
            # Employed for the WHOLE month, or no entitlement for it.
            if advisor["hire_date"] > month:
                continue
            departure = advisor["departure_date"]
            if departure is not None and departure < finish:
                continue

            for kind, participation in COMMISSION_PARTICIPATION.items():
                if rng.random() < participation:
                    emit(advisor, kind, "Monthly", month, finish, run_day)

    # -- annual bonus -------------------------------------------------------
    # Calculated in the January following the year it rewards, so the 2023,
    # 2024 and 2025 cycles all fall inside the window.
    for year in (2023, 2024, 2025):
        period_start, period_end = date(year, 1, 1), date(year, 12, 31)
        run_day = date(year + 1, 1, 31)
        while run_day.weekday() >= 5:
            run_day -= timedelta(days=1)
        if run_day > TODAY:
            continue

        for advisor in advisors:
            if advisor["hire_date"] > period_start:
                continue
            departure = advisor["departure_date"]
            if departure is not None and departure < period_end:
                continue
            if rng.random() < BONUS_PARTICIPATION:
                emit(advisor, "Performance Bonus", "Annual", period_start,
                     period_end, run_day)

    return rows


def _build_one_commission(rng, commission_id, advisor, kind, period_type,
                          period_start, period_end, calculated_on,
                          validators_by_country, fx_by_month):
    country = advisor["country_code"]
    currency = "CHF" if country == "CH" else "EUR"

    basis = money(lognormal(rng, COMMISSION_BASIS_MEDIAN, COMMISSION_BASIS_P90,
                            COMMISSION_BASIS_MAX))
    low, high = COMMISSION_RATES[kind]
    rate = round(rng.uniform(low, high), 4)
    amount = money(basis * rate)

    fx = 1.0 if currency == "EUR" else fx_by_month[(period_end.year,
                                                    period_end.month)]

    calculated_at = _instant(calculated_on, 20, rng.randint(0, 59))

    row = {
        "commission_id": commission_id,
        "employee_ref": advisor["employee_ref"],
        "cost_center_id": advisor["cost_center_id"],
        "period_type": period_type,
        "period_start": period_start,
        "period_end": period_end,
        "commission_type": kind,
        "basis_amount": basis,
        "commission_rate": rate,
        "currency_code": currency,
        "commission_amount": amount,
        "fx_rate_to_eur": fx,
        "commission_amount_eur": money(amount * fx),
        "commission_status": "Calculated",
        "calculated_at": calculated_at,
        "validated_at": None,
        "validated_by_employee_ref": None,
        "paid_on": None,
        "payroll_reference": None,
        "cancellation_reason": None,
        "source_system": "CRM",
        "created_at": calculated_at,
        "updated_at": calculated_at,
    }

    # -- the lifecycle, driven by how long ago the run happened -------------
    age = (TODAY - calculated_on).days

    if rng.random() < CANCELLED_SHARE:
        row["commission_status"] = "Cancelled"
        row["cancellation_reason"] = rng.choice([
            "Clawback - mandate terminated", "Recalculated after correction",
            "Duplicate run", "Client left within retention period",
        ])
        # A run that happens at 20:11 can be cancelled the same evening, so
        # the clock hour alone is not enough to order the two events. Taking
        # the later of the two keeps updated_at >= created_at, which the
        # schema enforces and which a clawback booked "before" its own
        # calculation would violate.
        cancelled_on = min(calculated_on + timedelta(days=rng.randint(5, 60)),
                           TODAY)
        moment = _instant(cancelled_on, 10, rng.randint(0, 59))
        row["updated_at"] = max(moment, calculated_at + timedelta(minutes=20))
        return row

    if age < 12:
        return row                              # still merely calculated

    validator = _pick_validator(rng, validators_by_country, country,
                                advisor["employee_ref"], calculated_on)
    if validator is None:
        return row

    validated_on = min(calculated_on + timedelta(days=rng.randint(3, 11)), TODAY)
    row["commission_status"] = "Validated"
    row["validated_at"] = _instant(validated_on, 9, rng.randint(0, 59))
    row["validated_by_employee_ref"] = validator
    row["updated_at"] = row["validated_at"]

    if age < 45:
        return row                              # validated, awaiting payroll

    paid_on = min(validated_on + timedelta(days=rng.randint(10, 32)), TODAY)
    row["commission_status"] = "Paid"
    row["paid_on"] = paid_on
    row["payroll_reference"] = f"PR-{paid_on.strftime('%Y%m')}"
    row["updated_at"] = _instant(paid_on, 16, rng.randint(0, 59))
    return row


def _pick_validator(rng, validators_by_country, country, employee_ref, on_day):
    """Whoever signs off the commission run - never the advisor who earned it."""
    return _pick_from(rng, validators_by_country, country, employee_ref, on_day)

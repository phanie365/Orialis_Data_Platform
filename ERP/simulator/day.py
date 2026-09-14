"""
One business day in the life of the Orialis ERP.

---------------------------------------------------------------------------
THE SAME RULES AS THE SEED, APPLIED ONE DAY AT A TIME
---------------------------------------------------------------------------

This module imports its business rules from `ERP.seed` rather than restating
them: the same Poisson rates, the same seasonality, the same VAT derivation,
the same row builders. A simulator with its own copy of the rules drifts away
from the history it is supposed to continue, and the drift is invisible until
someone plots a metric across the boundary.

What differs is the TIME SCALE. The seed asks "how many invoices does this
supplier issue in this month?"; the simulator asks the same question for one
day, by dividing the monthly rate across that month's working days. The
statistical behaviour is identical; only the granularity changes.

---------------------------------------------------------------------------
THE ORDER OF A DAY
---------------------------------------------------------------------------

    1. FX rate          on the first business day of a month
    2. payments settle  Initiated -> Executed / Failed
    3. invoices advance Draft -> Pending Approval -> Approved / Rejected
    4. expenses advance Draft -> Submitted -> Approved/Rejected -> Reimbursed
    5. commissions      Calculated -> Validated -> Paid
    6. new invoices     business days
    7. new expenses     business days
    8. payment run      Tuesdays and Fridays
    9. commission run   last business day of the month
   10. corrections      an occasional mis-keyed allocation, cancelled
   11. reference data   a rare supplier or employee change
   12. settlement       paid_amount recomputed for every invoice touched

Step 12 is last for a reason: every earlier step can change what an invoice
has been paid, and `paid_amount` is never written by the step that caused the
change. It is recomputed from the allocations, by the one rule, exactly as in
the seed.

Note what step 8 does NOT do: reissue a failed payment. It does not need to.
A failed transfer leaves its invoice approved and unpaid, so the next payment
run picks it up again on its own - the retry is an emergent behaviour of the
rules rather than a special case.

---------------------------------------------------------------------------
WEEKENDS AND HOLIDAYS
---------------------------------------------------------------------------

A weekend is a real no-op: no invoices are received, no claims are filed, no
bank settles. The day is recorded as simulated and nothing else happens.

Public holidays are per country - an Italian office works on 14 July and a
French one does not - so a holiday suppresses activity only for the countries
that observe it.
"""

from datetime import date, datetime, timedelta, timezone

from ..seed.activity import (CLAIM_RATE, COMMISSION_PARTICIPATION,
                             _build_one_expense, _build_one_commission,
                             _pick_approver, _pick_validator)
from ..seed.purchases import (LATENESS_MIX, PAYMENT_METHOD_MIX,
                              _build_one_invoice, _pick_approver as _pick_invoice_approver)
from ..seed.reference import COUNTRY_OF_COST_CENTRE
from ..seed.toolkit import (EXPENSE_SEASONALITY, INVOICE_SEASONALITY,
                            business_days, is_business_day, money, month_end,
                            poisson)

COUNTRIES = ("FR", "BE", "CH", "IT")

# Treasury sits in Paris: the payment calendar follows the French one.
TREASURY_COUNTRY = "FR"
PAYMENT_RUN_WEEKDAYS = (1, 4)          # Tuesday, Friday

# How a day's transitions are paced. All expressed as "what happens to a row
# that has been sitting in this state", so the resulting dwell times match the
# ones the seed produced.
DRAFT_INVOICE_SUBMIT_ODDS = 0.12       # most drafts are eventually abandoned
PENDING_DECISION_MIN_DAYS = 1
PENDING_APPROVAL_ODDS = 0.28           # per day, once old enough
INVOICE_REJECTION_SHARE = 0.027        # of decided invoices

DRAFT_EXPENSE_SUBMIT_ODDS = 0.15
EXPENSE_DECISION_ODDS = 0.30
EXPENSE_REJECTION_SHARE = 0.036
REIMBURSEMENT_MIN_DAYS = 5
REIMBURSEMENT_ODDS = 0.22

PAYMENT_SETTLE_MIN_DAYS = 1
PAYMENT_OUTCOME = {"Executed": 97.5, "Failed": 1.5, "Cancelled": 1.0}

COMMISSION_VALIDATE_MIN_DAYS = 12
COMMISSION_VALIDATE_ODDS = 0.35
COMMISSION_PAY_MIN_DAYS = 45
COMMISSION_PAY_ODDS = 0.40

# An allocation is mis-keyed roughly once a fortnight of business days, which
# reproduces the ~120 corrections per 36 months of the seed.
MISKEY_ODDS = 0.09

# Reference data barely moves. These are per business day.
SUPPLIER_DEACTIVATION_ODDS = 0.012
SUPPLIER_ONBOARDING_ODDS = 0.020
EMPLOYEE_DEPARTURE_ODDS = 0.010
EMPLOYEE_HIRE_ODDS = 0.014

BONUS_PARTICIPATION = 0.70


def _instant(day, hour, minute=0):
    return datetime(day.year, day.month, day.day, hour, minute,
                    tzinfo=timezone.utc)


def new_report():
    return {"created": {}, "updated": {}, "transitions": {}, "notes": []}


def _count(report, bucket, key, amount=1):
    report[bucket][key] = report[bucket].get(key, 0) + amount


# ===========================================================================
# Context
# ===========================================================================

def load_context(cursor, day):
    """Everything the day needs, read once from the database.

    The simulator reads the CURRENT state rather than carrying the seed's
    in-memory objects: a supplier deactivated three simulated days ago must
    stop billing today, and that fact only exists in the database.
    """
    cursor.execute("""
        SELECT supplier_id, supplier_category, country_code, default_currency,
               payment_terms_days, supplier_status, onboarded_on, deactivated_on
        FROM suppliers
    """)
    suppliers = [dict(zip(
        ("supplier_id", "supplier_category", "country_code", "default_currency",
         "payment_terms_days", "supplier_status", "onboarded_on",
         "deactivated_on"), row)) for row in cursor.fetchall()]

    # THE BILLING RATE IS OBSERVED, NOT ASSUMED.
    #
    # The seed knew each supplier's tier because it invented it. The simulator
    # does not have that memory, and hard-coding the tier boundaries by id
    # would break the moment a supplier is onboarded mid-simulation. So the
    # rate is measured from the trailing twelve months of the supplier's own
    # ledger - the simulator continues the behaviour the data shows rather
    # than a behaviour it was told about.
    cursor.execute("""
        SELECT supplier_id, COUNT(*) / 12.0
        FROM supplier_invoices
        WHERE invoice_date > %s - INTERVAL '12 months'
        GROUP BY supplier_id
    """, (day,))
    observed = dict(cursor.fetchall())

    cursor.execute("SELECT supplier_id, COUNT(*) FROM supplier_invoices GROUP BY 1")
    ordinals = dict(cursor.fetchall())

    for supplier in suppliers:
        supplier["_monthly_rate"] = float(observed.get(supplier["supplier_id"], 0.04))
        supplier["_ordinal"] = ordinals.get(supplier["supplier_id"], 0)

    cursor.execute("""
        SELECT employee_ref, first_name, last_name, employee_type,
               cost_center_id, office_code, country_code, hire_date,
               departure_date, is_commission_eligible, employee_status
        FROM employees
    """)
    employees = [dict(zip(
        ("employee_ref", "first_name", "last_name", "employee_type",
         "cost_center_id", "office_code", "country_code", "hire_date",
         "departure_date", "is_commission_eligible", "employee_status"),
        row)) for row in cursor.fetchall()]

    cursor.execute("""
        SELECT cost_center_id, cost_center_type, country_code
        FROM cost_centers WHERE cost_center_status = 'Active'
    """)
    cost_centres = [dict(zip(("cost_center_id", "cost_center_type",
                              "country_code"), row)) for row in cursor.fetchall()]
    chargeable = [row["cost_center_id"] for row in cost_centres
                  if row["cost_center_type"] in ("Branch", "Function")]
    by_country = {}
    for identifier in chargeable:
        by_country.setdefault(COUNTRY_OF_COST_CENTRE[identifier], []).append(identifier)

    cursor.execute("SELECT rate_month, rate FROM fx_rates "
                   "WHERE from_currency = 'CHF' AND to_currency = 'EUR'")
    fx = {(row[0].year, row[0].month): float(row[1]) for row in cursor.fetchall()}

    return {
        "suppliers": suppliers,
        "employees": employees,
        "chargeable": chargeable,
        "by_country": by_country,
        "fx": fx,
        "approvers": _pool(employees, ("Management", "Back Office")),
        "validators": _pool(employees, ("Management", "Compliance")),
        "sequences": _load_sequences(cursor, day.year),
    }


def _pool(employees, types):
    pool = {"*": []}
    for row in employees:
        if row["employee_type"] in types:
            pool.setdefault(row["country_code"], []).append(row)
            pool["*"].append(row)
    return pool


def _load_sequences(cursor, year):
    """Continue each per-year id sequence from where the data stops.

    The simulator is deliberately NOT idempotent, so it must never reuse an
    id. Reading the maximum back from the database is what lets it append to a
    history it did not itself generate.
    """
    sequences = {}
    for prefix, table, column in (
        ("INV", "supplier_invoices", "invoice_id"),
        ("PAY", "payments", "payment_id"),
        ("ALC", "payment_allocations", "allocation_id"),
        ("EXP", "expenses", "expense_id"),
        ("COM", "commissions", "commission_id"),
    ):
        cursor.execute(
            f"SELECT MAX(RIGHT({column}, 5)) FROM {table} "
            f"WHERE {column} LIKE %s", (f"{prefix}-{year}-%",))
        highest = cursor.fetchone()[0]
        sequences[prefix] = int(highest) if highest else 0
    return sequences


def _next_id(context, prefix, year):
    context["sequences"][prefix] += 1
    return f"{prefix}-{year}-{context['sequences'][prefix]:05d}"


# ===========================================================================
# The day
# ===========================================================================

def simulate_day(cursor, day, rng, report=None):
    report = report or new_report()
    touched_invoices = set()

    weekday = day.weekday()
    if weekday >= 5:
        report["notes"].append("week-end : aucune activite metier")
        return report, False, touched_invoices

    working = [country for country in COUNTRIES if is_business_day(day, country)]
    if not working:
        report["notes"].append("jour ferie dans les quatre pays")
        return report, False, touched_invoices

    if len(working) < len(COUNTRIES):
        closed = [c for c in COUNTRIES if c not in working]
        report["notes"].append(f"ferie en {', '.join(closed)}")

    context = load_context(cursor, day)

    _ensure_fx_rate(cursor, day, context, report)
    _settle_payments(cursor, day, rng, report, touched_invoices)

    # SETTLE BEFORE ANYTHING READS THE LEDGER.
    #
    # The payment run below decides what to pay from `paid_amount`. If the
    # recomputation only happened at the end of the day, the run would read a
    # figure that this morning's settlements had already made stale - and it
    # did: an invoice cleared by a transfer executing at 11:00 was paid a
    # second time at 10:00 the same Friday, because its paid_amount still
    # showed the previous balance.
    _recompute_settlement(cursor, day, touched_invoices, report)

    _advance_invoices(cursor, day, rng, context, working, report)
    _advance_expenses(cursor, day, rng, context, working, report)
    _advance_commissions(cursor, day, rng, context, report)
    _new_invoices(cursor, day, rng, context, working, report)
    _new_expenses(cursor, day, rng, context, working, report)

    if weekday in PAYMENT_RUN_WEEKDAYS and is_business_day(day, TREASURY_COUNTRY):
        _payment_run(cursor, day, rng, context, report, touched_invoices)

    if _is_last_business_day_of_month(day):
        _commission_run(cursor, day, rng, context, report)

    if rng.random() < MISKEY_ODDS:
        _miskey_allocation(cursor, day, rng, context, report, touched_invoices)

    _reference_data(cursor, day, rng, context, report)
    _recompute_settlement(cursor, day, touched_invoices, report)

    return report, True, touched_invoices


def _is_last_business_day_of_month(day):
    finish = month_end(day)
    remaining = business_days(day + timedelta(days=1), finish, TREASURY_COUNTRY)
    return is_business_day(day, TREASURY_COUNTRY) and not remaining


# ---------------------------------------------------------------------------
# 1. FX rate
# ---------------------------------------------------------------------------

def _ensure_fx_rate(cursor, day, context, report):
    """A month that has begun needs its rate before anything is booked in CHF.

    The walk continues from the previous month rather than restarting, so the
    series stays a series across the seed/simulation boundary.
    """
    key = (day.year, day.month)
    if key in context["fx"]:
        return

    cursor.execute("""
        SELECT rate FROM fx_rates
        WHERE from_currency = 'CHF' AND to_currency = 'EUR'
        ORDER BY rate_month DESC LIMIT 1
    """)
    row = cursor.fetchone()
    previous = float(row[0]) if row else 1.0745

    import random
    walk = random.Random(f"fx:{day.year}-{day.month}")
    rate = round(previous + walk.gauss(0.0008, 0.0045), 6)

    cursor.execute(
        "INSERT INTO fx_rates (rate_month, from_currency, to_currency, rate, "
        "created_at) VALUES (%s, 'CHF', 'EUR', %s, %s) "
        "ON CONFLICT DO NOTHING",
        (date(day.year, day.month, 1), rate, _instant(day, 6)),
    )
    context["fx"][key] = rate
    _count(report, "created", "fx_rates")


# ---------------------------------------------------------------------------
# 2. Payments settle
# ---------------------------------------------------------------------------

def _settle_payments(cursor, day, rng, report, touched):
    """Initiated -> Executed / Failed / Cancelled, after a day or three.

    A failed transfer is NOT retried here. The invoice it was meant to settle
    stays approved and unpaid, so the next payment run finds it again on its
    own. The retry is emergent, not scripted.
    """
    cursor.execute("""
        SELECT payment_id, payment_date FROM payments
        WHERE payment_status = 'Initiated' AND payment_date <= %s
        ORDER BY payment_id
    """, (day - timedelta(days=PAYMENT_SETTLE_MIN_DAYS),))
    pending = cursor.fetchall()

    labels = list(PAYMENT_OUTCOME)
    weights = list(PAYMENT_OUTCOME.values())

    for payment_id, payment_date in pending:
        # A transfer ordered three days ago has certainly resolved; one
        # ordered yesterday may still be in flight.
        age = (day - payment_date).days
        if age < 3 and rng.random() < 0.35:
            continue

        outcome = rng.choices(labels, weights=weights)[0]
        moment = _instant(day, 11, rng.randint(0, 59))

        if outcome == "Executed":
            cursor.execute("""
                UPDATE payments SET payment_status = 'Executed', executed_at = %s,
                       value_date = %s, updated_at = %s
                WHERE payment_id = %s
            """, (moment, day, moment, payment_id))
        elif outcome == "Failed":
            cursor.execute("""
                UPDATE payments SET payment_status = 'Failed',
                       failure_reason = %s, updated_at = %s
                WHERE payment_id = %s
            """, (rng.choice(["Invalid IBAN", "Account closed",
                              "Insufficient funds",
                              "Rejected by beneficiary bank"]),
                  moment, payment_id))
        else:
            cursor.execute("""
                UPDATE payments SET payment_status = 'Cancelled', updated_at = %s
                WHERE payment_id = %s
            """, (moment, payment_id))

        _count(report, "transitions", f"payment Initiated->{outcome}")
        cursor.execute("SELECT invoice_id FROM payment_allocations "
                       "WHERE payment_id = %s", (payment_id,))
        touched.update(row[0] for row in cursor.fetchall())


# ---------------------------------------------------------------------------
# 3. Invoices advance
# ---------------------------------------------------------------------------

def _advance_invoices(cursor, day, rng, context, working, report):
    # Draft -> Pending Approval. Most drafts are never completed; that is what
    # keeps a permanent population of abandoned captures, as in the seed.
    cursor.execute("""
        SELECT invoice_id, cost_center_id FROM supplier_invoices
        WHERE invoice_approval_status = 'Draft' AND received_date <= %s
        ORDER BY invoice_id
    """, (day - timedelta(days=1),))
    for invoice_id, cost_centre in cursor.fetchall():
        if COUNTRY_OF_COST_CENTRE.get(cost_centre) not in working:
            continue
        if rng.random() >= DRAFT_INVOICE_SUBMIT_ODDS:
            continue
        cursor.execute("""
            UPDATE supplier_invoices
            SET invoice_approval_status = 'Pending Approval', updated_at = %s
            WHERE invoice_id = %s
        """, (_instant(day, 10, rng.randint(0, 59)), invoice_id))
        _count(report, "transitions", "invoice Draft->Pending Approval")

    # Pending Approval -> Approved / Rejected.
    cursor.execute("""
        SELECT invoice_id, cost_center_id FROM supplier_invoices
        WHERE invoice_approval_status = 'Pending Approval' AND received_date <= %s
        ORDER BY invoice_id
    """, (day - timedelta(days=PENDING_DECISION_MIN_DAYS),))
    for invoice_id, cost_centre in cursor.fetchall():
        country = COUNTRY_OF_COST_CENTRE.get(cost_centre)
        if country not in working or rng.random() >= PENDING_APPROVAL_ODDS:
            continue

        moment = _instant(day, 14, rng.randint(0, 59))
        if rng.random() < INVOICE_REJECTION_SHARE:
            cursor.execute("""
                UPDATE supplier_invoices
                SET invoice_approval_status = 'Rejected', rejection_reason = %s,
                    updated_at = %s
                WHERE invoice_id = %s
            """, (rng.choice(["Disputed amount", "Goods not received",
                              "Duplicate billing", "Missing purchase order"]),
                  moment, invoice_id))
            _count(report, "transitions", "invoice Pending->Rejected")
            continue

        approver = _pick_invoice_approver(rng, context["approvers"], country, day)
        if approver is None:
            continue
        cursor.execute("""
            UPDATE supplier_invoices
            SET invoice_approval_status = 'Approved',
                approved_by_employee_ref = %s, approved_at = %s, updated_at = %s
            WHERE invoice_id = %s
        """, (approver, moment, moment, invoice_id))
        _count(report, "transitions", "invoice Pending->Approved")


# ---------------------------------------------------------------------------
# 4. Expenses advance
# ---------------------------------------------------------------------------

def _advance_expenses(cursor, day, rng, context, working, report):
    cursor.execute("""
        SELECT expense_id, employee_ref, expense_date FROM expenses
        WHERE expense_status = 'Draft' AND expense_date <= %s
        ORDER BY expense_id
    """, (day - timedelta(days=1),))
    for expense_id, employee_ref, expense_date in cursor.fetchall():
        if rng.random() >= DRAFT_EXPENSE_SUBMIT_ODDS:
            continue
        moment = _instant(day, 12, rng.randint(0, 59))
        cursor.execute("""
            UPDATE expenses SET expense_status = 'Submitted', submitted_date = %s,
                   updated_at = %s
            WHERE expense_id = %s
        """, (day, moment, expense_id))
        _count(report, "transitions", "expense Draft->Submitted")

    cursor.execute("""
        SELECT e.expense_id, e.employee_ref, e.receipt_reference, emp.country_code
        FROM expenses e JOIN employees emp USING (employee_ref)
        WHERE e.expense_status = 'Submitted' AND e.submitted_date <= %s
        ORDER BY e.expense_id
    """, (day - timedelta(days=1),))
    for expense_id, employee_ref, receipt, country in cursor.fetchall():
        if country not in working or rng.random() >= EXPENSE_DECISION_ODDS:
            continue
        moment = _instant(day, 15, rng.randint(0, 59))

        # A missing receipt is what gets a claim rejected, so the two stay
        # correlated exactly as they are in the seed.
        odds = EXPENSE_REJECTION_SHARE * (4.0 if receipt is None else 1.0)
        if rng.random() < odds:
            cursor.execute("""
                UPDATE expenses SET expense_status = 'Rejected',
                       rejection_reason = %s, updated_at = %s
                WHERE expense_id = %s
            """, ("Missing receipt" if receipt is None
                  else rng.choice(["Outside policy", "Duplicate claim",
                                   "Personal expense", "Exceeds per-diem limit"]),
                  moment, expense_id))
            _count(report, "transitions", "expense Submitted->Rejected")
            continue

        approver = _pick_approver(rng, context["approvers"], country,
                                  employee_ref, day)
        if approver is None:
            continue
        cursor.execute("""
            UPDATE expenses SET expense_status = 'Approved',
                   approved_by_employee_ref = %s, approved_at = %s, updated_at = %s
            WHERE expense_id = %s
        """, (approver, moment, moment, expense_id))
        _count(report, "transitions", "expense Submitted->Approved")

    # Approved -> Reimbursed, on the payroll cycle.
    cursor.execute("""
        SELECT expense_id FROM expenses
        WHERE expense_status = 'Approved' AND approved_at::date <= %s
        ORDER BY expense_id
    """, (day - timedelta(days=REIMBURSEMENT_MIN_DAYS),))
    for (expense_id,) in cursor.fetchall():
        if rng.random() >= REIMBURSEMENT_ODDS:
            continue
        moment = _instant(day, 17, rng.randint(0, 59))
        cursor.execute("""
            UPDATE expenses SET expense_status = 'Reimbursed', reimbursed_on = %s,
                   reimbursement_reference = %s, updated_at = %s
            WHERE expense_id = %s
        """, (day, f"PAY-{day.strftime('%Y%m')}-{rng.randint(100, 999)}",
              moment, expense_id))
        _count(report, "transitions", "expense Approved->Reimbursed")


# ---------------------------------------------------------------------------
# 5. Commissions advance
# ---------------------------------------------------------------------------

def _advance_commissions(cursor, day, rng, context, report):
    cursor.execute("""
        SELECT c.commission_id, c.employee_ref, emp.country_code
        FROM commissions c JOIN employees emp USING (employee_ref)
        WHERE c.commission_status = 'Calculated' AND c.calculated_at::date <= %s
        ORDER BY c.commission_id
    """, (day - timedelta(days=COMMISSION_VALIDATE_MIN_DAYS),))
    for commission_id, employee_ref, country in cursor.fetchall():
        if rng.random() >= COMMISSION_VALIDATE_ODDS:
            continue
        validator = _pick_validator(rng, context["validators"], country,
                                    employee_ref, day)
        if validator is None:
            continue
        moment = _instant(day, 9, rng.randint(0, 59))
        cursor.execute("""
            UPDATE commissions SET commission_status = 'Validated',
                   validated_at = %s, validated_by_employee_ref = %s,
                   updated_at = %s
            WHERE commission_id = %s
        """, (moment, validator, moment, commission_id))
        _count(report, "transitions", "commission Calculated->Validated")

    cursor.execute("""
        SELECT commission_id FROM commissions
        WHERE commission_status = 'Validated' AND calculated_at::date <= %s
        ORDER BY commission_id
    """, (day - timedelta(days=COMMISSION_PAY_MIN_DAYS),))
    for (commission_id,) in cursor.fetchall():
        if rng.random() >= COMMISSION_PAY_ODDS:
            continue
        moment = _instant(day, 16, rng.randint(0, 59))
        cursor.execute("""
            UPDATE commissions SET commission_status = 'Paid', paid_on = %s,
                   payroll_reference = %s, updated_at = %s
            WHERE commission_id = %s
        """, (day, f"PR-{day.strftime('%Y%m')}", moment, commission_id))
        _count(report, "transitions", "commission Validated->Paid")


# ---------------------------------------------------------------------------
# 6-7. New business
# ---------------------------------------------------------------------------

def _new_invoices(cursor, day, rng, context, working, report):
    """Today's share of each supplier's monthly rate."""
    finish = month_end(day)
    start = date(day.year, day.month, 1)
    season = INVOICE_SEASONALITY[day.month]

    for supplier in context["suppliers"]:
        if supplier["onboarded_on"] > day:
            continue
        deactivated = supplier["deactivated_on"]
        if deactivated is not None and deactivated < day:
            continue

        country = supplier["country_code"]
        calendar = country if country in COUNTRIES else TREASURY_COUNTRY
        working_days = len(business_days(start, finish, calendar)) or 1
        daily = supplier["_monthly_rate"] * season / working_days

        count = poisson(rng, daily)
        for _ in range(count):
            supplier["_ordinal"] += 1

            # The supplier issued it a few days ago; it lands on the desk
            # TODAY. Building it from the issue date and then forcing the
            # reception date is what keeps received_date >= invoice_date -
            # the seed's builder clamps dates to the end of the historical
            # window, which is in the past once the simulator is running.
            issued = day - timedelta(days=rng.randint(0, 5))
            invoice = _build_one_invoice(
                rng, _next_id(context, "INV", day.year), issued, supplier,
                context["by_country"], context["chargeable"], context["fx"],
                supplier["_ordinal"])

            if COUNTRY_OF_COST_CENTRE.get(invoice["cost_center_id"]) not in working:
                context["sequences"]["INV"] -= 1
                supplier["_ordinal"] -= 1
                continue

            recorded = _instant(day, 9, rng.randint(0, 59))
            invoice["received_date"] = day
            invoice["created_at"] = recorded
            invoice["updated_at"] = recorded

            # A NEWLY RECEIVED INVOICE IS NOT APPROVED.
            #
            # The seed builder leaves the status at 'Approved' as a
            # placeholder, because the seed assigns the whole distribution
            # afterwards in one pass. The simulator has no "afterwards": an
            # invoice arrives undecided and is approved on a later day, by
            # `_advance_invoices`. That is the difference between describing
            # a history and living one.
            invoice["invoice_approval_status"] = (
                "Draft" if rng.random() < 0.10 else "Pending Approval")
            invoice["approved_by_employee_ref"] = None
            invoice["approved_at"] = None
            invoice["rejection_reason"] = None

            _insert_invoice(cursor, invoice)
            _count(report, "created", "supplier_invoices")


def _insert_invoice(cursor, invoice):
    columns = [name for name in invoice if not name.startswith("_")]
    cursor.execute(
        f"INSERT INTO supplier_invoices ({', '.join(columns)}) "
        f"VALUES ({', '.join(['%s'] * len(columns))})",
        tuple(invoice[name] for name in columns))


def _new_expenses(cursor, day, rng, context, working, report):
    finish = month_end(day)
    start = date(day.year, day.month, 1)
    season = EXPENSE_SEASONALITY[day.month]

    for employee in context["employees"]:
        if employee["employee_status"] != "Active":
            continue
        if employee["hire_date"] > day:
            continue
        country = employee["country_code"]
        if country not in working:
            continue

        working_days = len(business_days(start, finish, country)) or 1
        daily = (CLAIM_RATE.get(employee["employee_type"], 0.8) * season
                 / working_days)

        for _ in range(poisson(rng, daily)):
            expense = _build_one_expense(
                rng, _next_id(context, "EXP", day.year), day, employee)
            if expense["currency_code"] == "CHF":
                fx = context["fx"].get((day.year, day.month), 1.0)
                expense["fx_rate_to_eur"] = fx
                expense["gross_amount_eur"] = money(expense["gross_amount"] * fx)
            # The claim is filed today. The seed spread submission over a
            # fortnight after the expense; here the lag is produced instead by
            # the Draft -> Submitted transition, day by day.
            #
            # The dates and timestamps must be rewritten because the seed
            # builder clamps them to the end of the historical window, which
            # is now in the past.
            recorded = _instant(day, 12, rng.randint(0, 59))
            expense["expense_date"] = day
            expense["submitted_date"] = day
            expense["expense_status"] = "Submitted"
            expense["approved_by_employee_ref"] = None
            expense["approved_at"] = None
            expense["rejection_reason"] = None
            expense["reimbursed_on"] = None
            expense["reimbursement_reference"] = None
            expense["created_at"] = recorded
            expense["updated_at"] = recorded
            if rng.random() < 0.06:
                expense["expense_status"] = "Draft"
                expense["submitted_date"] = None
            columns = [name for name in expense if not name.startswith("_")]
            cursor.execute(
                f"INSERT INTO expenses ({', '.join(columns)}) "
                f"VALUES ({', '.join(['%s'] * len(columns))})",
                tuple(expense[name] for name in columns))
            _count(report, "created", "expenses")


# ---------------------------------------------------------------------------
# 8. Payment run
# ---------------------------------------------------------------------------

def _payment_run(cursor, day, rng, context, report, touched):
    """Tuesday and Friday: pay what is due, grouped by supplier.

    Every payment created here starts as `Initiated`. It settles one to three
    days later, in `_settle_payments`. That is what produces a realistic
    population of in-flight transfers at any moment - and what makes
    `paid_amount` lag the payment run, as it does in a real treasury.
    """
    horizon = day + timedelta(days=4)
    cursor.execute("""
        SELECT i.invoice_id, i.supplier_id, i.currency_code, i.gross_amount,
               i.paid_amount, i.due_date, s.supplier_status, s.deactivated_on
        FROM supplier_invoices i
        JOIN suppliers s USING (supplier_id)
        WHERE i.invoice_approval_status = 'Approved'
          AND i.invoice_payment_status <> 'Paid'
          AND i.due_date <= %s
          AND i.invoice_type = 'Standard'
          AND NOT EXISTS (
              SELECT 1 FROM payment_allocations pa
              JOIN payments p ON p.payment_id = pa.payment_id
              WHERE pa.invoice_id = i.invoice_id
                AND pa.allocation_status = 'Active'
                AND p.payment_status = 'Initiated')
        ORDER BY i.due_date, i.invoice_id
    """, (horizon,))
    due = cursor.fetchall()

    groups = {}
    for (invoice_id, supplier_id, currency, gross, paid, due_date,
         status, deactivated) in due:
        # A BLOCKED supplier is not paid while the block lasts. An Inactive
        # one is: the debt is still owed.
        if status == "Blocked" and deactivated is not None and day >= deactivated:
            continue

        outstanding = money(float(gross) - float(paid))
        if outstanding <= 0:
            continue

        # One settlement in twenty is deliberately partial.
        amount = outstanding
        if float(paid) == 0 and rng.random() < 0.05:
            amount = money(outstanding * round(rng.uniform(0.3, 0.7), 2))

        groups.setdefault(supplier_id, []).append((invoice_id, currency, amount))

    methods = list(PAYMENT_METHOD_MIX)
    weights = list(PAYMENT_METHOD_MIX.values())

    for supplier_id, lines in sorted(groups.items()):
        # Batch a supplier's invoices only sometimes, exactly as the seed did.
        batches = [lines] if (len(lines) == 1 or rng.random() < 0.45) \
            else [[line] for line in lines]

        for batch in batches:
            currency = batch[0][1]
            batch = [line for line in batch if line[1] == currency]
            total = money(sum(line[2] for line in batch))
            if total <= 0:
                continue

            payment_id = _next_id(context, "PAY", day.year)
            ordered_at = _instant(day, 10, rng.randint(0, 59))
            cursor.execute("""
                INSERT INTO payments (payment_id, supplier_id, payment_date,
                    value_date, currency_code, payment_amount, payment_method,
                    payment_status, bank_reference, executed_at, failure_reason,
                    created_at, updated_at)
                VALUES (%s, %s, %s, NULL, %s, %s, %s, 'Initiated', %s, NULL,
                        NULL, %s, %s)
            """, (payment_id, supplier_id, day, currency, total,
                  rng.choices(methods, weights=weights)[0],
                  f"BNK{payment_id[4:8]}{payment_id[9:]}", ordered_at, ordered_at))
            _count(report, "created", "payments")

            for invoice_id, _, amount in batch:
                cursor.execute("""
                    INSERT INTO payment_allocations (allocation_id, payment_id,
                        invoice_id, supplier_id, currency_code, allocated_amount,
                        allocation_status, cancelled_at, cancellation_reason,
                        replaced_by_allocation_id, created_at, updated_at)
                    VALUES (%s, %s, %s, %s, %s, %s, 'Active', NULL, NULL, NULL,
                            %s, %s)
                """, (_next_id(context, "ALC", day.year), payment_id, invoice_id,
                      supplier_id, currency, amount, ordered_at, ordered_at))
                _count(report, "created", "payment_allocations")
                touched.add(invoice_id)


# ---------------------------------------------------------------------------
# 9. Commission run
# ---------------------------------------------------------------------------

def _commission_run(cursor, day, rng, context, report):
    """The last business day of the month: this month's commissions."""
    start = date(day.year, day.month, 1)
    finish = month_end(day)

    advisors = [row for row in context["employees"]
                if row["employee_type"] == "Advisor"
                and row["is_commission_eligible"]
                and row["hire_date"] <= start
                and (row["departure_date"] is None
                     or row["departure_date"] >= finish)]

    for advisor in advisors:
        for kind, participation in COMMISSION_PARTICIPATION.items():
            if rng.random() >= participation:
                continue
            _emit_commission(cursor, rng, context, advisor, kind, "Monthly",
                             start, finish, day, report)

    # The annual bonus for the previous year is calculated at the end of
    # January, alongside the monthly run.
    if day.month == 1:
        year = day.year - 1
        period_start, period_end = date(year, 1, 1), date(year, 12, 31)
        for advisor in context["employees"]:
            if (advisor["employee_type"] != "Advisor"
                    or not advisor["is_commission_eligible"]
                    or advisor["hire_date"] > period_start):
                continue
            departure = advisor["departure_date"]
            if departure is not None and departure < period_end:
                continue
            if rng.random() < BONUS_PARTICIPATION:
                _emit_commission(cursor, rng, context, advisor,
                                 "Performance Bonus", "Annual",
                                 period_start, period_end, day, report)


def _emit_commission(cursor, rng, context, advisor, kind, period_type,
                     period_start, period_end, day, report):
    row = _build_one_commission(
        rng, _next_id(context, "COM", day.year), advisor, kind, period_type,
        period_start, period_end, day, context["validators"], context["fx"])

    # The run has only just happened, so nothing is validated yet. The
    # lifecycle is driven forward day by day by `_advance_commissions`, not
    # decided here - which is the difference between simulating and seeding.
    row.update({
        "commission_status": "Calculated", "validated_at": None,
        "validated_by_employee_ref": None, "paid_on": None,
        "payroll_reference": None, "cancellation_reason": None,
        "updated_at": row["calculated_at"],
    })

    columns = list(row)
    cursor.execute(
        f"INSERT INTO commissions ({', '.join(columns)}) "
        f"VALUES ({', '.join(['%s'] * len(columns))}) "
        f"ON CONFLICT (employee_ref, commission_type, period_start, period_end) "
        f"DO NOTHING",
        tuple(row[name] for name in columns))
    if cursor.rowcount:
        _count(report, "created", "commissions")
    else:
        context["sequences"]["COM"] -= 1


# ---------------------------------------------------------------------------
# 10. A mis-keyed allocation, found and corrected
# ---------------------------------------------------------------------------

def _miskey_allocation(cursor, day, rng, context, report, touched):
    """Cancel an allocation keyed with the wrong amount, and replace it.

    The only scenario `allocation_status` exists for: the transfer itself was
    fine, the matching was not. Nothing is deleted - the wrong row stays,
    cancelled, pointing at its replacement.
    """
    cursor.execute("""
        SELECT pa.allocation_id, pa.payment_id, pa.invoice_id, pa.supplier_id,
               pa.currency_code, pa.allocated_amount, pa.created_at
        FROM payment_allocations pa
        JOIN payments p ON p.payment_id = pa.payment_id
        WHERE pa.allocation_status = 'Active'
          AND p.payment_status = 'Executed'
          AND pa.created_at > %s
        ORDER BY random() LIMIT 1
    """, (_instant(day - timedelta(days=45), 0),))
    row = cursor.fetchone()
    if not row:
        return

    (allocation_id, payment_id, invoice_id, supplier_id, currency,
     amount, created_at) = row
    moment = _instant(day, 13, rng.randint(0, 59))
    replacement = _next_id(context, "ALC", day.year)

    # THREE STATEMENTS, AND THE ORDER IS FORCED FROM BOTH SIDES.
    #
    #   1. cancel the old row, WITHOUT naming its successor yet
    #   2. insert the replacement
    #   3. only now, point the cancelled row at it
    #
    # Step 1 must precede step 2: the partial unique index allows only one
    # Active allocation per (payment, invoice), so the new row cannot exist
    # while the old one is still active.
    #
    # Step 3 must follow step 2: `replaced_by_allocation_id` is a foreign key
    # onto this same table, so naming a row that does not exist yet is
    # rejected immediately. Doing steps 1 and 3 together - which is what this
    # function did first - fails for exactly that reason.
    cursor.execute("""
        UPDATE payment_allocations
        SET allocation_status = 'Cancelled', cancelled_at = %s,
            cancellation_reason = %s, updated_at = %s
        WHERE allocation_id = %s
    """, (moment, rng.choice(["Keying error", "Mis-matched amount",
                              "Wrong instalment split"]),
          moment, allocation_id))

    cursor.execute("""
        INSERT INTO payment_allocations (allocation_id, payment_id, invoice_id,
            supplier_id, currency_code, allocated_amount, allocation_status,
            cancelled_at, cancellation_reason, replaced_by_allocation_id,
            created_at, updated_at)
        VALUES (%s, %s, %s, %s, %s, %s, 'Active', NULL, NULL, NULL, %s, %s)
    """, (replacement, payment_id, invoice_id, supplier_id, currency, amount,
          moment, moment))

    cursor.execute("""
        UPDATE payment_allocations
        SET replaced_by_allocation_id = %s, updated_at = %s
        WHERE allocation_id = %s
    """, (replacement, moment, allocation_id))

    _count(report, "transitions", "allocation Active->Cancelled (remplacee)")
    _count(report, "created", "payment_allocations")
    touched.add(invoice_id)


# ---------------------------------------------------------------------------
# 11. Reference data
# ---------------------------------------------------------------------------

def _reference_data(cursor, day, rng, context, report):
    """Suppliers and employees barely move, but they do move.

    The odds are per business day and deliberately small: about three supplier
    deactivations and two departures a year, which is what a 180-supplier,
    160-person firm actually experiences. Over a 45-day run they may well not
    fire at all - that is the correct frequency, not a broken feature.
    """
    moment = _instant(day, 8, rng.randint(0, 59))

    if rng.random() < SUPPLIER_ONBOARDING_ODDS:
        _onboard_supplier(cursor, day, rng, moment, report)

    if rng.random() < EMPLOYEE_HIRE_ODDS:
        _hire_employee(cursor, day, rng, context, moment, report)

    if rng.random() < SUPPLIER_DEACTIVATION_ODDS:
        cursor.execute("""
            SELECT supplier_id FROM suppliers WHERE supplier_status = 'Active'
            ORDER BY random() LIMIT 1
        """)
        row = cursor.fetchone()
        if row:
            status = "Blocked" if rng.random() < 0.25 else "Inactive"
            cursor.execute("""
                UPDATE suppliers SET supplier_status = %s, deactivated_on = %s,
                       updated_at = %s
                WHERE supplier_id = %s
            """, (status, day, moment, row[0]))
            _count(report, "transitions", f"supplier Active->{status}")

    if rng.random() < EMPLOYEE_DEPARTURE_ODDS:
        cursor.execute("""
            SELECT employee_ref FROM employees
            WHERE employee_status = 'Active' AND hire_date < %s
            ORDER BY random() LIMIT 1
        """, (day - timedelta(days=400),))
        row = cursor.fetchone()
        if row:
            cursor.execute("""
                UPDATE employees SET employee_status = 'Inactive',
                       departure_date = %s, updated_at = %s
                WHERE employee_ref = %s
            """, (day, moment, row[0]))
            _count(report, "transitions", "employee Active->Inactive")


def _onboard_supplier(cursor, day, rng, moment, report):
    """A new supplier signs. It starts at the bottom of the Pareto curve.

    Nobody is onboarded as a top-ten supplier: the relationship has to build.
    Because `load_context` derives each supplier's billing rate from its own
    trailing twelve months, a newcomer naturally starts at the floor rate and
    climbs only if it actually invoices - the tier is emergent rather than
    assigned.
    """
    cursor.execute("SELECT MAX(supplier_id) FROM suppliers")
    highest = cursor.fetchone()[0]
    identifier = f"SUP-{int(highest.split('-')[1]) + 1:05d}"

    country = rng.choices(["FR", "CH", "IT", "BE", "DE", "LU"],
                          weights=[33, 26, 15, 13, 8, 5])[0]
    category = rng.choice([
        "IT & Software", "Professional Services", "Marketing", "Travel",
        "Telecom", "Office Supplies", "Training", "Insurance"])
    name = f"{rng.choice(['Nova', 'Veritas', 'Solstice', 'Meridien', 'Atlas', 'Orion'])} " \
           f"{rng.choice(['Advisory', 'Systems', 'Partners', 'Group', 'Services'])} " \
           f"{identifier[-3:]}"

    cursor.execute("""
        INSERT INTO suppliers (supplier_id, supplier_name, supplier_legal_name,
            supplier_category, country_code, vat_number, iban_masked,
            default_currency, payment_terms_days, supplier_status, onboarded_on,
            deactivated_on, created_at, updated_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, 'Active', %s, NULL, %s, %s)
    """, (identifier, name, f"{name} SA", category, country,
          None if country in ("GB", "US") else f"{country}{rng.randrange(10**10, 10**11)}",
          f"{country}{rng.randint(10, 99)}{'*' * 10}{rng.randint(1000, 9999)}",
          "CHF" if country == "CH" else "EUR",
          rng.choice([30, 30, 45, 60]), day, moment, moment))
    _count(report, "created", "suppliers")


def _hire_employee(cursor, day, rng, context, moment, report):
    """Someone joins. Support roles mostly - advisors are hired in cohorts."""
    from ..seed.people import FIRST_NAMES, LAST_NAMES

    cursor.execute("SELECT MAX(employee_ref) FROM employees")
    highest = cursor.fetchone()[0]
    reference = f"EMP-{int(highest.split('-')[1]) + 1:04d}"

    offices = [row["office_code"] for row in context["employees"]
               if row["office_code"]]
    office = rng.choice(offices)
    country = next(row["country_code"] for row in context["employees"]
                   if row["office_code"] == office)
    cost_centre = next(row["cost_center_id"] for row in context["employees"]
                       if row["office_code"] == office)

    employee_type = rng.choices(
        ["Back Office", "Advisor", "IT", "Support", "Compliance"],
        weights=[34, 30, 14, 12, 10])[0]
    first = rng.choice(FIRST_NAMES[country])
    last = rng.choice(LAST_NAMES[country])

    # A new hire is not on a commission plan from day one, and only advisors
    # ever are - the schema enforces the second half of that.
    eligible = employee_type == "Advisor" and rng.random() < 0.55

    # The address carries the employee number so it cannot collide with an
    # existing one - `work_email` is UNIQUE, and a rare event is the worst
    # place to discover a duplicate. Roughly one hire in twenty arrives with
    # no address on file, as in the seed.
    from ..seed.organisation import _ascii
    email = None if rng.random() < 0.05 else \
        f"{_ascii(first)}.{_ascii(last)}.{reference[4:]}@orialis.com"

    cursor.execute("""
        INSERT INTO employees (employee_ref, first_name, last_name, work_email,
            employee_type, cost_center_id, office_code, country_code, hire_date,
            departure_date, is_commission_eligible, employee_status,
            created_at, updated_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, NULL, %s, 'Active', %s, %s)
        ON CONFLICT (employee_ref) DO NOTHING
    """, (reference, first, last, email, employee_type, cost_centre,
          office, country, day, eligible, moment, moment))
    if cursor.rowcount:
        _count(report, "created", "employees")


# ---------------------------------------------------------------------------
# 12. Settlement
# ---------------------------------------------------------------------------

def _recompute_settlement(cursor, day, invoice_ids, report):
    """THE rule, applied to every invoice the day touched.

    Identical to the seed's `settle()`, expressed in SQL because the rows are
    already in the database. Nothing consults what any earlier step intended:
    a payment that failed this morning simply stops appearing in the sum.
    """
    if not invoice_ids:
        return

    moment = _instant(day, 23, 30)
    cursor.execute("""
        WITH computed AS (
            SELECT i.invoice_id,
                   i.gross_amount,
                   COALESCE(SUM(pa.allocated_amount) FILTER (
                       WHERE pa.allocation_status = 'Active'
                         AND p.payment_status = 'Executed'), 0) AS total
            FROM supplier_invoices i
            LEFT JOIN payment_allocations pa ON pa.invoice_id = i.invoice_id
            LEFT JOIN payments p ON p.payment_id = pa.payment_id
            WHERE i.invoice_id = ANY(%s)
            GROUP BY i.invoice_id, i.gross_amount
        )
        UPDATE supplier_invoices i
        SET paid_amount = c.total,
            invoice_payment_status = CASE
                WHEN c.total = 0 THEN 'Unpaid'
                WHEN c.total = c.gross_amount THEN 'Paid'
                ELSE 'Partially Paid' END,
            updated_at = %s
        FROM computed c
        WHERE i.invoice_id = c.invoice_id
          AND (i.paid_amount <> c.total
               OR i.invoice_payment_status <> CASE
                    WHEN c.total = 0 THEN 'Unpaid'
                    WHEN c.total = c.gross_amount THEN 'Paid'
                    ELSE 'Partially Paid' END)
    """, (list(invoice_ids), moment))

    if cursor.rowcount:
        _count(report, "updated", "supplier_invoices (reglement)", cursor.rowcount)

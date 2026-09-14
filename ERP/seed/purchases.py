"""
The purchase-to-pay history: invoices, payments and their matching.

The three tables are generated together because they are one story told in
three places. An invoice is received, approved, scheduled into a payment run,
paid - possibly in two instalments, possibly alongside other invoices of the
same supplier, possibly by a transfer that fails and has to be reissued. Any
attempt to generate the three tables independently produces rows that pass
every constraint and describe nothing.

---------------------------------------------------------------------------
THE ORDER OF EVENTS, WHICH IS ALSO THE ORDER OF THIS FILE
---------------------------------------------------------------------------

    1. invoices        driven by supplier tier x month x seasonality
    2. approval        a decision, taken days after receipt
    3. payment runs    Tuesdays and Fridays, grouped by supplier
    4. payment fate    executed, failed (and usually reissued), still in flight
    5. corrections     ~120 mis-keyed allocations, cancelled and replaced
    6. paid_amount     RECOMPUTED from the allocations, never assumed

Step 6 is the one that matters. `paid_amount` is never decided up front and
written onto the invoice: it is summed at the end, from the Active allocations
of Executed payments only, exactly as the specification requires. That is why
a failed transfer leaves its invoice unpaid without any special case - the sum
simply does not see it.
"""

from datetime import date, datetime, timedelta, timezone

from .reference import (COUNTRY_OF_COST_CENTRE, FUNCTION_COST_CENTRES,
                        OFFICE_TO_COST_CENTRE)
from .toolkit import (INVOICE_SEASONALITY, PERIOD_END, PERIOD_START, TODAY,
                      add_business_days, business_days, is_business_day,
                      labels_for, lognormal, money, month_end,
                      months_in_period, new_rng, next_business_day, poisson,
                      split_exactly)

# ---------------------------------------------------------------------------
# Business parameters
# ---------------------------------------------------------------------------

# Gross amounts: median 1 850, 90th percentile 12 000, capped at 180 000.
INVOICE_MEDIAN, INVOICE_P90, INVOICE_MAX = 1850.0, 12000.0, 180000.0

CREDIT_NOTE_SHARE = 0.03

# Domestic VAT rates. Switzerland's 8.1% is the rate in force since 2024.
VAT_RATE = {"FR": 20.0, "BE": 21.0, "IT": 22.0, "CH": 8.1}

# A few categories are VAT-exempt even domestically (insurance, some
# financial services).
EXEMPT_CATEGORIES = ("Insurance",)

APPROVAL_MIX = {
    "Approved": 90.0, "Pending Approval": 4.0, "Draft": 3.0,
    "Rejected": 2.5, "Cancelled": 0.5,
}

# Of every 1 000 invoices, how many are settled, part-settled, or left
# outstanding. These are INTENTIONS: the final invoice_payment_status is
# recomputed from the allocations, so a failed transfer can still leave an
# invoice the business meant to pay sitting at Unpaid.
SETTLEMENT_MIX = {"full": 938, "partial": 54, "none": 8}

# How late the firm actually pays, relative to the due date.
LATENESS_MIX = {"on_time": 60, "late_short": 25, "late_medium": 10, "late_long": 5}

PAYMENT_STATUS_MIX = {
    "Executed": 96.4, "Failed": 1.5, "Cancelled": 1.0, "Initiated": 1.1,
}

# A failed or cancelled transfer does not make the debt disappear: four times
# out of five it is reissued a few days later, and the second attempt works.
REISSUE_PROBABILITY = 0.90

# One paid invoice in ten is settled in two instalments.
TWO_INSTALMENT_SHARE = 0.10

# When several invoices of one supplier fall on the same run, how often they
# travel as a single batched transfer rather than one transfer each.
BATCH_PROBABILITY = 0.45

PAYMENT_METHOD_MIX = {
    "SEPA Credit Transfer": 84, "SWIFT": 9, "Direct Debit": 4,
    "Card": 2, "Cheque": 1,
}

# Allocations mis-keyed by the accounts-payable team, later corrected. The
# specification asks for about 120.
MISKEYED_ALLOCATIONS = 120

# Payment runs happen twice a week. This is why created_at values arrive in
# bursts rather than evenly, which is exactly the behaviour a future
# incremental extraction will have to cope with.
PAYMENT_RUN_WEEKDAYS = (1, 4)  # Tuesday, Friday

EU_COUNTRIES = ("FR", "BE", "IT", "LU", "DE")

# How likely a supplier is to bill the entity in its OWN country, by category.
#
# This is what drives the VAT mix, and it is a real distinction rather than a
# knob. A landlord invoices the building's own entity - there is no other
# option. A market-data vendor or a software publisher signs one group-wide
# contract and bills whichever entity the licence is allocated to, which is
# what produces intra-EU reverse charge and out-of-scope imports.
#
# Assuming a single probability for every category put 69% of the ledger on
# domestic VAT and only 12% on reverse charge; the split by category is what
# brings it back towards the stated mix.
LOCAL_BILLING = {
    "Real Estate & Facilities": 0.97,
    "Office Supplies": 0.95,
    "Travel": 0.92,
    "Telecom": 0.90,
    "Training": 0.85,
    "Insurance": 0.80,
    "Professional Services": 0.78,
    "Marketing": 0.75,
    "IT & Software": 0.30,
    "Market Data": 0.20,
}


def _instant(day, hour, minute=0):
    return datetime(day.year, day.month, day.day, hour, minute,
                    tzinfo=timezone.utc)


# ===========================================================================
# 1. Invoices
# ===========================================================================

def build_invoices(suppliers, cost_centres, fx_by_month, approvers):
    """~8 000 invoices, as the consequence of supplier tiers and seasonality."""
    rng = new_rng("invoices")
    months = months_in_period()

    # Chargeable cost centres: branches and functions. The group and country
    # levels are aggregation nodes, never the target of a charge.
    chargeable = [row["cost_center_id"] for row in cost_centres
                  if row["cost_center_type"] in ("Branch", "Function")]
    by_country = {}
    for identifier in chargeable:
        by_country.setdefault(COUNTRY_OF_COST_CENTRE[identifier], []).append(identifier)

    drafts = []
    for month in months:
        finish = month_end(month)
        season = INVOICE_SEASONALITY[month.month]

        for supplier in suppliers:
            # A supplier bills only while it is a supplier. Onboarding and
            # deactivation are hard walls, not probabilities - this is the
            # chronology rule the specification names explicitly.
            if supplier["onboarded_on"] > finish:
                continue
            deactivated = supplier["deactivated_on"]
            if deactivated is not None and deactivated < month:
                continue

            count = poisson(rng, supplier["_monthly_rate"] * season)
            if not count:
                continue

            window_start = max(month, supplier["onboarded_on"], PERIOD_START)
            window_end = min(finish, deactivated or finish, PERIOD_END)
            if window_start > window_end:
                continue

            country = supplier["country_code"]
            days = business_days(window_start, window_end,
                                 country if country in VAT_RATE else "FR")
            if not days:
                continue

            for _ in range(count):
                drafts.append((rng.choice(days), supplier))

    # Oldest first: invoice ids are allocated in chronological order, the way
    # a sequence in a real system would.
    drafts.sort(key=lambda item: (item[0], item[1]["supplier_id"]))

    invoices = []
    sequence = {}
    supplier_ordinal = {}
    for invoice_date, supplier in drafts:
        year = invoice_date.year
        sequence[year] = sequence.get(year, 0) + 1
        key = supplier["supplier_id"]
        supplier_ordinal[key] = supplier_ordinal.get(key, 0) + 1
        invoices.append(_build_one_invoice(
            rng, f"INV-{year}-{sequence[year]:05d}", invoice_date, supplier,
            by_country, chargeable, fx_by_month, supplier_ordinal[key]))

    _assign_approval(rng, invoices, approvers)
    return invoices


def _build_one_invoice(rng, invoice_id, invoice_date, supplier, by_country,
                       chargeable, fx_by_month, supplier_ordinal):
    supplier_country = supplier["country_code"]

    # Whether a supplier bills its own country depends on WHAT it sells, not
    # on a single global probability - see LOCAL_BILLING above. This is what
    # makes the VAT treatment follow from the business relationship rather
    # than from a lottery.
    local_odds = LOCAL_BILLING.get(supplier["supplier_category"], 0.85)
    if supplier_country in by_country and rng.random() < local_odds:
        cost_centre = rng.choice(by_country[supplier_country])
    else:
        cost_centre = rng.choice(chargeable)
    centre_country = COUNTRY_OF_COST_CENTRE[cost_centre]

    treatment, rate = _vat_for(rng, supplier, centre_country)

    currency = supplier["default_currency"]
    is_credit_note = rng.random() < CREDIT_NOTE_SHARE
    gross_target = lognormal(rng, INVOICE_MEDIAN, INVOICE_P90, INVOICE_MAX)

    net = money(gross_target / (1 + rate / 100))
    tax = money(gross_target - net)
    gross = money(net + tax)

    if is_credit_note:
        net, tax, gross = -net, -tax, -gross

    fx = 1.0 if currency == "EUR" else fx_by_month[(invoice_date.year,
                                                    invoice_date.month)]
    received = add_business_days(invoice_date, rng.randint(0, 5),
                                 centre_country)
    received = min(received, PERIOD_END)
    due = invoice_date + timedelta(days=supplier["payment_terms_days"])

    # ONE instant, reused. Drawing the minute twice would let updated_at land
    # before created_at - which the schema refuses, and rightly: a row cannot
    # have been modified before it existed.
    recorded = _instant(received, 9, rng.randint(0, 59))

    return {
        "invoice_id": invoice_id,
        "supplier_id": supplier["supplier_id"],
        "supplier_invoice_number": _supplier_number(supplier, invoice_date,
                                                    supplier_ordinal),
        "cost_center_id": cost_centre,
        "invoice_type": "Credit Note" if is_credit_note else "Standard",
        "invoice_date": invoice_date,
        "received_date": received,
        "due_date": due,
        "currency_code": currency,
        "net_amount": net,
        "tax_treatment": treatment,
        "tax_rate": rate,
        "tax_amount": tax,
        "gross_amount": gross,
        "fx_rate_to_eur": fx,
        "gross_amount_eur": money(gross * fx),
        "paid_amount": 0.0,                     # recomputed in step 6
        "invoice_approval_status": "Approved",  # assigned just below
        "invoice_payment_status": "Unpaid",     # recomputed in step 6
        "approved_by_employee_ref": None,
        "approved_at": None,
        "rejection_reason": None,
        "created_at": recorded,
        "updated_at": recorded,
        "_country": centre_country,
    }


def _vat_for(rng, supplier, centre_country):
    """VAT treatment, derived from geography rather than drawn from a table.

    Zero-rated is not one situation but three, and which one applies is a
    consequence of where the supplier and the charged entity sit:

        same country            domestic VAT at the local rate
        both inside the EU      reverse charge - VAT owed by Orialis
        either side outside     out of scope (Switzerland, the UK, the US)

    A handful of domestic categories are exempt outright: insurance is not a
    taxable supply.
    """
    supplier_country = supplier["country_code"]

    if supplier_country == centre_country:
        if supplier["supplier_category"] in EXEMPT_CATEGORIES or rng.random() < 0.035:
            return "Exempt", 0.0
        return "Standard", VAT_RATE[centre_country]

    if supplier_country in EU_COUNTRIES and centre_country in EU_COUNTRIES:
        return "Reverse Charge", 0.0

    return "Out of Scope", 0.0


def _supplier_number(supplier, invoice_date, ordinal):
    """The number the SUPPLIER put on its own invoice - not ours.

    SEQUENTIAL, per supplier, because that is how invoice numbering works: a
    supplier's ledger runs 1, 2, 3, and never reuses a number. The first
    version of this function drew a random number instead and duplicated one,
    which the anti-duplicate-booking constraint caught immediately. The
    constraint was right twice over: the data was wrong, and so was the model
    of how suppliers number their paper.

    Different suppliers use different formats, which is exactly why this is
    not our identifier and why the schema stores both.
    """
    style = ord(supplier["supplier_id"][-1]) % 3
    if style == 0:
        return f"{invoice_date.year}-{ordinal:04d}"
    if style == 1:
        return f"F{invoice_date.strftime('%y')}{ordinal:05d}"
    return f"{supplier['supplier_id'][-4:]}/{ordinal:05d}"


def _pick_approver(rng, approvers, country, on_day):
    """Whoever signed the invoice off, employed on the day they signed it.

    The schema ties `approved_at` and `approved_by_employee_ref` together:
    one cannot exist without the other. That constraint caught the first
    version of this generator, which stamped an approval instant and left the
    approver null - a reminder that the value of a constraint is measured in
    the bugs it refuses, not in the rows it lets through.
    """
    for key in (country, "*"):
        candidates = [row for row in approvers.get(key, [])
                      if row["hire_date"] <= on_day
                      and (row["departure_date"] is None
                           or row["departure_date"] >= on_day)]
        if candidates:
            return rng.choice(candidates)["employee_ref"]
    return None


def _assign_approval(rng, invoices, approvers):
    """Approval states, respecting what can still be undecided today.

    A 2024 invoice cannot still be "Pending Approval" in September 2026 - the
    backlog would have been cleared long ago. So the undecided states are
    confined to the recent end of the ledger:

        Pending Approval   only in the last 120 days, weighted to the newest
        Draft              anywhere - an abandoned capture stays a draft for
                           ever, and that is a real state in every ERP
        Approved / Rejected / Cancelled   anywhere

    Without this the distribution would be right and the history absurd.
    """
    total = len(invoices)
    counts = split_exactly(total, APPROVAL_MIX)

    order = sorted(range(total), key=lambda i: invoices[i]["received_date"])
    recent_cutoff = TODAY - timedelta(days=120)
    recent = [i for i in order if invoices[i]["received_date"] >= recent_cutoff]

    assigned = {}

    # Pending Approval: newest first, so the backlog is genuinely a backlog.
    pending = counts["Pending Approval"]
    for index in reversed(recent):
        if pending == 0:
            break
        assigned[index] = "Pending Approval"
        pending -= 1

    remaining = [i for i in order if i not in assigned]
    rng.shuffle(remaining)

    for label in ("Draft", "Rejected", "Cancelled", "Approved"):
        take = counts[label] if label != "Pending Approval" else 0
        if label == "Approved":
            take = len(remaining)          # whatever is left
        for _ in range(min(take, len(remaining))):
            assigned[remaining.pop()] = label

    for index, invoice in enumerate(invoices):
        status = assigned.get(index, "Approved")
        invoice["invoice_approval_status"] = status

        if status == "Approved":
            decided = add_business_days(invoice["received_date"],
                                        rng.randint(1, 9), invoice["_country"])
            decided = min(decided, TODAY)
            approver = _pick_approver(rng, approvers, invoice["_country"], decided)
            if approver is None:
                # Nobody was in post to sign it: it is still awaiting
                # approval, which is the truthful state rather than an
                # approval signed by no one.
                invoice["invoice_approval_status"] = "Pending Approval"
                continue
            invoice["approved_by_employee_ref"] = approver
            invoice["approved_at"] = _instant(decided, 14, rng.randint(0, 59))
            invoice["updated_at"] = invoice["approved_at"]
            invoice["_approved_on"] = decided
        elif status == "Rejected":
            decided = add_business_days(invoice["received_date"],
                                        rng.randint(2, 15), invoice["_country"])
            decided = min(decided, TODAY)
            invoice["rejection_reason"] = rng.choice([
                "Disputed amount", "Goods not received", "Duplicate billing",
                "Missing purchase order", "Incorrect VAT treatment",
            ])
            invoice["updated_at"] = _instant(decided, 14, rng.randint(0, 59))


# ===========================================================================
# 2-5. Payments, allocations, corrections
# ===========================================================================

def build_payments(invoices, suppliers, rng=None):
    """Payment runs, allocations, failures and reissues.

    Returns (payments, allocations). `invoices` is mutated only at the end,
    by `settle()`.
    """
    rng = rng or new_rng("payments")
    supplier_by_id = {row["supplier_id"]: row for row in suppliers}

    # -- which invoices the business intends to settle ----------------------
    # Only approved ones are payable: the schema refuses anything else, and so
    # does the business.
    payable = [inv for inv in invoices
               if inv["invoice_approval_status"] == "Approved"
               and inv["invoice_type"] == "Standard"]
    credit_notes = [inv for inv in invoices
                    if inv["invoice_approval_status"] == "Approved"
                    and inv["invoice_type"] == "Credit Note"]

    intents = labels_for(len(payable), SETTLEMENT_MIX, rng)

    # An invoice that is not yet due cannot have been paid. Force the most
    # recent ones to "none" and give their slot to an older invoice.
    for position, invoice in enumerate(payable):
        if invoice["due_date"] > TODAY and intents[position] != "none":
            for other in range(len(payable)):
                if intents[other] == "none" and payable[other]["due_date"] <= TODAY:
                    intents[position], intents[other] = "none", intents[position]
                    break
            else:
                intents[position] = "none"

    # -- schedule each settlement into a payment run ------------------------
    lateness = labels_for(len(payable), LATENESS_MIX, rng)
    scheduled = {}       # (supplier_id, run_date) -> [(invoice, amount)]

    for position, invoice in enumerate(payable):
        intent = intents[position]
        if intent == "none":
            continue

        run = _run_date_for(rng, invoice, lateness[position])
        if run is None:
            continue

        # A BLOCKED supplier has its payments frozen - that is the whole
        # difference between Blocked and Inactive. An inactive supplier is
        # still paid what it is owed; a blocked one is not paid at all while
        # the dispute lasts. The rule is temporal: transfers made BEFORE the
        # block are legitimate and stay.
        supplier = supplier_by_id[invoice["supplier_id"]]
        if (supplier["supplier_status"] == "Blocked"
                and supplier["deactivated_on"] is not None
                and run >= supplier["deactivated_on"]):
            continue

        gross = invoice["gross_amount"]
        if intent == "partial":
            share = round(rng.uniform(0.25, 0.75), 2)
            _schedule(scheduled, invoice, money(gross * share), run)
        elif rng.random() < TWO_INSTALMENT_SHARE:
            first = money(gross * round(rng.uniform(0.3, 0.6), 2))
            second = money(gross - first)
            _schedule(scheduled, invoice, first, run)
            later = _next_run(run + timedelta(days=rng.randint(7, 45)))
            if later <= TODAY:
                _schedule(scheduled, invoice, second, later)
            # If the second instalment would fall after today it simply has
            # not happened yet: the invoice stays Partially Paid, which is a
            # perfectly ordinary state and not a defect.
        else:
            _schedule(scheduled, invoice, gross, run)

    # -- credit notes ride along on a run of the same supplier --------------
    # In practice an avoir is netted off the next transfer to that supplier,
    # which is why a payment can carry a negative line. The group total must
    # stay positive: a transfer of a negative amount does not exist.
    for note in credit_notes:
        if rng.random() < 0.75:
            # The run must come after the credit note was APPROVED, not merely
            # after it was issued. Filtering on invoice_date let 58
            # allocations settle invoices that had not been signed off yet -
            # caught by the pre-commit chronology check.
            available_from = note.get("_approved_on") or note["invoice_date"]
            candidates = [key for key in scheduled
                          if key[0] == note["supplier_id"]
                          and key[1] >= available_from]
            if candidates:
                key = min(candidates, key=lambda item: item[1])
                total = sum(amount for _, amount in scheduled[key])
                if total + note["gross_amount"] > 0:
                    scheduled[key].append((note, note["gross_amount"]))

    # -- turn each (supplier, run) group into one payment -------------------
    payments, allocations = [], []
    payment_sequence, allocation_sequence = {}, {}

    # A supplier with several invoices falling on the same run is NOT always
    # paid in one transfer. Most accounts-payable teams issue one transfer per
    # invoice - it is what makes the supplier's own reconciliation possible -
    # and batch only when the run is large or the supplier asks for it.
    #
    # So a multi-line group is batched only `BATCH_PROBABILITY` of the time;
    # otherwise it becomes one payment per line. This is what sets the
    # allocations-per-payment ratio, and with it the ~7 000 payments against
    # ~8 000 allocations the specification predicts.
    ordered_groups = []
    for key, lines in sorted(scheduled.items(),
                             key=lambda item: (item[0][1], item[0][0])):
        total = sum(amount for _, amount in lines)
        if total <= 0:
            # A transfer of zero or less does not exist. This can only happen
            # if a credit note outweighs everything it was netted against.
            continue

        # A group carrying a credit note is NEVER split. The avoir is a
        # negative line, and it only has meaning alongside the positive ones
        # it is deducted from - split apart, it would become a transfer of a
        # negative amount, which the schema rightly refuses.
        has_credit_note = any(amount < 0 for _, amount in lines)

        if len(lines) == 1 or has_credit_note or rng.random() < BATCH_PROBABILITY:
            ordered_groups.append((key, lines))
        else:
            for line in lines:
                ordered_groups.append((key, [line]))

    statuses = _payment_statuses(rng, len(ordered_groups))
    methods = labels_for(len(ordered_groups), PAYMENT_METHOD_MIX, rng)

    for position, ((supplier_id, run_date), lines) in enumerate(ordered_groups):
        status = statuses[position]
        payment = _make_payment(rng, payment_sequence, supplier_by_id[supplier_id],
                                run_date, lines, status, methods[position])
        payments.append(payment)
        for invoice, amount in lines:
            allocations.append(_make_allocation(
                rng, allocation_sequence, payment, invoice, amount))

        # A failed or cancelled transfer is normally reissued: the debt did
        # not go away. This is what keeps the settled share near its target
        # WITHOUT the generator ever counting settled invoices.
        if status in ("Failed", "Cancelled") and rng.random() < REISSUE_PROBABILITY:
            retry_date = _next_run(run_date + timedelta(days=rng.randint(3, 12)))
            if retry_date <= TODAY:
                retry = _make_payment(rng, payment_sequence,
                                      supplier_by_id[supplier_id], retry_date,
                                      lines, "Executed",
                                      methods[position])
                payments.append(retry)
                for invoice, amount in lines:
                    allocations.append(_make_allocation(
                        rng, allocation_sequence, retry, invoice, amount))

    _miskey_and_correct(rng, payments, allocations, allocation_sequence)
    return payments, allocations


def _payment_statuses(rng, total):
    """Payment fates, with `Initiated` reserved for the newest runs.

    `ordered_groups` is sorted by run date, so the last entries are the most
    recent payments - and "still in flight" is the one status that is a
    statement about NOW rather than about the payment. A transfer ordered in
    2024 is not still initiated; it settled or it failed.

    The first version drew the status at random and then downgraded old
    `Initiated` rows to `Executed`, which produced zero in-flight payments in
    the whole dataset. Reserving the tail is what makes the 1.1% real.
    """
    counts = split_exactly(total, PAYMENT_STATUS_MIX)
    statuses = [None] * total

    for index in range(total - counts["Initiated"], total):
        statuses[index] = "Initiated"

    pool = []
    for label in ("Executed", "Failed", "Cancelled"):
        pool.extend([label] * counts[label])
    rng.shuffle(pool)

    for index in range(total):
        if statuses[index] is None:
            statuses[index] = pool.pop()
    return statuses


def _schedule(scheduled, invoice, amount, run_date):
    scheduled.setdefault((invoice["supplier_id"], run_date), []).append(
        (invoice, amount))


def _run_date_for(rng, invoice, lateness):
    """When this invoice actually gets paid, snapped to a payment run.

    The firm pays on Tuesdays and Fridays. An invoice due on a Wednesday is
    therefore paid on the Friday - which is itself a small, realistic source
    of lateness, on top of the deliberate distribution.
    """
    due = invoice["due_date"]
    if lateness == "on_time":
        # Snapping forward to the next Tuesday/Friday run can push a payment
        # past its due date. Aiming a few days early is what makes "on time"
        # actually arrive on time once the run calendar is applied.
        target = due - timedelta(days=rng.randint(7, 16))
    elif lateness == "late_short":
        target = due + timedelta(days=rng.randint(1, 15))
    elif lateness == "late_medium":
        target = due + timedelta(days=rng.randint(16, 45))
    else:
        target = due + timedelta(days=rng.randint(46, 120))

    approved = invoice.get("_approved_on")
    if approved is not None and target < approved:
        target = approved            # never paid before it was approved

    run = _next_run(target)
    return run if run <= TODAY else None


def _next_run(day):
    """The next Tuesday or Friday on or after `day`."""
    for _ in range(10):
        if day.weekday() in PAYMENT_RUN_WEEKDAYS and is_business_day(day, "FR"):
            return day
        day += timedelta(days=1)
    return day


def _make_payment(rng, sequence, supplier, run_date, lines, status, method):
    year = run_date.year
    sequence[year] = sequence.get(year, 0) + 1
    amount = money(sum(amount for _, amount in lines))

    executed_at = None
    failure_reason = None
    if status == "Executed":
        settled = run_date + timedelta(days=rng.randint(1, 3))
        executed_at = _instant(min(settled, TODAY), 11, rng.randint(0, 59))
    elif status == "Failed":
        failure_reason = rng.choice([
            "Invalid IBAN", "Account closed", "Insufficient funds",
            "Rejected by beneficiary bank", "Payment recalled",
        ])

    ordered_at = _instant(run_date, 10, rng.randint(0, 59))

    return {
        "payment_id": f"PAY-{year}-{sequence[year]:05d}",
        "supplier_id": supplier["supplier_id"],
        "payment_date": run_date,
        "value_date": (run_date + timedelta(days=rng.randint(1, 3))
                       if status == "Executed" else None),
        "currency_code": supplier["default_currency"],
        "payment_amount": amount,
        "payment_method": method,
        "payment_status": status,
        "bank_reference": f"BNK{year}{sequence[year]:06d}",
        "executed_at": executed_at,
        "failure_reason": failure_reason,
        "created_at": ordered_at,
        "updated_at": executed_at or ordered_at,
    }


def _make_allocation(rng, sequence, payment, invoice, amount, status="Active"):
    year = payment["payment_date"].year
    sequence[year] = sequence.get(year, 0) + 1
    created = payment["created_at"]
    return {
        "allocation_id": f"ALC-{year}-{sequence[year]:05d}",
        "payment_id": payment["payment_id"],
        "invoice_id": invoice["invoice_id"],
        "supplier_id": invoice["supplier_id"],
        "currency_code": invoice["currency_code"],
        "allocated_amount": money(amount),
        "allocation_status": status,
        "cancelled_at": None,
        "cancellation_reason": None,
        "replaced_by_allocation_id": None,
        "created_at": created,
        "updated_at": created,
    }


def _miskey_and_correct(rng, payments, allocations, sequence):
    """~120 allocations keyed with the wrong amount, then corrected.

    This is the ONLY reason `allocation_status` exists, and the scenario is
    deliberately narrow: the payment went out correctly, but the accounts-
    payable team typed the wrong amount when matching it. The wrong row is
    cancelled, a correct one is created, and the cancelled row points forward
    to its replacement.

    Note what is NOT done here: nothing is cancelled because a payment failed.
    A failed payment needs no write at all - the settlement sum filters on
    payment_status, so its invoices fall back to Unpaid on their own.
    """
    executed = {payment["payment_id"] for payment in payments
                if payment["payment_status"] == "Executed"}
    candidates = [allocation for allocation in allocations
                  if allocation["payment_id"] in executed]
    if not candidates:
        return

    chosen = rng.sample(candidates, min(MISKEYED_ALLOCATIONS, len(candidates)))
    for correct in chosen:
        year = correct["created_at"].year
        sequence[year] = sequence.get(year, 0) + 1

        wrong_amount = money(correct["allocated_amount"]
                             * rng.choice([0.1, 10.0, 0.5, 2.0]))
        discovered = correct["created_at"] + timedelta(days=rng.randint(1, 20))

        mistake = {
            "allocation_id": f"ALC-{year}-{sequence[year]:05d}",
            "payment_id": correct["payment_id"],
            "invoice_id": correct["invoice_id"],
            "supplier_id": correct["supplier_id"],
            "currency_code": correct["currency_code"],
            "allocated_amount": wrong_amount,
            "allocation_status": "Cancelled",
            "cancelled_at": discovered,
            "cancellation_reason": rng.choice([
                "Keying error", "Mis-matched amount", "Wrong instalment split",
            ]),
            "replaced_by_allocation_id": correct["allocation_id"],
            "created_at": correct["created_at"],
            "updated_at": discovered,
        }
        allocations.append(mistake)

        # The correction is recorded when the mistake was found, not when the
        # payment was made.
        correct["created_at"] = discovered
        correct["updated_at"] = discovered


# ===========================================================================
# 6. Settlement - the rule the specification insists on
# ===========================================================================

def settle(invoices, payments, allocations):
    """Recompute paid_amount and invoice_payment_status from the allocations.

    THE rule, and the only place it is applied:

        paid_amount = SUM(allocated_amount)
                      WHERE allocation_status = 'Active'
                        AND payments.payment_status = 'Executed'

    Nothing here consults what the generator INTENDED. A transfer that failed,
    was cancelled, or is still in flight contributes nothing, and its invoice
    comes out Unpaid without a single special case.
    """
    executed = {payment["payment_id"] for payment in payments
                if payment["payment_status"] == "Executed"}

    totals = {}
    for allocation in allocations:
        if allocation["allocation_status"] != "Active":
            continue
        if allocation["payment_id"] not in executed:
            continue
        totals[allocation["invoice_id"]] = money(
            totals.get(allocation["invoice_id"], 0.0)
            + allocation["allocated_amount"])

    for invoice in invoices:
        paid = money(totals.get(invoice["invoice_id"], 0.0))
        gross = invoice["gross_amount"]

        # Guard against a rounding drift making paid exceed gross by a cent.
        if gross > 0:
            paid = min(paid, gross)
        else:
            paid = max(paid, gross)

        invoice["paid_amount"] = paid
        if paid == 0:
            invoice["invoice_payment_status"] = "Unpaid"
        elif paid == gross:
            invoice["invoice_payment_status"] = "Paid"
        else:
            invoice["invoice_payment_status"] = "Partially Paid"

    # updated_at follows the last thing that happened to the invoice.
    last_touch = {}
    for allocation in allocations:
        if allocation["payment_id"] not in executed:
            continue
        key = allocation["invoice_id"]
        moment = allocation["updated_at"]
        if key not in last_touch or moment > last_touch[key]:
            last_touch[key] = moment
    for invoice in invoices:
        moment = last_touch.get(invoice["invoice_id"])
        if moment and moment > invoice["updated_at"]:
            invoice["updated_at"] = moment

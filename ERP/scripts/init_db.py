"""
Initialise the Orialis ERP schema in PostgreSQL.

Creates (if missing) the 9 ERP tables:

    cost_centers ---+--> employees ---+--> expenses
                    |                 +--> commissions
                    |
                    +--> supplier_invoices <--+
                                              |
    suppliers ------+--> supplier_invoices    +-- payment_allocations
                    |                         |
                    +--> payments ------------+

    fx_rates   (standalone reference table, no foreign key in or out)

The whole technical model is in English: table names, column names, business
values and documentation.

The script is idempotent: `CREATE TABLE IF NOT EXISTS` and
`CREATE INDEX IF NOT EXISTS` mean it can be re-run as many times as needed
without recreating or erasing anything. It creates NO data.

Connection: ERP_DATABASE_URL, read from the `.env` file at the repository
root. That value is never printed. The ERP has its OWN database - the CRM
variable DATABASE_URL is never read here, and no foreign key, import or
connection reaches the CRM.

Usage:
    python ERP/scripts/init_db.py
"""

import sys
from pathlib import Path

import psycopg

# ERP-only configuration: .env loading, ERP_DATABASE_URL retrieval and
# validation, credential scrubbing. It lives at ERP/config.py and is a
# deliberate copy of the CRM one rather than an import - see the module
# docstring there for why.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
from ERP.config import get_database_url, scrub  # noqa: E402


# ===========================================================================
# 1. The two rules the whole schema follows
# ===========================================================================
#
# RULE 1 - THE CONSTRAINT DOCTRINE
#
#   What can be verified on ONE ROW is declared to PostgreSQL.
#   What requires a SUM ACROSS ROWS is checked by the application before
#   COMMIT.
#
#   PostgreSQL cannot express "the sum of the allocations does not exceed the
#   invoice total" in a CHECK: that is a multi-row invariant. It can express
#   "the allocation currency equals the invoice currency", because a composite
#   foreign key turns that into a per-row fact. Everything decidable per row
#   is therefore declared here, and only the genuinely aggregate invariants
#   are left to the application.
#
# RULE 2 - THE ERP NEVER DELETES
#
#   Every foreign key below is ON DELETE RESTRICT. There is not one CASCADE in
#   this schema. Cancellation is modelled as a STATE, never as a missing row:
#   `allocation_status = 'Cancelled'`, `supplier_status = 'Blocked'`,
#   `invoice_approval_status = 'Cancelled'`.
#
#   This is what lets incremental extraction work later. The CRM documents the
#   opposite as a known limitation ("no deletion tracking, so incremental
#   extraction cannot detect a removed row"); the ERP does not repeat it,
#   because a cancellation is a visible UPDATE rather than a silent
#   disappearance.
#
#
# Temporal types follow the MEANING of each column, as in the CRM:
#
#   DATE        a calendar day, with no time of day
#               -> invoice_date, due_date, hire_date, period_start
#
#   TIMESTAMPTZ an instant in time, stored in UTC
#               -> every created_at / updated_at, approved_at, executed_at
#
# TIMESTAMPTZ rather than TIMESTAMP: the ERP spans four countries and three
# time zones. A timestamp without a zone would make any "per day" aggregation
# wrong at the day boundaries.
#
# Money is NUMERIC(14,2), never FLOAT. A binary floating-point type cannot
# represent 0.10 exactly, so sums drift - which is why `gross = net + tax`
# below is a constraint that can actually hold rather than one that fails at
# random.
#
# NOTE ON created_at / updated_at: they carry NO DEFAULT, on purpose.
# The validated specification says updated_at is maintained by the application,
# not by a trigger. A `DEFAULT now()` would quietly stamp today's date onto a
# row that a seed meant to backdate to 2023, and the mistake would be
# invisible. Without a default, NOT NULL turns "I forgot to set it" into an
# immediate error.


# ===========================================================================
# 2. Table definitions, ordered by dependency
# ===========================================================================
# A table referenced by a foreign key must be created before the table that
# references it. PostgreSQL refuses to create a table referencing one that
# does not exist yet.


# ---------------------------------------------------------------------------
# cost_centers - the analytical axis (~25 rows)
# ---------------------------------------------------------------------------
# Self-referencing hierarchy on three levels:
#
#     1  Group     Orialis Group                     (1 row)
#     2  Country   FR, BE, CH, IT                    (4 rows)
#     3  Branch    the 7 offices                     (7 rows)
#        Function  IT, Compliance, Marketing, ...   (13 rows)
#
# `office_code` holds the ERP site code (FR-PAR-01). It is deliberately NOT
# the CRM branch id (BR001): the two systems do not share identifiers, and
# reconciling them is left as a platform problem.

CREATE_COST_CENTERS = """
CREATE TABLE IF NOT EXISTS cost_centers (
    cost_center_id        TEXT        NOT NULL,
    cost_center_name      TEXT        NOT NULL,
    cost_center_type      TEXT        NOT NULL,
    office_code           TEXT,
    country_code          TEXT,
    default_currency      TEXT        NOT NULL,
    parent_cost_center_id TEXT,
    hierarchy_level       SMALLINT    NOT NULL,
    cost_center_status    TEXT        NOT NULL,
    opened_on             DATE        NOT NULL,
    closed_on             DATE,
    created_at            TIMESTAMPTZ NOT NULL,
    updated_at            TIMESTAMPTZ NOT NULL,

    CONSTRAINT cost_centers_pk PRIMARY KEY (cost_center_id),

    -- A cost centre hangs off its parent. RESTRICT: closing a country must
    -- never remove the branches underneath it.
    CONSTRAINT cost_centers_parent_fk
        FOREIGN KEY (parent_cost_center_id)
        REFERENCES cost_centers (cost_center_id)
        ON DELETE RESTRICT,

    -- One office code identifies one branch cost centre. NULLs are exempt
    -- from UNIQUE in PostgreSQL, so the 18 non-branch rows coexist freely.
    CONSTRAINT cost_centers_office_code_uq UNIQUE (office_code),

    CONSTRAINT cost_centers_type_chk
        CHECK (cost_center_type IN ('Group', 'Country', 'Branch', 'Function')),
    CONSTRAINT cost_centers_status_chk
        CHECK (cost_center_status IN ('Active', 'Closed')),
    CONSTRAINT cost_centers_currency_chk
        CHECK (default_currency IN ('EUR', 'CHF')),
    CONSTRAINT cost_centers_country_chk
        CHECK (country_code IS NULL OR country_code IN ('FR', 'BE', 'CH', 'IT')),

    -- Level and type are two views of the same fact; they may not disagree.
    CONSTRAINT cost_centers_level_chk
        CHECK (
            (cost_center_type = 'Group'    AND hierarchy_level = 1) OR
            (cost_center_type = 'Country'  AND hierarchy_level = 2) OR
            (cost_center_type IN ('Branch', 'Function') AND hierarchy_level = 3)
        ),

    -- Only a branch sits in a physical office.
    CONSTRAINT cost_centers_office_chk
        CHECK ((cost_center_type = 'Branch') = (office_code IS NOT NULL)),

    -- The group is the only root, and the only row without a country.
    CONSTRAINT cost_centers_root_chk
        CHECK ((cost_center_type = 'Group') = (parent_cost_center_id IS NULL)),
    CONSTRAINT cost_centers_root_country_chk
        CHECK ((cost_center_type = 'Group') = (country_code IS NULL)),

    -- Currency follows the country, exhaustively: Switzerland is CHF, the
    -- three euro-zone countries and the group are EUR. Written as a total
    -- expression rather than an implication so that no combination is left
    -- undefined.
    CONSTRAINT cost_centers_currency_country_chk
        CHECK (
            (country_code IS NULL              AND default_currency = 'EUR') OR
            (country_code = 'CH'               AND default_currency = 'CHF') OR
            (country_code IN ('FR','BE','IT')  AND default_currency = 'EUR')
        ),

    -- Closure is a state AND a date, never one without the other.
    CONSTRAINT cost_centers_closed_chk
        CHECK ((cost_center_status = 'Closed') = (closed_on IS NOT NULL)),
    CONSTRAINT cost_centers_closed_order_chk
        CHECK (closed_on IS NULL OR closed_on >= opened_on),

    -- A cost centre cannot be its own parent. IS DISTINCT FROM rather than
    -- <> so that a NULL parent does not make the check unknown.
    CONSTRAINT cost_centers_self_parent_chk
        CHECK (parent_cost_center_id IS DISTINCT FROM cost_center_id),

    CONSTRAINT cost_centers_timestamps_chk
        CHECK (updated_at >= created_at)
);
"""


# ---------------------------------------------------------------------------
# suppliers - who Orialis pays (~180 rows)
# ---------------------------------------------------------------------------
# Three statuses, two distinct behaviours:
#
#     Active    new invoices allowed, payments allowed
#     Inactive  no new invoices, payments still allowed (settle the debt)
#     Blocked   no new invoices, payments FROZEN (dispute, compliance)
#
# Both rules are cross-table (they constrain invoices and payments, not the
# supplier row) and therefore belong to the application layer. The status
# vocabulary that makes them expressible is declared here.

CREATE_SUPPLIERS = """
CREATE TABLE IF NOT EXISTS suppliers (
    supplier_id         TEXT        NOT NULL,
    supplier_name       TEXT        NOT NULL,
    supplier_legal_name TEXT,
    supplier_category   TEXT        NOT NULL,
    country_code        TEXT        NOT NULL,
    vat_number          TEXT,
    iban_masked         TEXT,
    default_currency    TEXT        NOT NULL,
    payment_terms_days  SMALLINT    NOT NULL,
    supplier_status     TEXT        NOT NULL,
    onboarded_on        DATE        NOT NULL,
    deactivated_on      DATE,
    created_at          TIMESTAMPTZ NOT NULL,
    updated_at          TIMESTAMPTZ NOT NULL,

    CONSTRAINT suppliers_pk PRIMARY KEY (supplier_id),
    CONSTRAINT suppliers_vat_number_uq UNIQUE (vat_number),

    CONSTRAINT suppliers_category_chk
        CHECK (supplier_category IN (
            'Market Data', 'IT & Software', 'Real Estate & Facilities',
            'Professional Services', 'Travel', 'Marketing', 'Telecom',
            'Training', 'Insurance', 'Office Supplies'
        )),

    -- Suppliers are NOT restricted to the four Orialis countries: market data
    -- comes from the US and the UK, holdings from Luxembourg. That is what
    -- makes reverse charge and out-of-scope VAT appear in the data.
    CONSTRAINT suppliers_country_chk
        CHECK (country_code IN ('FR','BE','CH','IT','LU','DE','GB','US')),

    CONSTRAINT suppliers_currency_chk
        CHECK (default_currency IN ('EUR', 'CHF')),
    CONSTRAINT suppliers_status_chk
        CHECK (supplier_status IN ('Active', 'Inactive', 'Blocked')),
    CONSTRAINT suppliers_terms_chk
        CHECK (payment_terms_days IN (0, 15, 30, 45, 60, 90)),

    -- A Swiss supplier invoices in CHF.
    CONSTRAINT suppliers_chf_chk
        CHECK (country_code <> 'CH' OR default_currency = 'CHF'),

    -- No EU VAT number outside the EU.
    CONSTRAINT suppliers_vat_scope_chk
        CHECK (country_code NOT IN ('GB', 'US') OR vat_number IS NULL),

    -- THE MASKED IBAN, ENFORCED BY THE DATABASE.
    --
    -- The validated decision is that synthetic IBANs are masked AT SOURCE:
    -- the full value never exists in the database at all, so no export, dump
    -- or API can reveal one. Leaving that to the generator would make it a
    -- convention; this pattern makes it a constraint.
    --
    -- Shape: 2 country letters, 2 check digits, at least 4 asterisks, 4 final
    -- digits - FR76**********3021. A complete IBAN, which carries no
    -- asterisks, is structurally rejected.
    CONSTRAINT suppliers_iban_masked_chk
        CHECK (iban_masked IS NULL
               OR iban_masked ~ '^[A-Z]{2}[0-9]{2}[*]{4,}[0-9]{4}$'),

    -- Deactivation is a state AND a date.
    CONSTRAINT suppliers_deactivated_chk
        CHECK ((supplier_status <> 'Active') = (deactivated_on IS NOT NULL)),
    CONSTRAINT suppliers_deactivated_order_chk
        CHECK (deactivated_on IS NULL OR deactivated_on >= onboarded_on),

    CONSTRAINT suppliers_timestamps_chk
        CHECK (updated_at >= created_at)
);
"""


# ---------------------------------------------------------------------------
# employees - the ERP-side staff register (~185 rows)
# ---------------------------------------------------------------------------
# NOT a copy of the CRM advisors, and there is no column here that could be
# joined to one. Three differences make the two registers genuinely distinct:
#
#   - population: advisors AND back office, IT, compliance, management - the
#     people who submit expenses but have no commercial existence
#   - lifecycle: departed employees are KEPT (payroll history), where the CRM
#     only lists active advisors
#   - identifier: EMP-0047, deliberately unlike ADV012
#
# `work_email` is nullable on purpose: roughly 5% will be missing, so that a
# future reconciliation cannot be solved by an email join alone.

CREATE_EMPLOYEES = """
CREATE TABLE IF NOT EXISTS employees (
    employee_ref           TEXT        NOT NULL,
    first_name             TEXT        NOT NULL,
    last_name              TEXT        NOT NULL,
    work_email             TEXT,
    employee_type          TEXT        NOT NULL,
    cost_center_id         TEXT        NOT NULL,
    office_code            TEXT,
    country_code           TEXT        NOT NULL,
    hire_date              DATE        NOT NULL,
    departure_date         DATE,
    is_commission_eligible BOOLEAN     NOT NULL DEFAULT FALSE,
    employee_status        TEXT        NOT NULL,
    created_at             TIMESTAMPTZ NOT NULL,
    updated_at             TIMESTAMPTZ NOT NULL,

    CONSTRAINT employees_pk PRIMARY KEY (employee_ref),
    CONSTRAINT employees_work_email_uq UNIQUE (work_email),

    CONSTRAINT employees_cost_center_fk
        FOREIGN KEY (cost_center_id)
        REFERENCES cost_centers (cost_center_id)
        ON DELETE RESTRICT,

    CONSTRAINT employees_type_chk
        CHECK (employee_type IN (
            'Advisor', 'Back Office', 'Management', 'IT', 'Compliance', 'Support'
        )),
    CONSTRAINT employees_country_chk
        CHECK (country_code IN ('FR', 'BE', 'CH', 'IT')),
    CONSTRAINT employees_status_chk
        CHECK (employee_status IN ('Active', 'Inactive')),

    -- Only an advisor earns commission. The commissions table relies on this.
    CONSTRAINT employees_commission_eligible_chk
        CHECK (NOT is_commission_eligible OR employee_type = 'Advisor'),

    -- Departure is a state AND a date.
    CONSTRAINT employees_departure_chk
        CHECK ((employee_status = 'Inactive') = (departure_date IS NOT NULL)),
    CONSTRAINT employees_departure_order_chk
        CHECK (departure_date IS NULL OR departure_date >= hire_date),

    CONSTRAINT employees_email_shape_chk
        CHECK (work_email IS NULL OR work_email LIKE '%@%.%'),

    CONSTRAINT employees_timestamps_chk
        CHECK (updated_at >= created_at)
);
"""


# ---------------------------------------------------------------------------
# supplier_invoices - the debt (~8 000 rows)
# ---------------------------------------------------------------------------
# Named `supplier_invoices`, not `invoices`: Orialis both receives supplier
# invoices (a liability) and issues client fee invoices (a receivable). Only
# the first is in scope, and the name says so permanently.
#
# TWO STATUS COLUMNS, and that is the central modelling decision.
#
#   invoice_approval_status   a human decision   Draft -> Approved / Rejected
#   invoice_payment_status    a treasury fact    Unpaid -> Partially -> Paid
#
# They advance independently. "Approved but unpaid" is the normal state of
# every invoice that is not yet due - a single status column could not express
# it without duplicating every value of the other cycle.

CREATE_SUPPLIER_INVOICES = """
CREATE TABLE IF NOT EXISTS supplier_invoices (
    invoice_id               TEXT          NOT NULL,
    supplier_id              TEXT          NOT NULL,
    supplier_invoice_number  TEXT          NOT NULL,
    cost_center_id           TEXT          NOT NULL,
    invoice_type             TEXT          NOT NULL,
    invoice_date             DATE          NOT NULL,
    received_date            DATE          NOT NULL,
    due_date                 DATE          NOT NULL,
    currency_code            TEXT          NOT NULL,
    net_amount               NUMERIC(14,2) NOT NULL,
    tax_treatment            TEXT          NOT NULL,
    tax_rate                 NUMERIC(5,2)  NOT NULL,
    tax_amount               NUMERIC(14,2) NOT NULL,
    gross_amount             NUMERIC(14,2) NOT NULL,
    fx_rate_to_eur           NUMERIC(12,6) NOT NULL,
    gross_amount_eur         NUMERIC(14,2) NOT NULL,
    paid_amount              NUMERIC(14,2) NOT NULL DEFAULT 0,
    invoice_approval_status  TEXT          NOT NULL,
    invoice_payment_status   TEXT          NOT NULL,
    approved_by_employee_ref TEXT,
    approved_at              TIMESTAMPTZ,
    rejection_reason         TEXT,
    created_at               TIMESTAMPTZ   NOT NULL,
    updated_at               TIMESTAMPTZ   NOT NULL,

    CONSTRAINT supplier_invoices_pk PRIMARY KEY (invoice_id),

    CONSTRAINT supplier_invoices_supplier_fk
        FOREIGN KEY (supplier_id)
        REFERENCES suppliers (supplier_id)
        ON DELETE RESTRICT,
    CONSTRAINT supplier_invoices_cost_center_fk
        FOREIGN KEY (cost_center_id)
        REFERENCES cost_centers (cost_center_id)
        ON DELETE RESTRICT,
    CONSTRAINT supplier_invoices_approver_fk
        FOREIGN KEY (approved_by_employee_ref)
        REFERENCES employees (employee_ref)
        ON DELETE RESTRICT,

    -- DUPLICATE BOOKING CONTROL.
    -- The same supplier cannot have its invoice "2026-0417" recorded twice.
    -- Double payment of a duplicated invoice is one of the most expensive
    -- errors in accounts payable, and it costs one index to make impossible.
    CONSTRAINT supplier_invoices_number_uq
        UNIQUE (supplier_id, supplier_invoice_number),

    -- Target of the composite foreign key from payment_allocations. It is a
    -- superkey (invoice_id alone is already unique), so it adds no new rule -
    -- it exists solely because a composite FK needs a matching unique index
    -- to point at. That is the price of making currency and supplier
    -- coherence structural rather than procedural.
    CONSTRAINT supplier_invoices_allocation_target_uq
        UNIQUE (invoice_id, supplier_id, currency_code),

    CONSTRAINT supplier_invoices_type_chk
        CHECK (invoice_type IN ('Standard', 'Credit Note')),
    CONSTRAINT supplier_invoices_currency_chk
        CHECK (currency_code IN ('EUR', 'CHF')),
    CONSTRAINT supplier_invoices_tax_treatment_chk
        CHECK (tax_treatment IN (
            'Standard', 'Reverse Charge', 'Exempt', 'Out of Scope'
        )),
    CONSTRAINT supplier_invoices_approval_status_chk
        CHECK (invoice_approval_status IN (
            'Draft', 'Pending Approval', 'Approved', 'Rejected', 'Cancelled'
        )),
    CONSTRAINT supplier_invoices_payment_status_chk
        CHECK (invoice_payment_status IN (
            'Unpaid', 'Partially Paid', 'Paid'
        )),

    -- Dates. An invoice can only be received and fall due on or after the day
    -- it was issued.
    CONSTRAINT supplier_invoices_received_chk
        CHECK (received_date >= invoice_date),
    CONSTRAINT supplier_invoices_due_chk
        CHECK (due_date >= invoice_date),

    -- THE AMOUNT IDENTITY.
    -- Only the addition is checked. Deliberately NOT
    -- `tax_amount = round(net_amount * tax_rate / 100, 2)`: real invoices are
    -- computed line by line and then totalled, which legitimately produces
    -- one-cent differences. Such a constraint would reject valid invoices.
    -- The rate is informational; the addition is the fact.
    CONSTRAINT supplier_invoices_amount_identity_chk
        CHECK (gross_amount = net_amount + tax_amount),

    -- A credit note is a NEGATIVE invoice. Modelled this way rather than as a
    -- separate table so that it clears through the same allocations and
    -- aggregates naturally.
    CONSTRAINT supplier_invoices_sign_chk
        CHECK (
            (invoice_type = 'Standard'    AND net_amount > 0 AND tax_amount >= 0) OR
            (invoice_type = 'Credit Note' AND net_amount < 0 AND tax_amount <= 0)
        ),
    CONSTRAINT supplier_invoices_eur_sign_chk
        CHECK (sign(gross_amount_eur) = sign(gross_amount)),

    CONSTRAINT supplier_invoices_tax_rate_chk
        CHECK (tax_rate >= 0 AND tax_rate <= 25),

    -- Zero-rated is not one situation but three. Reverse charge, exemption
    -- and out-of-scope all carry a 0% rate, and only `tax_treatment` tells
    -- them apart.
    CONSTRAINT supplier_invoices_tax_zero_chk
        CHECK (tax_treatment = 'Standard' OR tax_rate = 0),

    CONSTRAINT supplier_invoices_fx_chk
        CHECK (fx_rate_to_eur > 0),
    CONSTRAINT supplier_invoices_fx_eur_chk
        CHECK (currency_code <> 'EUR' OR fx_rate_to_eur = 1),

    -- paid_amount stays between zero and the invoice total, IN THE DIRECTION
    -- OF THE SIGN - so a credit note is cleared by negative allocations.
    -- Its VALUE is maintained by the application (a sum across allocations,
    -- which no CHECK can express); its BOUNDS are a per-row fact, so they are
    -- declared here.
    CONSTRAINT supplier_invoices_paid_bounds_chk
        CHECK (
            (gross_amount > 0 AND paid_amount >= 0 AND paid_amount <= gross_amount) OR
            (gross_amount < 0 AND paid_amount <= 0 AND paid_amount >= gross_amount)
        ),

    -- The payment status is a pure function of paid_amount and gross_amount,
    -- both on this row - so PostgreSQL can enforce it. This lifts one of the
    -- fourteen planned application checks into the database.
    CONSTRAINT supplier_invoices_payment_status_coherence_chk
        CHECK (
            (invoice_payment_status = 'Unpaid'         AND paid_amount = 0) OR
            (invoice_payment_status = 'Paid'           AND paid_amount = gross_amount) OR
            (invoice_payment_status = 'Partially Paid' AND paid_amount <> 0
                                                       AND paid_amount <> gross_amount)
        ),

    -- Approval is a state AND an instant AND an approver.
    CONSTRAINT supplier_invoices_approved_chk
        CHECK ((invoice_approval_status = 'Approved') = (approved_at IS NOT NULL)),
    CONSTRAINT supplier_invoices_approver_chk
        CHECK ((approved_at IS NULL) = (approved_by_employee_ref IS NULL)),
    CONSTRAINT supplier_invoices_rejected_chk
        CHECK ((invoice_approval_status = 'Rejected') = (rejection_reason IS NOT NULL)),

    -- THE INTERNAL CONTROL OF THE PURCHASE CYCLE.
    -- Nothing is paid that has not been approved. One line, and the most
    -- elementary segregation rule of accounts payable holds.
    CONSTRAINT supplier_invoices_pay_requires_approval_chk
        CHECK (invoice_payment_status = 'Unpaid'
               OR invoice_approval_status = 'Approved'),

    CONSTRAINT supplier_invoices_timestamps_chk
        CHECK (updated_at >= created_at)
);
"""


# ---------------------------------------------------------------------------
# payments - the cash going out (~7 000 rows)
# ---------------------------------------------------------------------------
# NO cost_center_id, deliberately. The charge is recognised on the INVOICE;
# imputing the payment as well would count the cost twice. An invoice is a
# profit-and-loss fact, a payment is a treasury fact - a missing column is how
# that distinction is enforced.
#
# `supplier_id` on the payment is an assumed simplification: one transfer goes
# to one IBAN, so a batch payment covers invoices of a single supplier. It is
# what makes the composite foreign key below able to guarantee that an
# allocation never crosses suppliers.

CREATE_PAYMENTS = """
CREATE TABLE IF NOT EXISTS payments (
    payment_id     TEXT          NOT NULL,
    supplier_id    TEXT          NOT NULL,
    payment_date   DATE          NOT NULL,
    value_date     DATE,
    currency_code  TEXT          NOT NULL,
    payment_amount NUMERIC(14,2) NOT NULL,
    payment_method TEXT          NOT NULL,
    payment_status TEXT          NOT NULL,
    bank_reference TEXT,
    executed_at    TIMESTAMPTZ,
    failure_reason TEXT,
    created_at     TIMESTAMPTZ   NOT NULL,
    updated_at     TIMESTAMPTZ   NOT NULL,

    CONSTRAINT payments_pk PRIMARY KEY (payment_id),
    CONSTRAINT payments_bank_reference_uq UNIQUE (bank_reference),

    CONSTRAINT payments_supplier_fk
        FOREIGN KEY (supplier_id)
        REFERENCES suppliers (supplier_id)
        ON DELETE RESTRICT,

    -- Target of the composite foreign key from payment_allocations; a
    -- superkey, like its counterpart on supplier_invoices.
    CONSTRAINT payments_allocation_target_uq
        UNIQUE (payment_id, supplier_id, currency_code),

    CONSTRAINT payments_currency_chk
        CHECK (currency_code IN ('EUR', 'CHF')),
    CONSTRAINT payments_method_chk
        CHECK (payment_method IN (
            'SEPA Credit Transfer', 'SWIFT', 'Direct Debit', 'Card', 'Cheque'
        )),
    CONSTRAINT payments_status_chk
        CHECK (payment_status IN ('Initiated', 'Executed', 'Failed', 'Cancelled')),

    -- A payment moves money out; the direction is carried by the allocation,
    -- not by a negative payment.
    CONSTRAINT payments_amount_chk
        CHECK (payment_amount > 0),

    CONSTRAINT payments_executed_chk
        CHECK ((payment_status = 'Executed') = (executed_at IS NOT NULL)),
    CONSTRAINT payments_failed_chk
        CHECK ((payment_status = 'Failed') = (failure_reason IS NOT NULL)),
    CONSTRAINT payments_value_date_chk
        CHECK (value_date IS NULL OR value_date >= payment_date),

    CONSTRAINT payments_timestamps_chk
        CHECK (updated_at >= created_at)
);
"""


# ---------------------------------------------------------------------------
# payment_allocations - the matching (~8 000 rows)
# ---------------------------------------------------------------------------
# The table that makes partial payments and batch payments possible, and the
# most delicate one in the schema.
#
# WHY A SURROGATE PRIMARY KEY.
#
# The natural key would be (payment_id, invoice_id). It cannot be the primary
# key here, because deletion is forbidden: correcting a mis-matched allocation
# means cancelling the old row and inserting a NEW one for the same pair, and
# a composite primary key would refuse it. Hence `allocation_id`, with the
# uniqueness rule moved into a PARTIAL index restricted to active rows (see
# the index section below).
#
# TWO INDEPENDENT WAYS AN ALLOCATION STOPS COUNTING - and they are not
# interchangeable:
#
#   allocation_status = 'Cancelled'     the MATCHING was wrong
#                                       (10 000 paid, split 6/4 instead of 5/5)
#                                       the payment stays Executed
#
#   payments.payment_status <> 'Executed'   the PAYMENT did not go through
#                                           (rejected transfer)
#                                           the allocations are LEFT ALONE
#
# The second case needs no write here at all: filtering on the payment status
# makes the invoices fall back to Unpaid by itself. Cancelling the allocations
# as well would make a rejected-then-reissued payment impossible to represent.
#
# The resulting rule, which the application must implement:
#
#   paid_amount = SUM(allocated_amount)
#                 WHERE allocation_status = 'Active'
#                   AND payments.payment_status = 'Executed'

CREATE_PAYMENT_ALLOCATIONS = """
CREATE TABLE IF NOT EXISTS payment_allocations (
    allocation_id             TEXT          NOT NULL,
    payment_id                TEXT          NOT NULL,
    invoice_id                TEXT          NOT NULL,
    supplier_id               TEXT          NOT NULL,
    currency_code             TEXT          NOT NULL,
    allocated_amount          NUMERIC(14,2) NOT NULL,
    allocation_status         TEXT          NOT NULL,
    cancelled_at              TIMESTAMPTZ,
    cancellation_reason       TEXT,
    replaced_by_allocation_id TEXT,
    created_at                TIMESTAMPTZ   NOT NULL,
    updated_at                TIMESTAMPTZ   NOT NULL,

    CONSTRAINT payment_allocations_pk PRIMARY KEY (allocation_id),

    -- THE TWO COMPOSITE FOREIGN KEYS.
    --
    -- `supplier_id` and `currency_code` are denormalised onto this row for
    -- one reason: they turn two invariants that would otherwise need an
    -- application check into facts the database refuses to violate.
    --
    --   a CHF payment can never be matched to an EUR invoice
    --   supplier A's payment can never be matched to supplier B's invoice
    --
    -- Both are per-row questions once the columns are present, so the
    -- doctrine says they belong here. They also subsume the plain foreign
    -- keys to payments and supplier_invoices - a composite FK is strictly
    -- stronger, so declaring the simple ones as well would add nothing.
    CONSTRAINT payment_allocations_invoice_fk
        FOREIGN KEY (invoice_id, supplier_id, currency_code)
        REFERENCES supplier_invoices (invoice_id, supplier_id, currency_code)
        ON DELETE RESTRICT,
    CONSTRAINT payment_allocations_payment_fk
        FOREIGN KEY (payment_id, supplier_id, currency_code)
        REFERENCES payments (payment_id, supplier_id, currency_code)
        ON DELETE RESTRICT,

    -- The correction chain: a cancelled allocation points at the row that
    -- replaced it, so the history says WHY there are two rows for one pair.
    CONSTRAINT payment_allocations_replacement_fk
        FOREIGN KEY (replaced_by_allocation_id)
        REFERENCES payment_allocations (allocation_id)
        ON DELETE RESTRICT,

    CONSTRAINT payment_allocations_status_chk
        CHECK (allocation_status IN ('Active', 'Cancelled')),
    CONSTRAINT payment_allocations_currency_chk
        CHECK (currency_code IN ('EUR', 'CHF')),

    -- Zero would be a matching that matches nothing. The SIGN is not checked
    -- here: it must follow the invoice's sign, which lives on another row.
    CONSTRAINT payment_allocations_amount_chk
        CHECK (allocated_amount <> 0),

    -- Cancellation is a state AND an instant AND a reason.
    CONSTRAINT payment_allocations_cancelled_chk
        CHECK ((allocation_status = 'Cancelled') = (cancelled_at IS NOT NULL)),
    CONSTRAINT payment_allocations_cancel_reason_chk
        CHECK ((allocation_status = 'Cancelled') = (cancellation_reason IS NOT NULL)),

    -- Only a cancelled row can have been replaced.
    CONSTRAINT payment_allocations_replacement_chk
        CHECK (replaced_by_allocation_id IS NULL
               OR allocation_status = 'Cancelled'),
    CONSTRAINT payment_allocations_self_replacement_chk
        CHECK (replaced_by_allocation_id IS DISTINCT FROM allocation_id),

    CONSTRAINT payment_allocations_timestamps_chk
        CHECK (updated_at >= created_at)
);
"""


# ---------------------------------------------------------------------------
# expenses - employee out-of-pocket costs (~12 000 rows)
# ---------------------------------------------------------------------------
# ONE status column here, unlike invoices, and the asymmetry is deliberate: a
# reimbursement is atomic. Nobody is reimbursed 40% of a train ticket, so
# there is no equivalent of 'Partially Paid' and therefore no second cycle to
# separate.
#
# Reimbursement is recorded HERE and does not go through `payments`. Sharing
# that table would require a polymorphic foreign key (payable_type /
# payable_id) that PostgreSQL cannot enforce - trading real referential
# integrity on the most sensitive table of the model for a cosmetic symmetry.

CREATE_EXPENSES = """
CREATE TABLE IF NOT EXISTS expenses (
    expense_id               TEXT          NOT NULL,
    employee_ref             TEXT          NOT NULL,
    cost_center_id           TEXT          NOT NULL,
    expense_category         TEXT          NOT NULL,
    expense_date             DATE          NOT NULL,
    submitted_date           DATE,
    currency_code            TEXT          NOT NULL,
    net_amount               NUMERIC(14,2) NOT NULL,
    tax_rate                 NUMERIC(5,2)  NOT NULL,
    tax_amount               NUMERIC(14,2) NOT NULL,
    gross_amount             NUMERIC(14,2) NOT NULL,
    fx_rate_to_eur           NUMERIC(12,6) NOT NULL,
    gross_amount_eur         NUMERIC(14,2) NOT NULL,
    expense_status           TEXT          NOT NULL,
    approved_by_employee_ref TEXT,
    approved_at              TIMESTAMPTZ,
    rejection_reason         TEXT,
    reimbursed_on            DATE,
    reimbursement_reference  TEXT,
    receipt_reference        TEXT,
    created_at               TIMESTAMPTZ   NOT NULL,
    updated_at               TIMESTAMPTZ   NOT NULL,

    CONSTRAINT expenses_pk PRIMARY KEY (expense_id),

    CONSTRAINT expenses_employee_fk
        FOREIGN KEY (employee_ref)
        REFERENCES employees (employee_ref)
        ON DELETE RESTRICT,
    CONSTRAINT expenses_cost_center_fk
        FOREIGN KEY (cost_center_id)
        REFERENCES cost_centers (cost_center_id)
        ON DELETE RESTRICT,
    CONSTRAINT expenses_approver_fk
        FOREIGN KEY (approved_by_employee_ref)
        REFERENCES employees (employee_ref)
        ON DELETE RESTRICT,

    CONSTRAINT expenses_category_chk
        CHECK (expense_category IN (
            'Travel', 'Accommodation', 'Meals', 'Client Entertainment',
            'Transport', 'Training', 'Telecom', 'Office Supplies'
        )),
    CONSTRAINT expenses_currency_chk
        CHECK (currency_code IN ('EUR', 'CHF')),
    CONSTRAINT expenses_status_chk
        CHECK (expense_status IN (
            'Draft', 'Submitted', 'Approved', 'Rejected', 'Reimbursed', 'Cancelled'
        )),

    -- Expenses are always positive: there is no such thing as a credit-note
    -- expense. A correction is a separate, cancelled claim.
    CONSTRAINT expenses_amounts_chk
        CHECK (net_amount > 0 AND tax_amount >= 0 AND gross_amount > 0),
    CONSTRAINT expenses_amount_identity_chk
        CHECK (gross_amount = net_amount + tax_amount),
    CONSTRAINT expenses_tax_rate_chk
        CHECK (tax_rate >= 0 AND tax_rate <= 25),
    CONSTRAINT expenses_fx_chk
        CHECK (fx_rate_to_eur > 0),
    CONSTRAINT expenses_fx_eur_chk
        CHECK (currency_code <> 'EUR' OR fx_rate_to_eur = 1),
    CONSTRAINT expenses_eur_amount_chk
        CHECK (gross_amount_eur > 0),

    -- Dates follow the cycle: incurred, then submitted, then reimbursed.
    CONSTRAINT expenses_submitted_order_chk
        CHECK (submitted_date IS NULL OR submitted_date >= expense_date),
    CONSTRAINT expenses_reimbursed_order_chk
        CHECK (reimbursed_on IS NULL
               OR submitted_date IS NULL
               OR reimbursed_on >= submitted_date),

    -- Anything past Draft has been submitted. Cancelled is exempt: a draft
    -- can be abandoned before it is ever sent.
    CONSTRAINT expenses_submitted_chk
        CHECK (expense_status IN ('Draft', 'Cancelled')
               OR submitted_date IS NOT NULL),

    CONSTRAINT expenses_approved_chk
        CHECK ((expense_status IN ('Approved', 'Reimbursed'))
               = (approved_at IS NOT NULL)),
    CONSTRAINT expenses_approver_chk
        CHECK ((approved_at IS NULL) = (approved_by_employee_ref IS NULL)),
    CONSTRAINT expenses_rejected_chk
        CHECK ((expense_status = 'Rejected') = (rejection_reason IS NOT NULL)),
    CONSTRAINT expenses_reimbursed_chk
        CHECK ((expense_status = 'Reimbursed') = (reimbursed_on IS NOT NULL)),

    -- SEGREGATION OF DUTIES: nobody approves their own expense claim.
    CONSTRAINT expenses_self_approval_chk
        CHECK (approved_by_employee_ref IS NULL
               OR approved_by_employee_ref <> employee_ref),

    CONSTRAINT expenses_timestamps_chk
        CHECK (updated_at >= created_at)
);
"""


# ---------------------------------------------------------------------------
# commissions - the administrative RESULT of a commission run (~5 900 rows)
# ---------------------------------------------------------------------------
# The ERP records what is owed, not how it was computed. `basis_amount` is a
# figure RECEIVED from an upstream system and copied here; there is no foreign
# key to anything that could recompute it, and `source_system` is a plain text
# label, never a reference.
#
# That is exactly how a real ERP behaves when it ingests a commission file: it
# knows the amount and the period, and it cannot reproduce the calculation.

CREATE_COMMISSIONS = """
CREATE TABLE IF NOT EXISTS commissions (
    commission_id             TEXT          NOT NULL,
    employee_ref              TEXT          NOT NULL,
    cost_center_id            TEXT          NOT NULL,
    period_type               TEXT          NOT NULL,
    period_start              DATE          NOT NULL,
    period_end                DATE          NOT NULL,
    commission_type           TEXT          NOT NULL,
    basis_amount              NUMERIC(14,2) NOT NULL,
    commission_rate           NUMERIC(6,4)  NOT NULL,
    currency_code             TEXT          NOT NULL,
    commission_amount         NUMERIC(14,2) NOT NULL,
    fx_rate_to_eur            NUMERIC(12,6) NOT NULL,
    commission_amount_eur     NUMERIC(14,2) NOT NULL,
    commission_status         TEXT          NOT NULL,
    calculated_at             TIMESTAMPTZ   NOT NULL,
    validated_at              TIMESTAMPTZ,
    validated_by_employee_ref TEXT,
    paid_on                   DATE,
    payroll_reference         TEXT,
    cancellation_reason       TEXT,
    source_system             TEXT          NOT NULL DEFAULT 'CRM',
    created_at                TIMESTAMPTZ   NOT NULL,
    updated_at                TIMESTAMPTZ   NOT NULL,

    CONSTRAINT commissions_pk PRIMARY KEY (commission_id),

    CONSTRAINT commissions_employee_fk
        FOREIGN KEY (employee_ref)
        REFERENCES employees (employee_ref)
        ON DELETE RESTRICT,
    CONSTRAINT commissions_cost_center_fk
        FOREIGN KEY (cost_center_id)
        REFERENCES cost_centers (cost_center_id)
        ON DELETE RESTRICT,
    CONSTRAINT commissions_validator_fk
        FOREIGN KEY (validated_by_employee_ref)
        REFERENCES employees (employee_ref)
        ON DELETE RESTRICT,

    -- THE ANTI-DUPLICATE KEY, and the most important constraint here.
    -- A monthly run replayed after an incident must not double what is owed.
    -- This makes the replay idempotent BY CONSTRUCTION - the same property
    -- the CRM seed scripts have, where "seeds describe a state and converge
    -- to it".
    CONSTRAINT commissions_period_uq
        UNIQUE (employee_ref, commission_type, period_start, period_end),

    CONSTRAINT commissions_period_type_chk
        CHECK (period_type IN ('Monthly', 'Annual')),
    CONSTRAINT commissions_type_chk
        CHECK (commission_type IN (
            'Recurring Management Fee', 'New Business', 'Performance Bonus'
        )),
    CONSTRAINT commissions_currency_chk
        CHECK (currency_code IN ('EUR', 'CHF')),
    CONSTRAINT commissions_status_chk
        CHECK (commission_status IN ('Calculated', 'Validated', 'Paid', 'Cancelled')),

    CONSTRAINT commissions_period_order_chk
        CHECK (period_end >= period_start),

    CONSTRAINT commissions_basis_chk
        CHECK (basis_amount >= 0),
    CONSTRAINT commissions_rate_chk
        CHECK (commission_rate > 0 AND commission_rate <= 1),
    CONSTRAINT commissions_amount_chk
        CHECK (commission_amount >= 0 AND commission_amount_eur >= 0),
    CONSTRAINT commissions_fx_chk
        CHECK (fx_rate_to_eur > 0),
    CONSTRAINT commissions_fx_eur_chk
        CHECK (currency_code <> 'EUR' OR fx_rate_to_eur = 1),

    -- Validation is a state AND an instant AND a validator.
    CONSTRAINT commissions_validated_chk
        CHECK ((commission_status IN ('Validated', 'Paid'))
               = (validated_at IS NOT NULL)),
    CONSTRAINT commissions_validator_chk
        CHECK ((validated_at IS NULL) = (validated_by_employee_ref IS NULL)),
    CONSTRAINT commissions_paid_chk
        CHECK ((commission_status = 'Paid') = (paid_on IS NOT NULL)),
    CONSTRAINT commissions_cancelled_chk
        CHECK ((commission_status = 'Cancelled') = (cancellation_reason IS NOT NULL)),

    -- SEGREGATION OF DUTIES: nobody validates their own commission.
    CONSTRAINT commissions_self_validation_chk
        CHECK (validated_by_employee_ref IS NULL
               OR validated_by_employee_ref <> employee_ref),

    CONSTRAINT commissions_timestamps_chk
        CHECK (updated_at >= created_at)
);
"""


# ---------------------------------------------------------------------------
# fx_rates - monthly reference rates (36 rows)
# ---------------------------------------------------------------------------
# This table EXPLAINS, it does not convert. The rate frozen on each invoice,
# expense and commission stays the accounting truth: recomputing a conversion
# today with today's rate would rewrite the past on every run.
#
# The only table in the schema with no foreign key, in either direction, and
# the only one that is purely append-only.

CREATE_FX_RATES = """
CREATE TABLE IF NOT EXISTS fx_rates (
    rate_month    DATE          NOT NULL,
    from_currency TEXT          NOT NULL,
    to_currency   TEXT          NOT NULL,
    rate          NUMERIC(12,6) NOT NULL,
    created_at    TIMESTAMPTZ   NOT NULL,

    CONSTRAINT fx_rates_pk PRIMARY KEY (rate_month, from_currency, to_currency),

    CONSTRAINT fx_rates_from_chk  CHECK (from_currency IN ('EUR', 'CHF')),
    CONSTRAINT fx_rates_to_chk    CHECK (to_currency IN ('EUR', 'CHF')),
    CONSTRAINT fx_rates_pair_chk  CHECK (from_currency <> to_currency),
    CONSTRAINT fx_rates_rate_chk  CHECK (rate > 0),

    -- A monthly rate is stored on the first of the month. Without this, the
    -- same month could be recorded twice under two different days and the
    -- primary key would not notice.
    CONSTRAINT fx_rates_month_chk CHECK (EXTRACT(DAY FROM rate_month) = 1)
);
"""


# Iterated in order: cost_centers first (everything depends on it),
# fx_rates last (nothing depends on it).
TABLES = [
    ("cost_centers", CREATE_COST_CENTERS),
    ("suppliers", CREATE_SUPPLIERS),
    ("employees", CREATE_EMPLOYEES),
    ("supplier_invoices", CREATE_SUPPLIER_INVOICES),
    ("payments", CREATE_PAYMENTS),
    ("payment_allocations", CREATE_PAYMENT_ALLOCATIONS),
    ("expenses", CREATE_EXPENSES),
    ("commissions", CREATE_COMMISSIONS),
    ("fx_rates", CREATE_FX_RATES),
]

TABLE_NAMES = [name for name, _ in TABLES]


# ===========================================================================
# 3. Indexes
# ===========================================================================
# Three families, for three different reasons.
#
# a) FOREIGN KEY COLUMNS.
#    PostgreSQL indexes the TARGET of a foreign key automatically (it is a
#    primary or unique key) but NOT the source column. With ON DELETE RESTRICT
#    on every key in this schema, each delete attempt has to scan the
#    referencing table to find out whether it is allowed - a sequential scan
#    over 12 000 expenses every time. These indexes are what keep the
#    "never delete" posture cheap to verify.
#
# b) THE EXTRACTION WATERMARK.
#    Every table carries an index on updated_at. A future platform asks "what
#    changed since?" on exactly that column, and without an index the answer
#    costs a full table scan each run.
#
# c) THE BUSINESS FILTERS named in the specification: due dates, statuses,
#    periods.

INDEXES = [
    # -- cost_centers --------------------------------------------------------
    ("cost_centers_parent_idx",
     "CREATE INDEX IF NOT EXISTS cost_centers_parent_idx "
     "ON cost_centers (parent_cost_center_id)"),
    ("cost_centers_updated_at_idx",
     "CREATE INDEX IF NOT EXISTS cost_centers_updated_at_idx "
     "ON cost_centers (updated_at)"),

    # -- suppliers -----------------------------------------------------------
    ("suppliers_status_idx",
     "CREATE INDEX IF NOT EXISTS suppliers_status_idx "
     "ON suppliers (supplier_status)"),
    ("suppliers_category_idx",
     "CREATE INDEX IF NOT EXISTS suppliers_category_idx "
     "ON suppliers (supplier_category)"),
    ("suppliers_updated_at_idx",
     "CREATE INDEX IF NOT EXISTS suppliers_updated_at_idx "
     "ON suppliers (updated_at)"),

    # -- employees -----------------------------------------------------------
    ("employees_cost_center_idx",
     "CREATE INDEX IF NOT EXISTS employees_cost_center_idx "
     "ON employees (cost_center_id)"),
    ("employees_status_idx",
     "CREATE INDEX IF NOT EXISTS employees_status_idx "
     "ON employees (employee_status)"),
    ("employees_updated_at_idx",
     "CREATE INDEX IF NOT EXISTS employees_updated_at_idx "
     "ON employees (updated_at)"),

    # -- supplier_invoices ---------------------------------------------------
    ("supplier_invoices_supplier_idx",
     "CREATE INDEX IF NOT EXISTS supplier_invoices_supplier_idx "
     "ON supplier_invoices (supplier_id)"),
    ("supplier_invoices_cost_center_idx",
     "CREATE INDEX IF NOT EXISTS supplier_invoices_cost_center_idx "
     "ON supplier_invoices (cost_center_id)"),
    ("supplier_invoices_approver_idx",
     "CREATE INDEX IF NOT EXISTS supplier_invoices_approver_idx "
     "ON supplier_invoices (approved_by_employee_ref)"),
    ("supplier_invoices_due_date_idx",
     "CREATE INDEX IF NOT EXISTS supplier_invoices_due_date_idx "
     "ON supplier_invoices (due_date)"),
    ("supplier_invoices_status_idx",
     "CREATE INDEX IF NOT EXISTS supplier_invoices_status_idx "
     "ON supplier_invoices (invoice_approval_status, invoice_payment_status)"),
    ("supplier_invoices_updated_at_idx",
     "CREATE INDEX IF NOT EXISTS supplier_invoices_updated_at_idx "
     "ON supplier_invoices (updated_at)"),

    # -- payments ------------------------------------------------------------
    ("payments_supplier_idx",
     "CREATE INDEX IF NOT EXISTS payments_supplier_idx "
     "ON payments (supplier_id)"),
    ("payments_payment_date_idx",
     "CREATE INDEX IF NOT EXISTS payments_payment_date_idx "
     "ON payments (payment_date)"),
    ("payments_status_idx",
     "CREATE INDEX IF NOT EXISTS payments_status_idx "
     "ON payments (payment_status)"),
    ("payments_updated_at_idx",
     "CREATE INDEX IF NOT EXISTS payments_updated_at_idx "
     "ON payments (updated_at)"),

    # -- payment_allocations -------------------------------------------------
    #
    # THE PARTIAL UNIQUE INDEX - the keystone of the "no deletion" decision.
    #
    # It says: at most one ACTIVE allocation per (payment, invoice) pair.
    # Cancelled rows are outside the index entirely, so any number of them can
    # pile up on the same pair. That is precisely what allows a mis-matched
    # allocation to be cancelled and replaced without ever deleting anything -
    # which a composite primary key on (payment_id, invoice_id) would have
    # made impossible.
    ("payment_allocations_active_uq",
     "CREATE UNIQUE INDEX IF NOT EXISTS payment_allocations_active_uq "
     "ON payment_allocations (payment_id, invoice_id) "
     "WHERE allocation_status = 'Active'"),

    ("payment_allocations_invoice_idx",
     "CREATE INDEX IF NOT EXISTS payment_allocations_invoice_idx "
     "ON payment_allocations (invoice_id)"),
    ("payment_allocations_payment_idx",
     "CREATE INDEX IF NOT EXISTS payment_allocations_payment_idx "
     "ON payment_allocations (payment_id)"),
    ("payment_allocations_replacement_idx",
     "CREATE INDEX IF NOT EXISTS payment_allocations_replacement_idx "
     "ON payment_allocations (replaced_by_allocation_id)"),
    ("payment_allocations_updated_at_idx",
     "CREATE INDEX IF NOT EXISTS payment_allocations_updated_at_idx "
     "ON payment_allocations (updated_at)"),

    # -- expenses ------------------------------------------------------------
    ("expenses_employee_idx",
     "CREATE INDEX IF NOT EXISTS expenses_employee_idx "
     "ON expenses (employee_ref)"),
    ("expenses_cost_center_idx",
     "CREATE INDEX IF NOT EXISTS expenses_cost_center_idx "
     "ON expenses (cost_center_id)"),
    ("expenses_approver_idx",
     "CREATE INDEX IF NOT EXISTS expenses_approver_idx "
     "ON expenses (approved_by_employee_ref)"),
    ("expenses_expense_date_idx",
     "CREATE INDEX IF NOT EXISTS expenses_expense_date_idx "
     "ON expenses (expense_date)"),
    ("expenses_status_idx",
     "CREATE INDEX IF NOT EXISTS expenses_status_idx "
     "ON expenses (expense_status)"),
    ("expenses_updated_at_idx",
     "CREATE INDEX IF NOT EXISTS expenses_updated_at_idx "
     "ON expenses (updated_at)"),

    # -- commissions ---------------------------------------------------------
    ("commissions_employee_idx",
     "CREATE INDEX IF NOT EXISTS commissions_employee_idx "
     "ON commissions (employee_ref)"),
    ("commissions_cost_center_idx",
     "CREATE INDEX IF NOT EXISTS commissions_cost_center_idx "
     "ON commissions (cost_center_id)"),
    ("commissions_validator_idx",
     "CREATE INDEX IF NOT EXISTS commissions_validator_idx "
     "ON commissions (validated_by_employee_ref)"),
    ("commissions_period_idx",
     "CREATE INDEX IF NOT EXISTS commissions_period_idx "
     "ON commissions (period_start, period_end)"),
    ("commissions_status_idx",
     "CREATE INDEX IF NOT EXISTS commissions_status_idx "
     "ON commissions (commission_status)"),
    ("commissions_updated_at_idx",
     "CREATE INDEX IF NOT EXISTS commissions_updated_at_idx "
     "ON commissions (updated_at)"),
]


# ===========================================================================
# 4. Schema creation
# ===========================================================================

def init_db():
    """Create the 9 ERP tables and their indexes. Creates no data."""

    database_url = get_database_url()

    try:
        connection = psycopg.connect(database_url)
    except psycopg.OperationalError as error:
        raise SystemExit(
            "Could not connect to PostgreSQL.\n"
            f"  {scrub(error, database_url)}\n"
            "Check that ERP_DATABASE_URL is correct and that your network can\n"
            "reach the host. Remember the ERP uses its OWN database: pointing\n"
            "this variable at the CRM one would defeat the separation."
        )

    try:
        with connection.cursor() as cursor:
            for table_name, create_statement in TABLES:
                cursor.execute(create_statement)
                print(f"  [ok] table   '{table_name}' ready")

            for index_name, create_statement in INDEXES:
                cursor.execute(create_statement)
                print(f"  [ok] index   '{index_name}' ready")

            # DDL is transactional in PostgreSQL: the nine tables and their
            # indexes are created together or not at all. Nothing above is
            # visible to anyone else until this commit.
            connection.commit()

            report(cursor)
    except psycopg.Error as error:
        connection.rollback()
        raise SystemExit(f"Schema creation failed: {scrub(error, database_url)}")
    finally:
        # "finally": the connection is closed even if an error occurs.
        connection.close()


def report(cursor):
    """Read the schema back from the catalogue and print what exists."""

    # Server identity, taken from the server itself rather than from the
    # connection string, so nothing sensitive is displayed.
    cursor.execute("SELECT current_database(), version()")
    database_name, version = cursor.fetchone()
    server_version = version.split(",")[0]

    cursor.execute(
        """
        SELECT table_name, COUNT(*)
        FROM information_schema.columns
        WHERE table_schema = 'public' AND table_name = ANY(%s)
        GROUP BY table_name
        ORDER BY table_name
        """,
        (TABLE_NAMES,),
    )
    columns_per_table = dict(cursor.fetchall())

    cursor.execute(
        """
        SELECT constraint_type, COUNT(*)
        FROM information_schema.table_constraints
        WHERE table_schema = 'public'
          AND table_name = ANY(%s)
          AND constraint_type IN ('FOREIGN KEY', 'PRIMARY KEY', 'UNIQUE')
        GROUP BY constraint_type
        """,
        (TABLE_NAMES,),
    )
    constraint_counts = dict(cursor.fetchall())

    # CHECK constraints are counted from pg_constraint rather than
    # information_schema: the latter also lists the implicit NOT NULL checks,
    # which would inflate the number several times over.
    cursor.execute(
        """
        SELECT COUNT(*)
        FROM pg_constraint c
        JOIN pg_class t ON t.oid = c.conrelid
        JOIN pg_namespace n ON n.oid = t.relnamespace
        WHERE n.nspname = 'public'
          AND c.contype = 'c'
          AND t.relname = ANY(%s)
        """,
        (TABLE_NAMES,),
    )
    check_constraints = cursor.fetchone()[0]

    cursor.execute(
        """
        SELECT COUNT(*)
        FROM pg_indexes
        WHERE schemaname = 'public' AND tablename = ANY(%s)
        """,
        (TABLE_NAMES,),
    )
    index_count = cursor.fetchone()[0]

    print()
    print("Orialis ERP schema initialised successfully.")
    print(f"Server   : {server_version}")
    print(f"Database : {database_name} (schema: public)")
    print(f"Tables   : {len(columns_per_table)} of {len(TABLES)} expected")
    for table_name in TABLE_NAMES:
        print(f"    {table_name:22} {columns_per_table.get(table_name, 0):>2} columns")
    print(f"Primary keys  : {constraint_counts.get('PRIMARY KEY', 0)}")
    print(f"Foreign keys  : {constraint_counts.get('FOREIGN KEY', 0)}")
    print(f"Unique keys   : {constraint_counts.get('UNIQUE', 0)}")
    print(f"Check constr. : {check_constraints}")
    print(f"Indexes       : {index_count}")


if __name__ == "__main__":
    init_db()

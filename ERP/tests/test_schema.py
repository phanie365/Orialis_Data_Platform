"""
Automated verification of the Orialis ERP schema.

    python ERP/tests/test_schema.py

Exit code 0 when everything passes, 1 otherwise - so it drops into CI as is.

---------------------------------------------------------------------------
WHAT THIS TESTS, AND WHY IT IS NOT A FORMALITY
---------------------------------------------------------------------------

A schema is not validated by reading it. `CREATE TABLE` succeeding proves that
the DDL parses, nothing more: a CHECK with a subtly inverted condition, a
foreign key pointing at the wrong column, a unique index that is not actually
partial - all of those create a schema that builds perfectly and guarantees
nothing.

The only validation that means anything is behavioural:

    does the database REFUSE what it is supposed to refuse,
    and ACCEPT what it is supposed to accept?

So this file is mostly a list of deliberately invalid rows. Each one names the
constraint that should reject it, and the run fails if a DIFFERENT constraint
fires - because a scenario rejected for the wrong reason is a scenario that
tests nothing.

---------------------------------------------------------------------------
THE FOUR THINGS BEING PROVEN
---------------------------------------------------------------------------

1. STRUCTURE. The nine tables exist with the expected shape, and the
   constraints the specification calls load-bearing exist BY NAME - not merely
   "some constraints exist".

2. THE TWO DOCTRINES. Every foreign key is ON DELETE RESTRICT and no CASCADE
   exists anywhere ("the ERP never deletes"); everything decidable per row is
   enforced by PostgreSQL ("the constraint doctrine").

3. THE BUSINESS CYCLES. Invalid invoice, payment, expense and commission
   states are refused - including the ones that matter most: paying an
   unapproved invoice, approving one's own expense claim, booking the same
   supplier invoice twice.

4. THE DELIBERATE LIMITS. A handful of scenarios assert that PostgreSQL
   ACCEPTS rows that are business-invalid, because the invariants they break
   are multi-row and belong to the future application layer. Those tests look
   backwards and are the most important ones in the file: they are what stops
   a later reader from "fixing" a gap that is a documented decision.

---------------------------------------------------------------------------
SAFETY: NOTHING IS EVER WRITTEN
---------------------------------------------------------------------------

The whole run happens inside ONE transaction that is always rolled back, and
each scenario additionally runs inside its own SAVEPOINT. There is no COMMIT
anywhere in this file.

Three further precautions, because "it rolls back" is a promise that should be
verifiable rather than trusted:

    - every test row carries a TEST- prefix (and ZZ- codes for the unique
      columns), so a row that somehow survived would be obvious
    - row counts are captured before the run
    - they are re-read AFTER the rollback, ON A NEW CONNECTION, and compared

That last check is the one that actually proves the promise. It means this
suite is safe to run against a populated ERP database, not only an empty one.

Requires: ERP_DATABASE_URL, and a schema already created by
`python ERP/scripts/init_db.py`.
"""

import sys
from pathlib import Path

import psycopg

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
from ERP.config import get_database_url, scrub  # noqa: E402


# ===========================================================================
# Row builders
# ===========================================================================
# Each builder returns a complete, VALID row, with every column named. A
# scenario then overrides only the field it wants to break:
#
#     invoice("TEST-INV-X", invoice_approval_status="Draft",
#                           invoice_payment_status="Paid")
#
# Naming the columns rather than relying on positional VALUES is what keeps
# this file readable, and what stops it from silently testing the wrong thing
# the day a column is added to the schema.

TS = "2026-01-15T10:00:00+00:00"
TS_LATER = "2026-01-16T10:00:00+00:00"


def _insert(table, values):
    columns = ", ".join(values)
    placeholders = ", ".join(["%s"] * len(values))
    return (f"INSERT INTO {table} ({columns}) VALUES ({placeholders})",
            tuple(values.values()))


def cost_center(cost_center_id, **overrides):
    values = {
        "cost_center_id": cost_center_id,
        "cost_center_name": "Test Cost Centre",
        "cost_center_type": "Branch",
        "office_code": "ZZ-TST-01",
        "country_code": "FR",
        "default_currency": "EUR",
        "parent_cost_center_id": "TEST-CC-FR",
        "hierarchy_level": 3,
        "cost_center_status": "Active",
        "opened_on": "2020-01-01",
        "closed_on": None,
        "created_at": TS,
        "updated_at": TS,
    }
    values.update(overrides)
    return _insert("cost_centers", values)


def supplier(supplier_id, **overrides):
    values = {
        "supplier_id": supplier_id,
        "supplier_name": "Test Supplier",
        "supplier_legal_name": "Test Supplier SA",
        "supplier_category": "IT & Software",
        "country_code": "FR",
        "vat_number": None,
        "iban_masked": "FR76**********3021",
        "default_currency": "EUR",
        "payment_terms_days": 30,
        "supplier_status": "Active",
        "onboarded_on": "2023-01-01",
        "deactivated_on": None,
        "created_at": TS,
        "updated_at": TS,
    }
    values.update(overrides)
    return _insert("suppliers", values)


def employee(employee_ref, **overrides):
    values = {
        "employee_ref": employee_ref,
        "first_name": "Test",
        "last_name": "Person",
        "work_email": None,
        "employee_type": "Back Office",
        "cost_center_id": "TEST-CC-BR",
        "office_code": "ZZ-TST-01",
        "country_code": "FR",
        "hire_date": "2020-01-01",
        "departure_date": None,
        "is_commission_eligible": False,
        "employee_status": "Active",
        "created_at": TS,
        "updated_at": TS,
    }
    values.update(overrides)
    return _insert("employees", values)


def invoice(invoice_id, **overrides):
    """A valid EUR invoice of TEST-SUP-1: 1000 net + 20% VAT = 1200 gross."""
    values = {
        "invoice_id": invoice_id,
        "supplier_id": "TEST-SUP-1",
        "supplier_invoice_number": invoice_id,
        "cost_center_id": "TEST-CC-BR",
        "invoice_type": "Standard",
        "invoice_date": "2026-01-05",
        "received_date": "2026-01-07",
        "due_date": "2026-02-04",
        "currency_code": "EUR",
        "net_amount": "1000.00",
        "tax_treatment": "Standard",
        "tax_rate": "20.00",
        "tax_amount": "200.00",
        "gross_amount": "1200.00",
        "fx_rate_to_eur": "1.000000",
        "gross_amount_eur": "1200.00",
        "paid_amount": "0.00",
        "invoice_approval_status": "Approved",
        "invoice_payment_status": "Unpaid",
        "approved_by_employee_ref": "TEST-EMP-2",
        "approved_at": TS,
        "rejection_reason": None,
        "created_at": TS,
        "updated_at": TS,
    }
    values.update(overrides)
    return _insert("supplier_invoices", values)


def payment(payment_id, **overrides):
    values = {
        "payment_id": payment_id,
        "supplier_id": "TEST-SUP-1",
        "payment_date": "2026-02-03",
        "value_date": None,
        "currency_code": "EUR",
        "payment_amount": "1200.00",
        "payment_method": "SEPA Credit Transfer",
        "payment_status": "Executed",
        "bank_reference": f"ZZ-{payment_id}",
        "executed_at": TS,
        "failure_reason": None,
        "created_at": TS,
        "updated_at": TS,
    }
    values.update(overrides)
    return _insert("payments", values)


def allocation(allocation_id, **overrides):
    values = {
        "allocation_id": allocation_id,
        "payment_id": "TEST-PAY-EUR",
        "invoice_id": "TEST-INV-EUR",
        "supplier_id": "TEST-SUP-1",
        "currency_code": "EUR",
        "allocated_amount": "1200.00",
        "allocation_status": "Active",
        "cancelled_at": None,
        "cancellation_reason": None,
        "replaced_by_allocation_id": None,
        "created_at": TS,
        "updated_at": TS,
    }
    values.update(overrides)
    return _insert("payment_allocations", values)


def expense(expense_id, **overrides):
    values = {
        "expense_id": expense_id,
        "employee_ref": "TEST-EMP-1",
        "cost_center_id": "TEST-CC-BR",
        "expense_category": "Travel",
        "expense_date": "2026-01-10",
        "submitted_date": "2026-01-12",
        "currency_code": "EUR",
        "net_amount": "100.00",
        "tax_rate": "20.00",
        "tax_amount": "20.00",
        "gross_amount": "120.00",
        "fx_rate_to_eur": "1.000000",
        "gross_amount_eur": "120.00",
        "expense_status": "Approved",
        "approved_by_employee_ref": "TEST-EMP-2",
        "approved_at": TS,
        "rejection_reason": None,
        "reimbursed_on": None,
        "reimbursement_reference": None,
        "receipt_reference": "ZZ-REC-1",
        "created_at": TS,
        "updated_at": TS,
    }
    values.update(overrides)
    return _insert("expenses", values)


def commission(commission_id, **overrides):
    values = {
        "commission_id": commission_id,
        "employee_ref": "TEST-EMP-1",
        "cost_center_id": "TEST-CC-BR",
        "period_type": "Monthly",
        "period_start": "2026-01-01",
        "period_end": "2026-01-31",
        "commission_type": "Recurring Management Fee",
        "basis_amount": "50000.00",
        "commission_rate": "0.0150",
        "currency_code": "EUR",
        "commission_amount": "750.00",
        "fx_rate_to_eur": "1.000000",
        "commission_amount_eur": "750.00",
        "commission_status": "Validated",
        "calculated_at": TS,
        "validated_at": TS,
        "validated_by_employee_ref": "TEST-EMP-2",
        "paid_on": None,
        "payroll_reference": None,
        "cancellation_reason": None,
        "source_system": "CRM",
        "created_at": TS,
        "updated_at": TS,
    }
    values.update(overrides)
    return _insert("commissions", values)


def fx_rate(rate_month, **overrides):
    values = {
        "rate_month": rate_month,
        "from_currency": "CHF",
        "to_currency": "EUR",
        "rate": "0.940000",
        "created_at": TS,
    }
    values.update(overrides)
    return _insert("fx_rates", values)


def sql(statement, *params):
    """A raw statement, for the cases no builder covers (UPDATE, DELETE)."""
    return (statement, params)


# ===========================================================================
# The fixtures every scenario builds on
# ===========================================================================
# A minimal but complete world: a cost-centre hierarchy, two suppliers in two
# currencies, two employees (one an advisor, one an approver - self-approval
# is forbidden, so a second person is structurally necessary), two invoices
# and two payments.
#
# Every identifier is TEST- prefixed, and every value that lands in a UNIQUE
# column (office_code, vat_number, bank_reference, work_email) is ZZ- or
# TEST- prefixed, so this suite cannot collide with real data.

FIXTURES = [
    cost_center("TEST-CC-GRP", cost_center_name="Test Group",
                cost_center_type="Group", office_code=None, country_code=None,
                parent_cost_center_id=None, hierarchy_level=1),
    cost_center("TEST-CC-FR", cost_center_name="Test France",
                cost_center_type="Country", office_code=None,
                parent_cost_center_id="TEST-CC-GRP", hierarchy_level=2),
    cost_center("TEST-CC-CH", cost_center_name="Test Switzerland",
                cost_center_type="Country", office_code=None,
                country_code="CH", default_currency="CHF",
                parent_cost_center_id="TEST-CC-GRP", hierarchy_level=2),
    cost_center("TEST-CC-BR"),

    supplier("TEST-SUP-1", vat_number="ZZTEST000000001"),
    supplier("TEST-SUP-2", country_code="CH", default_currency="CHF",
             supplier_category="Market Data", vat_number="ZZTEST000000002",
             iban_masked="CH93**********2730", payment_terms_days=45),

    employee("TEST-EMP-1", first_name="Sophie", last_name="Vidal",
             work_email="zz.test.advisor@example.invalid",
             employee_type="Advisor", is_commission_eligible=True),
    employee("TEST-EMP-2", first_name="Marc", last_name="Dubois",
             work_email="zz.test.manager@example.invalid",
             employee_type="Management"),

    invoice("TEST-INV-EUR"),
    invoice("TEST-INV-CHF", supplier_id="TEST-SUP-2", currency_code="CHF",
            tax_rate="8.10", tax_amount="81.00", gross_amount="1081.00",
            fx_rate_to_eur="0.940000", gross_amount_eur="1016.14",
            due_date="2026-02-19"),

    payment("TEST-PAY-EUR"),
    payment("TEST-PAY-CHF", supplier_id="TEST-SUP-2", currency_code="CHF",
            payment_amount="1081.00", payment_method="SWIFT"),
]


# ===========================================================================
# Structural checks - read from the catalogue
# ===========================================================================
# Deliberately NOT "count all the CHECK constraints and compare to 108". Such
# a test has to be edited every time a constraint is legitimately added, which
# trains everyone to bump the number without reading. It also passes happily
# if a critical constraint is replaced by a trivial one.
#
# Instead: the load-bearing constraints are asserted BY NAME, and the counts
# that are genuine invariants of the doctrine (zero CASCADE, every foreign key
# RESTRICT) are asserted exactly.

TABLES = [
    ("cost_centers", 13), ("suppliers", 14), ("employees", 14),
    ("supplier_invoices", 24), ("payments", 13), ("payment_allocations", 12),
    ("expenses", 22), ("commissions", 23), ("fx_rates", 5),
]

TABLE_NAMES = [name for name, _ in TABLES]

# Constraints the specification calls load-bearing. If one of these disappears
# or is renamed, the schema silently stops guaranteeing something, and no
# behavioural test would necessarily catch it.
REQUIRED_CONSTRAINTS = [
    # the internal control of the purchase cycle
    "supplier_invoices_pay_requires_approval_chk",
    # the two status columns must agree with the amounts
    "supplier_invoices_payment_status_coherence_chk",
    "supplier_invoices_paid_bounds_chk",
    # duplicate booking control
    "supplier_invoices_number_uq",
    # the amount identity
    "supplier_invoices_amount_identity_chk",
    "supplier_invoices_sign_chk",
    # masking enforced by the database, not by habit
    "suppliers_iban_masked_chk",
    # segregation of duties
    "expenses_self_approval_chk",
    "commissions_self_validation_chk",
    # idempotent commission runs
    "commissions_period_uq",
    # the composite foreign keys
    "payment_allocations_invoice_fk",
    "payment_allocations_payment_fk",
    # cancellation carries a reason
    "payment_allocations_cancel_reason_chk",
]


def structural_checks(cursor):
    """Yield (label, ok, detail) for each catalogue assertion."""

    # -- the nine tables, with their expected shape -------------------------
    cursor.execute(
        """
        SELECT table_name, COUNT(*)
        FROM information_schema.columns
        WHERE table_schema = 'public' AND table_name = ANY(%s)
        GROUP BY table_name
        """,
        (TABLE_NAMES,),
    )
    actual = dict(cursor.fetchall())
    for name, expected_columns in TABLES:
        found = actual.get(name)
        yield (f"table '{name}' : {expected_columns} colonnes",
               found == expected_columns,
               f"absente" if found is None else f"{found} colonnes")

    # -- primary keys -------------------------------------------------------
    cursor.execute(
        """
        SELECT COUNT(*) FROM pg_constraint c
        JOIN pg_class t ON t.oid = c.conrelid
        WHERE c.contype = 'p' AND t.relname = ANY(%s)
        """,
        (TABLE_NAMES,),
    )
    count = cursor.fetchone()[0]
    yield ("9 cles primaires", count == 9, f"{count}")

    # -- THE DOCTRINE: the ERP never deletes --------------------------------
    cursor.execute(
        """
        SELECT COUNT(*) FILTER (WHERE confdeltype = 'r'),
               COUNT(*) FILTER (WHERE confdeltype = 'c'),
               COUNT(*)
        FROM pg_constraint c
        JOIN pg_class t ON t.oid = c.conrelid
        WHERE c.contype = 'f' AND t.relname = ANY(%s)
        """,
        (TABLE_NAMES,),
    )
    restrict_count, cascade_count, total = cursor.fetchone()
    yield ("15 cles etrangeres", total == 15, f"{total}")
    yield ("toutes les FK sont ON DELETE RESTRICT",
           restrict_count == total and total > 0,
           f"{restrict_count}/{total}")
    yield ("aucun ON DELETE CASCADE", cascade_count == 0, f"{cascade_count}")

    # -- unique constraints -------------------------------------------------
    cursor.execute(
        """
        SELECT COUNT(*) FROM pg_constraint c
        JOIN pg_class t ON t.oid = c.conrelid
        WHERE c.contype = 'u' AND t.relname = ANY(%s)
        """,
        (TABLE_NAMES,),
    )
    count = cursor.fetchone()[0]
    yield ("8 contraintes d'unicite", count == 8, f"{count}")

    # -- CHECK constraints: a floor, not an exact number --------------------
    # Counted from pg_constraint rather than information_schema, which also
    # lists the implicit NOT NULL checks and would inflate this several times.
    cursor.execute(
        """
        SELECT COUNT(*) FROM pg_constraint c
        JOIN pg_class t ON t.oid = c.conrelid
        WHERE c.contype = 'c' AND t.relname = ANY(%s)
        """,
        (TABLE_NAMES,),
    )
    count = cursor.fetchone()[0]
    yield ("au moins 108 contraintes CHECK", count >= 108, f"{count}")

    # -- the load-bearing constraints, by name ------------------------------
    cursor.execute(
        """
        SELECT conname FROM pg_constraint c
        JOIN pg_class t ON t.oid = c.conrelid
        WHERE t.relname = ANY(%s)
        """,
        (TABLE_NAMES,),
    )
    present = {row[0] for row in cursor.fetchall()}
    for name in REQUIRED_CONSTRAINTS:
        yield (f"contrainte '{name}' presente", name in present,
               "absente" if name not in present else "ok")

    # -- the composite foreign keys are really composite --------------------
    cursor.execute(
        """
        SELECT conname, array_length(conkey, 1)
        FROM pg_constraint c
        JOIN pg_class t ON t.oid = c.conrelid
        WHERE c.contype = 'f' AND t.relname = 'payment_allocations'
          AND array_length(conkey, 1) > 1
        ORDER BY conname
        """
    )
    composite = dict(cursor.fetchall())
    for name in ("payment_allocations_invoice_fk", "payment_allocations_payment_fk"):
        yield (f"FK composite '{name}' sur 3 colonnes",
               composite.get(name) == 3,
               f"{composite.get(name)} colonne(s)")

    # -- the partial unique index -------------------------------------------
    # Three separate facts: it exists, it is UNIQUE, and it is PARTIAL. An
    # index that lost its WHERE clause would still be unique - and would make
    # the cancel-and-replace cycle impossible, which is the whole reason it
    # exists.
    cursor.execute(
        "SELECT indexdef FROM pg_indexes "
        "WHERE schemaname = 'public' AND indexname = 'payment_allocations_active_uq'"
    )
    row = cursor.fetchone()
    definition = row[0] if row else ""
    yield ("index 'payment_allocations_active_uq' present", bool(row),
           "absent" if not row else "ok")
    yield ("  ... il est UNIQUE", "CREATE UNIQUE INDEX" in definition,
           "non unique" if row else "n/a")
    yield ("  ... il est PARTIEL (WHERE allocation_status = 'Active')",
           "WHERE (allocation_status = 'Active'" in definition,
           "clause WHERE absente" if row else "n/a")


# ===========================================================================
# Behavioural scenarios
# ===========================================================================
# (group, label, statements, expectation, expected constraint)
#
# `expectation` is REJECT or ACCEPT. For a REJECT, the expected constraint name
# is asserted too: a row refused by the WRONG rule is a scenario that proves
# nothing, and that is a failure worth seeing.
#
# A tuple of names is accepted where more than one constraint could legitimately
# fire first - PostgreSQL does not promise an order between two foreign keys
# violated by the same row.

REJECT, ACCEPT = "REJECT", "ACCEPT"

SCENARIOS = [

    # -- ON DELETE RESTRICT: the ERP never deletes --------------------------
    ("RESTRICT", "supprimer un fournisseur portant des factures",
     [sql("DELETE FROM suppliers WHERE supplier_id = 'TEST-SUP-1'")],
     REJECT, ("supplier_invoices_supplier_fk", "payments_supplier_fk")),

    ("RESTRICT", "supprimer un centre de couts utilise",
     [sql("DELETE FROM cost_centers WHERE cost_center_id = 'TEST-CC-BR'")],
     REJECT, ("employees_cost_center_fk", "supplier_invoices_cost_center_fk")),

    ("RESTRICT", "supprimer un employe approbateur",
     [sql("DELETE FROM employees WHERE employee_ref = 'TEST-EMP-2'")],
     REJECT, "supplier_invoices_approver_fk"),

    ("RESTRICT", "supprimer un paiement portant une allocation (pas de CASCADE)",
     [allocation("TEST-ALC-1"),
      sql("DELETE FROM payments WHERE payment_id = 'TEST-PAY-EUR'")],
     REJECT, "payment_allocations_payment_fk"),

    ("RESTRICT", "supprimer un centre de couts parent",
     [sql("DELETE FROM cost_centers WHERE cost_center_id = 'TEST-CC-FR'")],
     REJECT, "cost_centers_parent_fk"),

    # -- Composite foreign keys: supplier and currency coherence ------------
    ("FK composite", "affecter un paiement CHF a une facture EUR",
     [allocation("TEST-ALC-X", payment_id="TEST-PAY-CHF",
                 invoice_id="TEST-INV-EUR", supplier_id="TEST-SUP-1",
                 currency_code="EUR", allocated_amount="1000.00")],
     REJECT, ("payment_allocations_payment_fk", "payment_allocations_invoice_fk")),

    ("FK composite", "payer la facture du fournisseur 2 avec le paiement du fournisseur 1",
     [allocation("TEST-ALC-X", payment_id="TEST-PAY-EUR",
                 invoice_id="TEST-INV-CHF", supplier_id="TEST-SUP-1",
                 currency_code="EUR")],
     REJECT, ("payment_allocations_invoice_fk", "payment_allocations_payment_fk")),

    ("FK composite", "mentir sur la devise portee par l'allocation",
     [allocation("TEST-ALC-X", payment_id="TEST-PAY-CHF",
                 invoice_id="TEST-INV-CHF", supplier_id="TEST-SUP-2",
                 currency_code="EUR")],
     REJECT, ("payment_allocations_invoice_fk", "payment_allocations_payment_fk")),

    ("FK composite", "allocation coherente en CHF",
     [allocation("TEST-ALC-X", payment_id="TEST-PAY-CHF",
                 invoice_id="TEST-INV-CHF", supplier_id="TEST-SUP-2",
                 currency_code="CHF", allocated_amount="1081.00")],
     ACCEPT, None),

    # -- The partial unique index, and the cancel-and-replace cycle ---------
    ("Index partiel", "deux allocations ACTIVE sur le meme couple",
     [allocation("TEST-ALC-1"),
      allocation("TEST-ALC-2", allocated_amount="600.00")],
     REJECT, "payment_allocations_active_uq"),

    ("Index partiel", "inserer le remplacement AVANT d'annuler l'ancien",
     [allocation("TEST-ALC-1", allocated_amount="600.00"),
      allocation("TEST-ALC-2")],
     REJECT, "payment_allocations_active_uq"),

    ("Index partiel", "annuler PUIS remplacer - le cycle de correction nominal",
     [allocation("TEST-ALC-1", allocated_amount="600.00"),
      sql("UPDATE payment_allocations SET allocation_status = 'Cancelled', "
          "cancelled_at = %s, cancellation_reason = 'Mis-matched', updated_at = %s "
          "WHERE allocation_id = 'TEST-ALC-1'", TS_LATER, TS_LATER),
      allocation("TEST-ALC-2", created_at=TS_LATER, updated_at=TS_LATER),
      sql("UPDATE payment_allocations SET replaced_by_allocation_id = 'TEST-ALC-2' "
          "WHERE allocation_id = 'TEST-ALC-1'")],
     ACCEPT, None),

    ("Index partiel", "empiler plusieurs allocations CANCELLED sur le meme couple",
     [allocation("TEST-ALC-1", allocation_status="Cancelled", cancelled_at=TS,
                 cancellation_reason="err1", allocated_amount="600.00"),
      allocation("TEST-ALC-2", allocation_status="Cancelled", cancelled_at=TS,
                 cancellation_reason="err2", allocated_amount="700.00"),
      allocation("TEST-ALC-3")],
     ACCEPT, None),

    ("Index partiel", "annuler sans motif",
     [allocation("TEST-ALC-X", allocation_status="Cancelled", cancelled_at=TS)],
     REJECT, "payment_allocations_cancel_reason_chk"),

    ("Index partiel", "declarer un remplacement sur une allocation encore ACTIVE",
     [allocation("TEST-ALC-1", allocation_status="Cancelled", cancelled_at=TS,
                 cancellation_reason="x"),
      allocation("TEST-ALC-2", payment_id="TEST-PAY-CHF",
                 invoice_id="TEST-INV-CHF", supplier_id="TEST-SUP-2",
                 currency_code="CHF", allocated_amount="1081.00",
                 replaced_by_allocation_id="TEST-ALC-1")],
     REJECT, "payment_allocations_replacement_chk"),

    ("Index partiel", "allocation de montant nul",
     [allocation("TEST-ALC-X", allocated_amount="0.00")],
     REJECT, "payment_allocations_amount_chk"),

    # -- Invoice cycle ------------------------------------------------------
    ("Facture", "payer une facture non approuvee",
     [invoice("TEST-INV-X", invoice_approval_status="Draft",
              invoice_payment_status="Paid", paid_amount="1200.00",
              approved_by_employee_ref=None, approved_at=None)],
     REJECT, "supplier_invoices_pay_requires_approval_chk"),

    ("Facture", "statut 'Paid' alors que paid_amount vaut 0",
     [invoice("TEST-INV-X", invoice_payment_status="Paid")],
     REJECT, "supplier_invoices_payment_status_coherence_chk"),

    ("Facture", "paid_amount superieur au montant TTC",
     [invoice("TEST-INV-X", invoice_payment_status="Partially Paid",
              paid_amount="5000.00")],
     REJECT, "supplier_invoices_paid_bounds_chk"),

    ("Facture", "gross <> net + tax",
     [invoice("TEST-INV-X", gross_amount="999.00", gross_amount_eur="999.00")],
     REJECT, "supplier_invoices_amount_identity_chk"),

    ("Facture", "facture Standard a montant negatif",
     [invoice("TEST-INV-X", net_amount="-1000.00", tax_amount="-200.00",
              gross_amount="-1200.00", gross_amount_eur="-1200.00")],
     REJECT, "supplier_invoices_sign_chk"),

    ("Facture", "avoir (Credit Note) a montant negatif",
     [invoice("TEST-INV-CN", invoice_type="Credit Note", net_amount="-1000.00",
              tax_amount="-200.00", gross_amount="-1200.00",
              gross_amount_eur="-1200.00")],
     ACCEPT, None),

    ("Facture", "avoir solde par une allocation negative",
     [invoice("TEST-INV-CN", invoice_type="Credit Note", net_amount="-1000.00",
              tax_amount="-200.00", gross_amount="-1200.00",
              gross_amount_eur="-1200.00", paid_amount="-1200.00",
              invoice_payment_status="Paid")],
     ACCEPT, None),

    ("Facture", "autoliquidation avec un taux de TVA non nul",
     [invoice("TEST-INV-X", tax_treatment="Reverse Charge")],
     REJECT, "supplier_invoices_tax_zero_chk"),

    ("Facture", "facture EUR avec un taux de change different de 1",
     [invoice("TEST-INV-X", fx_rate_to_eur="0.940000")],
     REJECT, "supplier_invoices_fx_eur_chk"),

    ("Facture", "echeance anterieure a la date de facture",
     [invoice("TEST-INV-X", due_date="2025-12-01")],
     REJECT, "supplier_invoices_due_chk"),

    ("Facture", "reception anterieure a la date de facture",
     [invoice("TEST-INV-X", received_date="2025-12-01")],
     REJECT, "supplier_invoices_received_chk"),

    ("Facture", "meme numero de facture chez le meme fournisseur",
     [invoice("TEST-INV-X", supplier_invoice_number="TEST-INV-EUR")],
     REJECT, "supplier_invoices_number_uq"),

    ("Facture", "meme numero chez un fournisseur different",
     [invoice("TEST-INV-X", supplier_id="TEST-SUP-2", currency_code="CHF",
              supplier_invoice_number="TEST-INV-EUR", tax_rate="8.10",
              tax_amount="81.00", gross_amount="1081.00",
              fx_rate_to_eur="0.940000", gross_amount_eur="1016.14")],
     ACCEPT, None),

    ("Facture", "approbation sans approbateur",
     [invoice("TEST-INV-X", approved_by_employee_ref=None)],
     REJECT, "supplier_invoices_approver_chk"),

    ("Facture", "rejet sans motif",
     [invoice("TEST-INV-X", invoice_approval_status="Rejected",
              approved_by_employee_ref=None, approved_at=None)],
     REJECT, "supplier_invoices_rejected_chk"),

    ("Facture", "approbateur inexistant",
     [invoice("TEST-INV-X", approved_by_employee_ref="TEST-EMP-GHOST")],
     REJECT, "supplier_invoices_approver_fk"),

    # -- Payment cycle ------------------------------------------------------
    ("Paiement", "statut 'Executed' sans executed_at",
     [payment("TEST-PAY-X", executed_at=None)],
     REJECT, "payments_executed_chk"),

    ("Paiement", "statut 'Failed' sans motif",
     [payment("TEST-PAY-X", payment_status="Failed", executed_at=None)],
     REJECT, "payments_failed_chk"),

    ("Paiement", "montant negatif",
     [payment("TEST-PAY-X", payment_amount="-100.00",
              payment_status="Initiated", executed_at=None)],
     REJECT, "payments_amount_chk"),

    ("Paiement", "date de valeur anterieure a la date d'ordre",
     [payment("TEST-PAY-X", value_date="2026-01-01")],
     REJECT, "payments_value_date_chk"),

    # -- Supplier reference data --------------------------------------------
    ("Fournisseur", "IBAN complet, non masque",
     [supplier("TEST-SUP-X", iban_masked="FR7630006000011234567890189")],
     REJECT, "suppliers_iban_masked_chk"),

    ("Fournisseur", "IBAN correctement masque",
     [supplier("TEST-SUP-X", iban_masked="FR76**********0189")],
     ACCEPT, None),

    ("Fournisseur", "fournisseur suisse facturant en EUR",
     [supplier("TEST-SUP-X", country_code="CH", default_currency="EUR")],
     REJECT, "suppliers_chf_chk"),

    ("Fournisseur", "fournisseur americain portant un numero de TVA UE",
     [supplier("TEST-SUP-X", country_code="US", vat_number="ZZTESTUS0001")],
     REJECT, "suppliers_vat_scope_chk"),

    ("Fournisseur", "statut 'Inactive' sans date de desactivation",
     [supplier("TEST-SUP-X", supplier_status="Inactive")],
     REJECT, "suppliers_deactivated_chk"),

    ("Fournisseur", "delai de paiement hors bareme (37 jours)",
     [supplier("TEST-SUP-X", payment_terms_days=37)],
     REJECT, "suppliers_terms_chk"),

    ("Fournisseur", "categorie inconnue",
     [supplier("TEST-SUP-X", supplier_category="Consulting")],
     REJECT, "suppliers_category_chk"),

    # -- Cost centres -------------------------------------------------------
    ("Centre de couts", "centre suisse libelle en EUR",
     [cost_center("TEST-CC-X", country_code="CH", default_currency="EUR",
                  office_code="ZZ-TST-02", parent_cost_center_id="TEST-CC-CH")],
     REJECT, "cost_centers_currency_country_chk"),

    ("Centre de couts", "centre de type 'Function' portant un office_code",
     [cost_center("TEST-CC-X", cost_center_type="Function",
                  office_code="ZZ-TST-02")],
     REJECT, "cost_centers_office_chk"),

    ("Centre de couts", "niveau hierarchique incoherent avec le type",
     [cost_center("TEST-CC-X", office_code="ZZ-TST-02", hierarchy_level=2)],
     REJECT, "cost_centers_level_chk"),

    ("Centre de couts", "centre declare son propre parent",
     [cost_center("TEST-CC-X", office_code="ZZ-TST-02",
                  parent_cost_center_id="TEST-CC-X")],
     REJECT, "cost_centers_self_parent_chk"),

    ("Centre de couts", "office_code deja utilise",
     [cost_center("TEST-CC-X", office_code="ZZ-TST-01")],
     REJECT, "cost_centers_office_code_uq"),

    ("Centre de couts", "statut 'Closed' sans date de fermeture",
     [cost_center("TEST-CC-X", office_code="ZZ-TST-02",
                  cost_center_status="Closed")],
     REJECT, "cost_centers_closed_chk"),

    # -- Employees ----------------------------------------------------------
    ("Employe", "non-conseiller declare eligible aux commissions",
     [employee("TEST-EMP-X", employee_type="IT", is_commission_eligible=True)],
     REJECT, "employees_commission_eligible_chk"),

    ("Employe", "statut 'Inactive' sans date de depart",
     [employee("TEST-EMP-X", employee_status="Inactive")],
     REJECT, "employees_departure_chk"),

    ("Employe", "depart anterieur a l'embauche",
     [employee("TEST-EMP-X", employee_status="Inactive",
               departure_date="2019-01-01")],
     REJECT, "employees_departure_order_chk"),

    ("Employe", "sans email - la friction de rapprochement est autorisee",
     [employee("TEST-EMP-X", work_email=None)],
     ACCEPT, None),

    # -- Expenses -----------------------------------------------------------
    ("Depense", "auto-validation d'une note de frais",
     [expense("TEST-EXP-X", approved_by_employee_ref="TEST-EMP-1")],
     REJECT, "expenses_self_approval_chk"),

    ("Depense", "validation par un tiers",
     [expense("TEST-EXP-X")],
     ACCEPT, None),

    ("Depense", "statut 'Reimbursed' sans date de remboursement",
     [expense("TEST-EXP-X", expense_status="Reimbursed")],
     REJECT, "expenses_reimbursed_chk"),

    ("Depense", "statut 'Submitted' sans date de soumission",
     [expense("TEST-EXP-X", expense_status="Submitted", submitted_date=None,
              approved_by_employee_ref=None, approved_at=None)],
     REJECT, "expenses_submitted_chk"),

    ("Depense", "montant negatif",
     [expense("TEST-EXP-X", net_amount="-100.00", tax_amount="-20.00",
              gross_amount="-120.00", gross_amount_eur="-120.00")],
     REJECT, "expenses_amounts_chk"),

    ("Depense", "soumission anterieure a l'engagement",
     [expense("TEST-EXP-X", submitted_date="2026-01-01")],
     REJECT, "expenses_submitted_order_chk"),

    # -- Commissions --------------------------------------------------------
    ("Commission", "commission valide",
     [commission("TEST-COM-1")],
     ACCEPT, None),

    ("Commission", "doublon sur (employe, type, periode)",
     [commission("TEST-COM-1"),
      commission("TEST-COM-2")],
     REJECT, "commissions_period_uq"),

    ("Commission", "meme periode, type de commission different",
     [commission("TEST-COM-1"),
      commission("TEST-COM-2", commission_type="New Business")],
     ACCEPT, None),

    ("Commission", "auto-validation",
     [commission("TEST-COM-1", validated_by_employee_ref="TEST-EMP-1")],
     REJECT, "commissions_self_validation_chk"),

    ("Commission", "taux superieur a 1",
     [commission("TEST-COM-1", commission_rate="1.5000")],
     REJECT, "commissions_rate_chk"),

    ("Commission", "statut 'Paid' sans date de paiement",
     [commission("TEST-COM-1", commission_status="Paid")],
     REJECT, "commissions_paid_chk"),

    ("Commission", "periode inversee",
     [commission("TEST-COM-1", period_start="2026-01-31",
                 period_end="2026-01-01")],
     REJECT, "commissions_period_order_chk"),

    # -- FX rates -----------------------------------------------------------
    ("Taux de change", "taux mensuel date du 1er du mois",
     [fx_rate("2030-06-01")],
     ACCEPT, None),

    ("Taux de change", "taux date du 15 du mois",
     [fx_rate("2030-06-15")],
     REJECT, "fx_rates_month_chk"),

    ("Taux de change", "paire de devises identique",
     [fx_rate("2030-06-01", from_currency="EUR", to_currency="EUR",
              rate="1.000000")],
     REJECT, "fx_rates_pair_chk"),

    ("Taux de change", "taux negatif",
     [fx_rate("2030-06-01", rate="-0.940000")],
     REJECT, "fx_rates_rate_chk"),

    # -- Timestamps ---------------------------------------------------------
    ("Horodatage", "updated_at anterieur a created_at",
     [supplier("TEST-SUP-X", created_at=TS_LATER, updated_at=TS)],
     REJECT, "suppliers_timestamps_chk"),
]


# ===========================================================================
# The deliberate limits
# ===========================================================================
# THESE TESTS LOOK BACKWARDS ON PURPOSE, AND THEY ARE THE MOST IMPORTANT ONES
# IN THIS FILE.
#
# Each asserts that PostgreSQL ACCEPTS a row that is business-invalid. That is
# not a gap to be fixed: the invariant each one breaks is MULTI-ROW, and the
# validated doctrine puts multi-row invariants in the application layer, to be
# checked inside the transaction before COMMIT.
#
# Without these tests, a future reader would eventually "harden" one of them
# with a trigger, and would be undoing a decision rather than fixing a bug.
# With them, the attempt turns a green suite red and the comment explains why.
#
# They are also the specification of what the seed and the simulator must
# check themselves.

DELIBERATE_LIMITS = [
    ("paid_amount peut mentir sur la somme des allocations",
     "L'invariant paid_amount = SUM(allocations Active de paiements Executed) "
     "porte sur une autre table. Un CHECK ne peut pas l'exprimer.",
     [allocation("TEST-ALC-1")]),  # invoice.paid_amount reste a 0

    ("un paiement peut etre sur-alloue",
     "SUM(allocations) <= payment_amount est une somme multi-lignes. "
     "Ici 1200 + 1200 sont alloues sur un paiement de 1200.",
     [invoice("TEST-INV-B"),
      allocation("TEST-ALC-1"),
      allocation("TEST-ALC-2", invoice_id="TEST-INV-B")]),

    ("une allocation peut avoir le signe oppose a sa facture",
     "Le signe de reference est porte par une autre ligne (la facture). "
     "Seule la couche applicative peut comparer les deux.",
     [allocation("TEST-ALC-1", allocated_amount="-500.00")]),

    ("une allocation Active peut viser une facture rejetee",
     "Le statut d'approbation est sur une autre ligne. La FK garantit que la "
     "facture existe, pas qu'elle soit dans un etat payable.",
     [sql("UPDATE supplier_invoices SET invoice_approval_status = 'Rejected', "
          "approved_at = NULL, approved_by_employee_ref = NULL, "
          "rejection_reason = 'Litige' WHERE invoice_id = 'TEST-INV-EUR'"),
      allocation("TEST-ALC-1")]),

    ("une facture peut etre creee pour un fournisseur bloque",
     "Le statut fournisseur est sur une autre ligne, et la regle est de plus "
     "temporelle (les factures anterieures au blocage restent legitimes).",
     [sql("UPDATE suppliers SET supplier_status = 'Blocked', "
          "deactivated_on = '2026-01-01' WHERE supplier_id = 'TEST-SUP-1'"),
      invoice("TEST-INV-X")]),

    ("une commission peut viser un employe non eligible",
     "Volontairement NON impose par une FK composite : l'eligibilite change "
     "dans le temps, et un conseiller devenu ineligible en 2026 n'invalide "
     "pas les commissions acquises en 2024.",
     [sql("UPDATE employees SET is_commission_eligible = FALSE "
          "WHERE employee_ref = 'TEST-EMP-1'"),
      commission("TEST-COM-1")]),
]


# ===========================================================================
# Runner
# ===========================================================================

class Result:
    def __init__(self):
        self.passed = 0
        self.failed = 0
        self.failures = []

    def record(self, ok, group, label, detail=""):
        if ok:
            self.passed += 1
        else:
            self.failed += 1
            self.failures.append((group, label, detail))
        return ok


def run_statements(cursor, statements):
    """Execute statements inside a savepoint; return the error, or None.

    The savepoint is what keeps a rejected statement from poisoning the
    transaction: in PostgreSQL any error aborts the whole transaction unless
    it is rolled back to a savepoint, so without this every scenario after the
    first rejection would fail for the wrong reason.
    """
    cursor.execute("SAVEPOINT scenario")
    error = None
    try:
        for statement, params in statements:
            cursor.execute(statement, params)
    except psycopg.Error as caught:
        error = caught
    cursor.execute("ROLLBACK TO SAVEPOINT scenario")
    return error


def constraint_of(error):
    """The constraint or index name PostgreSQL blamed, when it named one."""
    name = getattr(getattr(error, "diag", None), "constraint_name", None)
    return name or "<non nomme>"


def main():
    database_url = get_database_url()

    try:
        connection = psycopg.connect(database_url, autocommit=False)
    except psycopg.OperationalError as error:
        raise SystemExit(
            "Impossible de se connecter a PostgreSQL.\n"
            f"  {scrub(error, database_url)}\n"
            "Verifie ERP_DATABASE_URL."
        )

    result = Result()
    cursor = connection.cursor()

    try:
        # -- the schema must exist before anything else means anything ------
        cursor.execute(
            "SELECT COUNT(*) FROM information_schema.tables "
            "WHERE table_schema = 'public' AND table_name = ANY(%s)",
            (TABLE_NAMES,),
        )
        if cursor.fetchone()[0] != len(TABLE_NAMES):
            raise SystemExit(
                "Le schema ERP est incomplet ou absent.\n"
                "Lance d'abord : python ERP/scripts/init_db.py"
            )

        # -- baseline row counts, to prove nothing persists -----------------
        baseline = row_counts(cursor)

        print("=" * 72)
        print("STRUCTURE")
        print("=" * 72)
        for label, ok, detail in structural_checks(cursor):
            result.record(ok, "STRUCTURE", label, detail)
            print(f"  [{'PASS' if ok else 'FAIL'}] {label}"
                  + ("" if ok else f"  -> {detail}"))

        # -- fixtures -------------------------------------------------------
        for statement, params in FIXTURES:
            cursor.execute(statement, params)

        # Counted AFTER the fixtures, not before. Inside the transaction the
        # fixtures are of course visible - comparing against the baseline here
        # would only prove that INSERT works. What is worth asserting is that
        # the counts are still exactly this at the end of the run: that proves
        # every scenario's SAVEPOINT rolled back cleanly and none of the ~70
        # deliberately invalid rows leaked past it.
        with_fixtures = row_counts(cursor)
        print(f"\n{len(FIXTURES)} lignes de reference inserees "
              f"(transaction annulee a la fin).")

        # -- behavioural scenarios ------------------------------------------
        current_group = None
        for group, label, statements, expectation, expected in SCENARIOS:
            if group != current_group:
                current_group = group
                print()
                print("=" * 72)
                print(group.upper())
                print("=" * 72)

            error = run_statements(cursor, statements)
            rejected = error is not None

            if expectation == REJECT:
                ok = rejected
                detail = "accepte alors qu'un rejet etait attendu"
                if ok and expected is not None:
                    blamed = constraint_of(error)
                    allowed = (expected,) if isinstance(expected, str) else expected
                    if blamed not in allowed:
                        ok = False
                        detail = (f"rejete par '{blamed}', attendu "
                                  f"{' ou '.join(allowed)}")
            else:
                ok = not rejected
                detail = (f"rejete par '{constraint_of(error)}' alors qu'une "
                          f"acceptation etait attendue") if rejected else ""

            result.record(ok, group, label, detail)
            marker = "PASS" if ok else "FAIL"
            suffix = ""
            if ok and expectation == REJECT and expected is not None:
                suffix = f"  ({constraint_of(error)})"
            print(f"  [{marker}] {label}{suffix}")
            if not ok:
                print(f"         -> {detail}")

        # -- the deliberate limits ------------------------------------------
        print()
        print("=" * 72)
        print("LIMITES VOLONTAIRES  (PostgreSQL DOIT accepter)")
        print("=" * 72)
        print("  Ces scenarios verifient que le schema n'impose PAS des")
        print("  invariants multi-lignes. Un echec ici signifie qu'une")
        print("  contrainte a ete ajoutee contre une decision validee.")
        print()
        for label, why, statements in DELIBERATE_LIMITS:
            error = run_statements(cursor, statements)
            ok = error is None
            detail = (f"rejete par '{constraint_of(error)}' - une contrainte "
                      f"multi-lignes a-t-elle ete ajoutee ?") if error else ""
            result.record(ok, "LIMITES", label, detail)
            print(f"  [{'PASS' if ok else 'FAIL'}] {label}")
            print(f"         {why}")
            if not ok:
                print(f"         -> {detail}")

        # -- every scenario savepoint rolled back cleanly --------------------
        after = row_counts(cursor)
        leaked = {table: after[table] - with_fixtures[table]
                  for table in TABLE_NAMES if after[table] != with_fixtures[table]}
        result.record(not leaked, "ISOLATION",
                      "aucun scenario n'a fui hors de son savepoint",
                      f"lignes en trop : {leaked}" if leaked else "")

    finally:
        # No COMMIT anywhere in this file. This is the only exit.
        connection.rollback()
        connection.close()

    # -- and checked again from a NEW connection ----------------------------
    # The rollback above is a promise; this is the proof. A fresh connection
    # sees only committed data, so if anything had leaked it would show here.
    verification = psycopg.connect(database_url)
    with verification.cursor() as verify_cursor:
        final = row_counts(verify_cursor)
    verification.close()

    print()
    print("=" * 72)
    print("ISOLATION")
    print("=" * 72)
    clean = final == baseline
    result.record(clean, "ISOLATION",
                  "aucune donnee de test persistee (nouvelle connexion)",
                  f"{baseline} -> {final}")
    print(f"  [{'PASS' if clean else 'FAIL'}] aucune donnee de test persistee")
    print(f"         lignes avant : {sum(baseline.values())} | "
          f"apres rollback, nouvelle connexion : {sum(final.values())}")
    if not clean:
        for table in TABLE_NAMES:
            if baseline[table] != final[table]:
                print(f"         {table}: {baseline[table]} -> {final[table]}")

    # -- summary ------------------------------------------------------------
    total = result.passed + result.failed
    print()
    print("=" * 72)
    print(f"RESULTAT : {result.passed} PASS / {result.failed} FAIL "
          f"sur {total} verifications")
    print("=" * 72)
    if result.failures:
        print()
        for group, label, detail in result.failures:
            print(f"  FAIL [{group}] {label}")
            if detail:
                print(f"       {detail}")

    return 1 if result.failed else 0


def row_counts(cursor):
    """Row count per ERP table, as a dict."""
    counts = {}
    for table in TABLE_NAMES:
        cursor.execute(f"SELECT COUNT(*) FROM {table}")
        counts[table] = cursor.fetchone()[0]
    return counts


if __name__ == "__main__":
    sys.exit(main())

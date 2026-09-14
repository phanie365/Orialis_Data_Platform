"""
The multi-row invariants, checked in SQL before the COMMIT.

---------------------------------------------------------------------------
WHY THESE LIVE HERE AND NOT IN THE SCHEMA
---------------------------------------------------------------------------

`ERP/tests/test_schema.py` contains six scenarios that assert PostgreSQL
ACCEPTS business-invalid rows. They are not gaps: each breaks an invariant
that spans several rows, and a CHECK constraint cannot see past the row it is
attached to.

This module is the other half of that decision. Everything the database was
deliberately not asked to guarantee is guaranteed here instead, inside the
same transaction, before anything becomes visible. A failure means ROLLBACK -
a half-loaded history is worse than none, because it looks usable.

It is the same pattern the CRM simulator already uses with its seven
pre-commit checks, applied to a larger surface.

Each check returns (label, failure_count, sample) and is expected to return
zero. They are written as SQL rather than Python because the rows are already
in the database at that point, and because a check that re-derives the answer
from the generator's own in-memory state would only prove the generator agrees
with itself.
"""

# Each entry: (label, sql). The query must return the OFFENDING rows; an empty
# result is a pass.
CHECKS = [

    # -- the settlement rule, restated as a query ---------------------------
    # paid_amount must equal the sum of Active allocations of Executed
    # payments. This is the single most important invariant in the ERP, and
    # the one no CHECK constraint can express.
    ("paid_amount = somme des allocations Active de paiements Executed", """
        SELECT i.invoice_id,
               i.paid_amount,
               COALESCE(a.total, 0) AS computed
        FROM supplier_invoices i
        LEFT JOIN (
            SELECT pa.invoice_id, SUM(pa.allocated_amount) AS total
            FROM payment_allocations pa
            JOIN payments p ON p.payment_id = pa.payment_id
            WHERE pa.allocation_status = 'Active'
              AND p.payment_status = 'Executed'
            GROUP BY pa.invoice_id
        ) a ON a.invoice_id = i.invoice_id
        WHERE i.paid_amount <> COALESCE(a.total, 0)
    """),

    # -- no payment is allocated beyond its own amount ----------------------
    ("aucune sur-allocation d'un paiement", """
        SELECT p.payment_id, p.payment_amount, SUM(pa.allocated_amount) AS allocated
        FROM payments p
        JOIN payment_allocations pa ON pa.payment_id = p.payment_id
        WHERE pa.allocation_status = 'Active'
        GROUP BY p.payment_id, p.payment_amount
        HAVING SUM(pa.allocated_amount) > p.payment_amount + 0.005
    """),

    # -- no invoice is allocated more money than it is worth ----------------
    # The mirror of the check above, per INVOICE rather than per payment, and
    # the one that was missing: a payment run reading a stale `paid_amount`
    # paid an invoice a second time, and nothing noticed until the schema
    # refused the resulting paid_amount three days later.
    #
    # Scoped to money that has moved or is moving. A failed or cancelled
    # transfer keeps its allocations - they are the trace of an attempt - but
    # they are not a claim on the invoice.
    ("aucune facture sur-allouee", """
        SELECT i.invoice_id, i.gross_amount, SUM(pa.allocated_amount) AS allocated
        FROM supplier_invoices i
        JOIN payment_allocations pa ON pa.invoice_id = i.invoice_id
        JOIN payments p ON p.payment_id = pa.payment_id
        WHERE pa.allocation_status = 'Active'
          AND p.payment_status IN ('Executed', 'Initiated')
        GROUP BY i.invoice_id, i.gross_amount
        HAVING ABS(SUM(pa.allocated_amount)) > ABS(i.gross_amount) + 0.005
    """),

    # -- credit notes clear with negative allocations -----------------------
    ("signe des allocations conforme au signe de la facture", """
        SELECT pa.allocation_id, pa.allocated_amount, i.gross_amount
        FROM payment_allocations pa
        JOIN supplier_invoices i ON i.invoice_id = pa.invoice_id
        WHERE sign(pa.allocated_amount) <> sign(i.gross_amount)
    """),

    # -- nothing is paid against an invoice that was refused ----------------
    ("aucune allocation Active vers une facture Rejected/Cancelled", """
        SELECT pa.allocation_id, i.invoice_id, i.invoice_approval_status
        FROM payment_allocations pa
        JOIN supplier_invoices i ON i.invoice_id = pa.invoice_id
        WHERE pa.allocation_status = 'Active'
          AND i.invoice_approval_status IN ('Rejected', 'Cancelled', 'Draft')
    """),

    # -- the replacement chain is a chain, not a loop -----------------------
    # A recursive walk: if following replaced_by_allocation_id ever returns to
    # its starting point, the history is unreadable.
    ("chaines de remplacement sans cycle", """
        WITH RECURSIVE walk(start_id, current_id, depth) AS (
            SELECT allocation_id, replaced_by_allocation_id, 1
            FROM payment_allocations
            WHERE replaced_by_allocation_id IS NOT NULL
            UNION ALL
            SELECT w.start_id, pa.replaced_by_allocation_id, w.depth + 1
            FROM walk w
            JOIN payment_allocations pa ON pa.allocation_id = w.current_id
            WHERE pa.replaced_by_allocation_id IS NOT NULL
              AND w.depth < 25
        )
        SELECT start_id, current_id, depth
        FROM walk
        WHERE current_id = start_id OR depth >= 25
    """),

    # -- a replaced allocation points at a real, active successor -----------
    ("toute allocation remplacee pointe vers une allocation existante", """
        SELECT pa.allocation_id
        FROM payment_allocations pa
        LEFT JOIN payment_allocations target
               ON target.allocation_id = pa.replaced_by_allocation_id
        WHERE pa.replaced_by_allocation_id IS NOT NULL
          AND target.allocation_id IS NULL
    """),

    # -- supplier lifecycle -------------------------------------------------
    ("aucune facture emise apres la desactivation du fournisseur", """
        SELECT i.invoice_id, i.invoice_date, s.supplier_id, s.deactivated_on
        FROM supplier_invoices i
        JOIN suppliers s ON s.supplier_id = i.supplier_id
        WHERE s.deactivated_on IS NOT NULL
          AND i.invoice_date > s.deactivated_on
    """),

    ("aucun paiement vers un fournisseur bloque apres son blocage", """
        SELECT p.payment_id, p.payment_date, s.supplier_id, s.deactivated_on
        FROM payments p
        JOIN suppliers s ON s.supplier_id = p.supplier_id
        WHERE s.supplier_status = 'Blocked'
          AND s.deactivated_on IS NOT NULL
          AND p.payment_date >= s.deactivated_on
    """),

    # -- employment lifecycle -----------------------------------------------
    ("aucune depense engagee hors de la periode d'emploi", """
        SELECT e.expense_id, e.expense_date, emp.hire_date, emp.departure_date
        FROM expenses e
        JOIN employees emp ON emp.employee_ref = e.employee_ref
        WHERE e.expense_date < emp.hire_date
           OR (emp.departure_date IS NOT NULL AND e.expense_date > emp.departure_date)
    """),

    ("aucun approbateur de depense parti avant sa decision", """
        SELECT e.expense_id, e.approved_at, emp.departure_date
        FROM expenses e
        JOIN employees emp ON emp.employee_ref = e.approved_by_employee_ref
        WHERE e.approved_at IS NOT NULL
          AND emp.departure_date IS NOT NULL
          AND e.approved_at::date > emp.departure_date
    """),

    # -- commission eligibility, historically -------------------------------
    ("commissions versees a des conseillers eligibles uniquement", """
        SELECT c.commission_id, emp.employee_type, emp.is_commission_eligible
        FROM commissions c
        JOIN employees emp ON emp.employee_ref = c.employee_ref
        WHERE emp.employee_type <> 'Advisor'
           OR emp.is_commission_eligible = FALSE
    """),

    ("aucune commission couvrant une periode hors emploi", """
        SELECT c.commission_id, c.period_start, c.period_end,
               emp.hire_date, emp.departure_date
        FROM commissions c
        JOIN employees emp ON emp.employee_ref = c.employee_ref
        WHERE c.period_start < emp.hire_date
           OR (emp.departure_date IS NOT NULL AND c.period_end > emp.departure_date)
    """),

    ("aucun doublon de commission sur (employe, type, periode)", """
        SELECT employee_ref, commission_type, period_start, period_end, COUNT(*)
        FROM commissions
        GROUP BY employee_ref, commission_type, period_start, period_end
        HAVING COUNT(*) > 1
    """),

    # -- chronology of the purchase cycle -----------------------------------
    ("aucun paiement anterieur a l'approbation de la facture qu'il solde", """
        SELECT pa.allocation_id, p.payment_date, i.approved_at
        FROM payment_allocations pa
        JOIN payments p ON p.payment_id = pa.payment_id
        JOIN supplier_invoices i ON i.invoice_id = pa.invoice_id
        WHERE i.approved_at IS NOT NULL
          AND p.payment_date < i.approved_at::date
    """),

    ("aucun paiement anterieur a la date de la facture", """
        SELECT pa.allocation_id, p.payment_date, i.invoice_date
        FROM payment_allocations pa
        JOIN payments p ON p.payment_id = pa.payment_id
        JOIN supplier_invoices i ON i.invoice_id = pa.invoice_id
        WHERE p.payment_date < i.invoice_date
    """),

    # -- the window ---------------------------------------------------------
    # The upper bound is NOT the end of the seeded history: the simulator
    # legitimately moves past it, one day at a time. `{horizon}` is the latest
    # day the business has reached - the simulator clock when there is one,
    # the end of the seed otherwise. Hard-coding 2026-09-30 here made every
    # simulated day fail its own integrity check.
    ("aucune date metier hors de la periode connue", """
        SELECT 'invoice' AS kind, invoice_id AS id FROM supplier_invoices
        WHERE invoice_date < DATE '{start}' OR invoice_date > DATE '{horizon}'
        UNION ALL
        SELECT 'payment', payment_id FROM payments
        WHERE payment_date < DATE '{start}' OR payment_date > DATE '{horizon}'
        UNION ALL
        SELECT 'expense', expense_id FROM expenses
        WHERE expense_date < DATE '{start}' OR expense_date > DATE '{horizon}'
    """),

    # -- horodatage ---------------------------------------------------------
    ("aucun horodatage posterieur a la journee en cours", """
        SELECT 'invoice' AS kind, invoice_id AS id FROM supplier_invoices
        WHERE created_at > DATE '{horizon}' + INTERVAL '1 day'
        UNION ALL
        SELECT 'payment', payment_id FROM payments
        WHERE created_at > DATE '{horizon}' + INTERVAL '1 day'
        UNION ALL
        SELECT 'expense', expense_id FROM expenses
        WHERE created_at > DATE '{horizon}' + INTERVAL '1 day'
        UNION ALL
        SELECT 'commission', commission_id FROM commissions
        WHERE created_at > DATE '{horizon}' + INTERVAL '1 day'
    """),
]

PERIOD_START = "2023-10-01"
SEED_HORIZON = "2026-09-30"


def _horizon(cursor):
    """The latest day the business has reached.

    Read from the simulator clock when it exists, so the same checks serve the
    seed and every simulated day after it. Both halves use one list of
    invariants - a simulator checking a different set from the seed would let
    the two drift apart silently.
    """
    cursor.execute("""
        SELECT to_regclass('public.simulation_state') IS NOT NULL
    """)
    if cursor.fetchone()[0]:
        cursor.execute("SELECT MAX(last_simulated_date) FROM simulation_state")
        row = cursor.fetchone()
        if row and row[0]:
            return max(str(row[0]), SEED_HORIZON)
    return SEED_HORIZON


def run_checks(cursor, sample_size=3):
    """Run every check. Returns a list of (label, offending_count, sample)."""
    bounds = {"start": PERIOD_START, "horizon": _horizon(cursor)}
    results = []
    for label, template in CHECKS:
        sql = template.format(**bounds)
        cursor.execute(f"SELECT COUNT(*) FROM ({sql}) AS offenders")
        count = cursor.fetchone()[0]
        sample = []
        if count:
            cursor.execute(f"SELECT * FROM ({sql}) AS offenders LIMIT {sample_size}")
            sample = cursor.fetchall()
        results.append((label, count, sample))
    return results

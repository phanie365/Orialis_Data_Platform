"""
Measure the loaded ERP dataset against the specification.

    python ERP/scripts/report_seed.py

Reads only. Every figure below is queried from the database rather than taken
from the generator's own memory - a generator that reports its own intentions
proves nothing, and the point of this script is to find out whether the rules
actually produced what they were supposed to produce.

Where a target exists it is printed alongside the observed value with the gap,
so a deviation is visible rather than buried.
"""

import sys
from pathlib import Path

import psycopg

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
from ERP.config import get_database_url, scrub  # noqa: E402

TARGET_ROWS = {
    "cost_centers": 25, "suppliers": 180, "employees": 185,
    "supplier_invoices": 8000, "payments": 7000, "payment_allocations": 8000,
    "expenses": 12000, "commissions": 5900, "fx_rates": 36,
}


def section(title):
    print()
    print("=" * 74)
    print(title)
    print("=" * 74)


def distribution(cursor, title, sql, targets=None, unit="%"):
    """Print a share breakdown, with the specification target beside it."""
    cursor.execute(sql)
    rows = cursor.fetchall()
    total = sum(row[1] for row in rows) or 1
    print(f"\n  {title}")
    for label, count in rows:
        share = 100.0 * count / total
        if targets and label in targets:
            target = targets[label]
            gap = share - target
            print(f"    {str(label):32} {count:>7,}  {share:5.1f}{unit}"
                  f"   cible {target:5.1f}{unit}   ecart {gap:+5.1f}")
        else:
            print(f"    {str(label):32} {count:>7,}  {share:5.1f}{unit}")


def main():
    database_url = get_database_url()
    try:
        connection = psycopg.connect(database_url)
    except psycopg.OperationalError as error:
        raise SystemExit(f"Connexion impossible : {scrub(error, database_url)}")

    cursor = connection.cursor()

    # -- volumes ------------------------------------------------------------
    section("1. VOLUMES")
    grand_total = 0
    for table, target in TARGET_ROWS.items():
        cursor.execute(f"SELECT COUNT(*) FROM {table}")
        count = cursor.fetchone()[0]
        grand_total += count
        gap = 100.0 * (count - target) / target
        print(f"  {table:24} {count:>7,}   cible {target:>6,}   ecart {gap:+5.1f}%")
    print(f"  {'TOTAL':24} {grand_total:>7,}   cible {sum(TARGET_ROWS.values()):>6,}")

    # -- headcount ----------------------------------------------------------
    section("2. EFFECTIFS")
    cursor.execute("""
        SELECT employee_status, COUNT(*) FROM employees
        GROUP BY employee_status ORDER BY 1
    """)
    for status, count in cursor.fetchall():
        print(f"  {status:24} {count:>7,}")

    cursor.execute("""
        SELECT employee_type, COUNT(*) FILTER (WHERE employee_status = 'Active'),
               COUNT(*)
        FROM employees GROUP BY employee_type ORDER BY 3 DESC
    """)
    print("\n  par type                 actifs    total")
    for kind, active, total in cursor.fetchall():
        print(f"    {kind:22} {active:>6,}   {total:>6,}")

    cursor.execute("""
        SELECT COUNT(*) FILTER (WHERE is_commission_eligible),
               COUNT(*) FILTER (WHERE work_email IS NULL),
               COUNT(*)
        FROM employees
    """)
    eligible, no_email, total = cursor.fetchone()
    print(f"\n  eligibles aux commissions  {eligible:>6,}")
    print(f"  sans work_email            {no_email:>6,}   "
          f"({100.0 * no_email / total:.1f}%, cible ~5%)")

    print("\n  courbe des effectifs actifs (1er janvier / 30 septembre)")
    for day in ("2023-10-01", "2024-04-01", "2024-10-01", "2025-04-01",
                "2025-10-01", "2026-04-01", "2026-09-30"):
        cursor.execute("""
            SELECT COUNT(*) FILTER (WHERE employee_type = 'Advisor'),
                   COUNT(*) FILTER (WHERE employee_type <> 'Advisor'),
                   COUNT(*) FILTER (WHERE employee_type = 'Advisor'
                                      AND is_commission_eligible)
            FROM employees
            WHERE hire_date <= %s
              AND (departure_date IS NULL OR departure_date >= %s)
        """, (day, day))
        advisors, support, eligible_now = cursor.fetchone()
        print(f"    {day}   conseillers {advisors:>4}  "
              f"(eligibles {eligible_now:>4})   support {support:>4}   "
              f"total {advisors + support:>4}")

    # -- suppliers ----------------------------------------------------------
    section("3. FOURNISSEURS")
    distribution(cursor, "statut", """
        SELECT supplier_status, COUNT(*) FROM suppliers
        GROUP BY 1 ORDER BY 2 DESC
    """, {"Active": 88.0, "Inactive": 9.0, "Blocked": 3.0})

    print("\n  concentration (Pareto)")
    cursor.execute("""
        WITH ranked AS (
            SELECT supplier_id, COUNT(*) AS invoices,
                   ROW_NUMBER() OVER (ORDER BY COUNT(*) DESC) AS rank
            FROM supplier_invoices GROUP BY supplier_id
        ), total AS (SELECT COUNT(*)::numeric AS n FROM supplier_invoices)
        SELECT
            ROUND(100 * SUM(invoices) FILTER (WHERE rank <= 10) / (SELECT n FROM total), 1),
            ROUND(100 * SUM(invoices) FILTER (WHERE rank <= 32) / (SELECT n FROM total), 1),
            ROUND(100 * SUM(invoices) FILTER (WHERE rank <= 77) / (SELECT n FROM total), 1),
            COUNT(*)
        FROM ranked
    """)
    top10, top32, top77, active_suppliers = cursor.fetchone()
    print(f"    top 10 fournisseurs   {top10:>5}%   cible ~40%")
    print(f"    top 32 fournisseurs   {top32:>5}%   cible ~70%")
    print(f"    top 77 fournisseurs   {top77:>5}%   cible ~90%")
    print(f"    fournisseurs ayant facture : {active_suppliers} / 180")

    # -- invoices -----------------------------------------------------------
    section("4. FACTURES")
    distribution(cursor, "type", """
        SELECT invoice_type, COUNT(*) FROM supplier_invoices
        GROUP BY 1 ORDER BY 2 DESC
    """, {"Standard": 97.0, "Credit Note": 3.0})

    distribution(cursor, "statut d'approbation", """
        SELECT invoice_approval_status, COUNT(*) FROM supplier_invoices
        GROUP BY 1 ORDER BY 2 DESC
    """, {"Approved": 90.0, "Pending Approval": 4.0, "Draft": 3.0,
          "Rejected": 2.5, "Cancelled": 0.5})

    distribution(cursor, "statut de paiement", """
        SELECT invoice_payment_status, COUNT(*) FROM supplier_invoices
        GROUP BY 1 ORDER BY 2 DESC
    """, {"Paid": 82.5, "Unpaid": 12.6, "Partially Paid": 4.9})

    distribution(cursor, "devise", """
        SELECT currency_code, COUNT(*) FROM supplier_invoices
        GROUP BY 1 ORDER BY 2 DESC
    """, {"EUR": 78.0, "CHF": 22.0})

    distribution(cursor, "traitement TVA", """
        SELECT tax_treatment, COUNT(*) FROM supplier_invoices
        GROUP BY 1 ORDER BY 2 DESC
    """, {"Standard": 62.0, "Reverse Charge": 21.0, "Out of Scope": 11.0,
          "Exempt": 6.0})

    distribution(cursor, "conditions de paiement (jours)", """
        SELECT s.payment_terms_days::text, COUNT(*)
        FROM supplier_invoices i JOIN suppliers s USING (supplier_id)
        GROUP BY 1 ORDER BY 2 DESC
    """, {"30": 55.0, "45": 20.0, "60": 15.0, "15": 6.0, "90": 3.0, "0": 1.0})

    print("\n  montants TTC (EUR, factures Standard)")
    cursor.execute("""
        SELECT ROUND(PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY gross_amount_eur)::numeric, 2),
               ROUND(PERCENTILE_CONT(0.9) WITHIN GROUP (ORDER BY gross_amount_eur)::numeric, 2),
               ROUND(MAX(gross_amount), 2),
               ROUND(SUM(gross_amount_eur), 2)
        FROM supplier_invoices WHERE invoice_type = 'Standard'
    """)
    median, p90, maximum, total_eur = cursor.fetchone()
    print(f"    mediane {median:>12,}   cible ~1 850")
    print(f"    P90     {p90:>12,}   cible ~12 000")
    # The cap applies to the TRANSACTION currency. A CHF invoice at the cap
    # converts to more than 180 000 EUR, which is arithmetic, not a breach.
    print(f"    max     {maximum:>12,}   plafond 180 000 (devise d'origine)")
    print(f"    total   {total_eur:>12,} EUR sur 36 mois")

    print("\n  retard de paiement (factures soldees)")
    cursor.execute("""
        WITH settled AS (
            SELECT i.invoice_id, i.due_date, MAX(p.payment_date) AS paid_on
            FROM supplier_invoices i
            JOIN payment_allocations pa ON pa.invoice_id = i.invoice_id
                                       AND pa.allocation_status = 'Active'
            JOIN payments p ON p.payment_id = pa.payment_id
                           AND p.payment_status = 'Executed'
            WHERE i.invoice_payment_status = 'Paid'
            GROUP BY i.invoice_id, i.due_date
        )
        SELECT CASE
                 WHEN paid_on <= due_date THEN 'a l''heure'
                 WHEN paid_on - due_date <= 15 THEN '1-15 jours'
                 WHEN paid_on - due_date <= 45 THEN '16-45 jours'
                 ELSE '> 45 jours' END AS bucket,
               COUNT(*)
        FROM settled GROUP BY 1 ORDER BY 2 DESC
    """)
    rows = cursor.fetchall()
    total = sum(row[1] for row in rows) or 1
    targets = {"a l'heure": 60.0, "1-15 jours": 25.0, "16-45 jours": 10.0,
               "> 45 jours": 5.0}
    for label, count in rows:
        share = 100.0 * count / total
        target = targets.get(label, 0)
        print(f"    {label:22} {count:>7,}  {share:5.1f}%   cible {target:5.1f}%")

    # -- payments -----------------------------------------------------------
    section("5. PAIEMENTS ET ALLOCATIONS")
    distribution(cursor, "statut de paiement", """
        SELECT payment_status, COUNT(*) FROM payments
        GROUP BY 1 ORDER BY 2 DESC
    """, {"Executed": 96.4, "Failed": 1.5, "Cancelled": 1.0, "Initiated": 1.1})

    distribution(cursor, "moyen de paiement", """
        SELECT payment_method, COUNT(*) FROM payments
        GROUP BY 1 ORDER BY 2 DESC
    """, {"SEPA Credit Transfer": 84.0, "SWIFT": 9.0, "Direct Debit": 4.0,
          "Card": 2.0, "Cheque": 1.0})

    print("\n  campagnes de paiement (jour de la semaine)")
    cursor.execute("""
        SELECT TO_CHAR(payment_date, 'Day'), COUNT(*)
        FROM payments GROUP BY 1, EXTRACT(DOW FROM payment_date)
        ORDER BY EXTRACT(DOW FROM payment_date)
    """)
    for day, count in cursor.fetchall():
        print(f"    {day.strip():12} {count:>7,}")

    print("\n  groupement")
    cursor.execute("""
        WITH per_payment AS (
            SELECT payment_id, COUNT(*) AS lines
            FROM payment_allocations WHERE allocation_status = 'Active'
            GROUP BY payment_id
        )
        SELECT lines, COUNT(*) FROM per_payment GROUP BY 1 ORDER BY 1
    """)
    rows = cursor.fetchall()
    total = sum(row[1] for row in rows) or 1
    for lines, count in rows:
        print(f"    {lines} facture(s) par paiement   {count:>7,}  "
              f"{100.0 * count / total:5.1f}%")

    print("\n  paiements partiels et echelonnes")
    cursor.execute("""
        WITH per_invoice AS (
            SELECT invoice_id, COUNT(*) AS n
            FROM payment_allocations WHERE allocation_status = 'Active'
            GROUP BY invoice_id
        )
        SELECT COUNT(*) FILTER (WHERE n >= 2), COUNT(*) FROM per_invoice
    """)
    multi, allocated = cursor.fetchone()
    print(f"    factures soldees en plusieurs versements  {multi:>7,} "
          f"/ {allocated:,}  ({100.0 * multi / max(allocated, 1):.1f}%)")

    cursor.execute("""
        SELECT COUNT(*) FROM supplier_invoices
        WHERE invoice_payment_status = 'Partially Paid'
    """)
    print(f"    factures 'Partially Paid'                 {cursor.fetchone()[0]:>7,}")

    distribution(cursor, "allocations", """
        SELECT allocation_status, COUNT(*) FROM payment_allocations
        GROUP BY 1 ORDER BY 2 DESC
    """)
    cursor.execute("""
        SELECT COUNT(*) FROM payment_allocations
        WHERE replaced_by_allocation_id IS NOT NULL
    """)
    print(f"    dont remplacees (chaine de correction)  {cursor.fetchone()[0]:>7,}"
          f"   cible ~120")

    # -- expenses -----------------------------------------------------------
    section("6. DEPENSES")
    distribution(cursor, "statut", """
        SELECT expense_status, COUNT(*) FROM expenses
        GROUP BY 1 ORDER BY 2 DESC
    """, {"Reimbursed": 86.0, "Approved": 5.0, "Submitted": 4.0,
          "Rejected": 3.0, "Draft": 1.5, "Cancelled": 0.5})

    distribution(cursor, "categorie", """
        SELECT expense_category, COUNT(*) FROM expenses
        GROUP BY 1 ORDER BY 2 DESC
    """, {"Travel": 28.0, "Meals": 19.0, "Accommodation": 16.0,
          "Client Entertainment": 12.0, "Transport": 10.0, "Training": 7.0,
          "Telecom": 5.0, "Office Supplies": 3.0})

    print("\n  par profil (reclamations par personne et par mois)")
    cursor.execute("""
        SELECT emp.employee_type, COUNT(*) AS claims,
               COUNT(DISTINCT emp.employee_ref) AS people
        FROM expenses e JOIN employees emp USING (employee_ref)
        GROUP BY 1 ORDER BY 2 DESC
    """)
    for kind, claims, people in cursor.fetchall():
        print(f"    {kind:22} {claims:>7,} reclamations  "
              f"{people:>4} personnes  {claims / max(people, 1) / 36:4.2f}/mois")

    cursor.execute("""
        SELECT ROUND(PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY gross_amount_eur)::numeric, 2),
               ROUND(PERCENTILE_CONT(0.9) WITHIN GROUP (ORDER BY gross_amount_eur)::numeric, 2),
               ROUND(MAX(gross_amount_eur), 2)
        FROM expenses
    """)
    median, p90, maximum = cursor.fetchone()
    print(f"\n  montants TTC (EUR) : mediane {median}  P90 {p90}  max {maximum}")

    # -- commissions --------------------------------------------------------
    section("7. COMMISSIONS")
    distribution(cursor, "type", """
        SELECT commission_type, COUNT(*) FROM commissions
        GROUP BY 1 ORDER BY 2 DESC
    """)
    distribution(cursor, "statut", """
        SELECT commission_status, COUNT(*) FROM commissions
        GROUP BY 1 ORDER BY 2 DESC
    """)
    distribution(cursor, "periodicite", """
        SELECT period_type, COUNT(*) FROM commissions
        GROUP BY 1 ORDER BY 2 DESC
    """)
    cursor.execute("""
        SELECT COUNT(DISTINCT employee_ref),
               ROUND(SUM(commission_amount_eur), 2)
        FROM commissions
    """)
    people, total_eur = cursor.fetchone()
    print(f"\n    conseillers concernes  {people}")
    print(f"    total verse            {total_eur:,} EUR")

    # -- seasonality --------------------------------------------------------
    section("8. SAISONNALITE")
    cursor.execute("""
        SELECT EXTRACT(MONTH FROM invoice_date)::int AS m,
               COUNT(*) AS invoices
        FROM supplier_invoices GROUP BY 1 ORDER BY 1
    """)
    invoices_by_month = dict(cursor.fetchall())
    cursor.execute("""
        SELECT EXTRACT(MONTH FROM expense_date)::int AS m, COUNT(*)
        FROM expenses GROUP BY 1 ORDER BY 1
    """)
    expenses_by_month = dict(cursor.fetchall())

    invoice_mean = sum(invoices_by_month.values()) / 12
    expense_mean = sum(expenses_by_month.values()) / 12
    names = ["jan", "fev", "mar", "avr", "mai", "jun",
             "jul", "aou", "sep", "oct", "nov", "dec"]
    print("\n  mois   factures  indice    depenses  indice")
    for month in range(1, 13):
        inv = invoices_by_month.get(month, 0)
        exp = expenses_by_month.get(month, 0)
        print(f"   {names[month - 1]}    {inv:>7,}   {inv / invoice_mean:4.2f}"
              f"     {exp:>7,}   {exp / expense_mean:4.2f}")

    cursor.execute("""
        SELECT COUNT(*) FILTER (WHERE EXTRACT(DOW FROM invoice_date) IN (0, 6)),
               COUNT(*) FROM supplier_invoices
    """)
    weekend, total = cursor.fetchone()
    print(f"\n  factures datees d'un week-end : {weekend} / {total:,}")
    cursor.execute("""
        SELECT COUNT(*) FILTER (WHERE EXTRACT(DOW FROM expense_date) IN (0, 6)),
               COUNT(*) FROM expenses
    """)
    weekend, total = cursor.fetchone()
    print(f"  depenses datees d'un week-end : {weekend} / {total:,}")

    print("\n  decembre : part de Client Entertainment")
    cursor.execute("""
        SELECT ROUND(100.0 * COUNT(*) FILTER (
                   WHERE expense_category = 'Client Entertainment') / COUNT(*), 1)
        FROM expenses WHERE EXTRACT(MONTH FROM expense_date) = 12
    """)
    print(f"    {cursor.fetchone()[0]}%   (moyenne annuelle 12%)")

    # -- CRM/ERP friction ---------------------------------------------------
    section("9. FRICTION CRM / ERP")
    cursor.execute("""
        SELECT COUNT(*) FILTER (WHERE employee_type = 'Advisor'
                                  AND employee_status = 'Active'),
               COUNT(*) FILTER (WHERE employee_type = 'Advisor'
                                  AND employee_status = 'Inactive'),
               COUNT(*) FILTER (WHERE employee_type <> 'Advisor'),
               COUNT(*) FILTER (WHERE work_email IS NULL),
               COUNT(*) FILTER (WHERE last_name = UPPER(last_name))
        FROM employees
    """)
    active_adv, gone_adv, support, no_email, upper_case = cursor.fetchone()
    print(f"  conseillers actifs (93 communs + 5 ERP seuls)   {active_adv:>5}")
    print(f"  conseillers partis (absents du CRM)             {gone_adv:>5}")
    print(f"  salaries support (aucune existence CRM)         {support:>5}")
    print(f"  sans work_email                                 {no_email:>5}")
    print(f"  patronymes en majuscules (variante de graphie)  {upper_case:>5}")
    cursor.execute("""
        SELECT COUNT(*) FROM employees
        WHERE last_name ~ '[éèêëàâîïôöûüçÉÈÊËÀÂÎÏÔÖÛÜÇ]'
           OR first_name ~ '[éèêëàâîïôöûüçÉÈÊËÀÂÎÏÔÖÛÜÇ]'
    """)
    print(f"  noms accentues                                  {cursor.fetchone()[0]:>5}")
    cursor.execute("SELECT COUNT(DISTINCT office_code) FROM cost_centers "
                   "WHERE office_code IS NOT NULL")
    print(f"  codes bureaux ERP (format FR-PAR-01)            {cursor.fetchone()[0]:>5}")

    connection.close()
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())

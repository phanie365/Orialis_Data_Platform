"""
Generate and load the Orialis ERP historical dataset.

    python ERP/scripts/seed_all.py            load, check, commit
    python ERP/scripts/seed_all.py --dry-run  generate and report, write nothing

Builds 36 months of history - October 2023 to September 2026, about 41 300
rows across the nine tables - and loads it in ONE transaction.

---------------------------------------------------------------------------
IDEMPOTENCY: UPSERT ON A DETERMINISTIC DATASET
---------------------------------------------------------------------------

Every row is written with `INSERT ... ON CONFLICT (pk) DO UPDATE`, and the
generator is deterministic: the same seed produces the same primary keys with
the same content. Re-running therefore converges to the identical state rather
than appending a second history. It is the pattern the CRM seeds already use -
"seed scripts describe a state and converge to it".

Note what is NOT used: no TRUNCATE, no DELETE. The ERP never deletes, and a
seed that starts by emptying the tables would make that doctrine a fiction on
its first line.

The honest limit: idempotency holds for the same CODE and the same SEED. Change
a distribution and re-run, and rows from the previous shape that no longer have
a counterpart would remain. That is a deliberate consequence of refusing to
delete, and `--dry-run` plus a fresh database is the way to iterate.

---------------------------------------------------------------------------
ALL OR NOTHING
---------------------------------------------------------------------------

The whole load, and the fourteen-odd multi-row integrity checks, happen inside
a single transaction. If one invariant fails, everything is rolled back.

A half-loaded history is worse than an empty database: it looks usable.
"""

import sys
import time
from pathlib import Path

import psycopg

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from ERP.config import get_database_url, scrub  # noqa: E402
from ERP.seed.activity import build_commissions, build_expenses  # noqa: E402
from ERP.seed.integrity import run_checks  # noqa: E402
from ERP.seed.organisation import build_employees, build_suppliers  # noqa: E402
from ERP.seed.purchases import build_invoices, build_payments, settle  # noqa: E402
from ERP.seed.reference import (build_cost_centres, build_fx_rates,  # noqa: E402
                                fx_lookup)
from ERP.seed.toolkit import PERIOD_END, PERIOD_START, RNG_SEED  # noqa: E402


# Loaded in dependency order: a table referenced by a foreign key is written
# before the table that references it.
LOAD_ORDER = [
    ("cost_centers", ["cost_center_id"]),
    ("fx_rates", ["rate_month", "from_currency", "to_currency"]),
    ("suppliers", ["supplier_id"]),
    ("employees", ["employee_ref"]),
    ("supplier_invoices", ["invoice_id"]),
    ("payments", ["payment_id"]),
    ("payment_allocations", ["allocation_id"]),
    ("expenses", ["expense_id"]),
    ("commissions", ["commission_id"]),
]


# ===========================================================================
# Generation
# ===========================================================================

def generate():
    """Build the whole history in memory. Touches no database."""
    started = time.time()

    cost_centres = build_cost_centres()
    fx_rates = build_fx_rates()
    fx_by_month = fx_lookup(fx_rates)

    suppliers = build_suppliers()
    employees = build_employees()

    # Who may approve an expense claim, and who may sign off a commission run.
    # Both are indexed by country with a "*" fallback, so a small office
    # without a manager of its own escalates to group level rather than
    # leaving claims stuck.
    approvers = _pool(employees, ("Management", "Back Office"))
    validators = _pool(employees, ("Management", "Compliance"))

    invoices = build_invoices(suppliers, cost_centres, fx_by_month, approvers)
    payments, allocations = build_payments(invoices, suppliers)

    # THE settlement rule. paid_amount is summed from the allocations here and
    # nowhere else; nothing upstream is allowed to assert it.
    settle(invoices, payments, allocations)

    expenses = build_expenses(employees, approvers, fx_by_month)
    commissions = build_commissions(employees, validators, fx_by_month)

    dataset = {
        "cost_centers": cost_centres,
        "fx_rates": fx_rates,
        "suppliers": suppliers,
        "employees": employees,
        "supplier_invoices": invoices,
        "payments": payments,
        "payment_allocations": allocations,
        "expenses": expenses,
        "commissions": commissions,
    }

    elapsed = time.time() - started
    total = sum(len(rows) for rows in dataset.values())
    print(f"Generation : {total:,} lignes en {elapsed:.1f}s "
          f"(seed {RNG_SEED}, {PERIOD_START} -> {PERIOD_END})")
    for table, _ in LOAD_ORDER:
        print(f"    {table:22} {len(dataset[table]):>7,}")
    return dataset


def _pool(employees, types):
    """Index employees of the given types by country, plus a '*' catch-all."""
    pool = {"*": []}
    for row in employees:
        if row["employee_type"] in types:
            pool.setdefault(row["country_code"], []).append(row)
            pool["*"].append(row)
    return pool


# ===========================================================================
# Loading
# ===========================================================================

def upsert(cursor, table, key_columns, rows, batch=2000):
    """Write rows with ON CONFLICT DO UPDATE, in batches.

    Keys beginning with an underscore are generator scratch (`_tier`,
    `_country`) and are stripped: they carry information the generator needed
    and the schema has no column for.
    """
    if not rows:
        return 0

    columns = [name for name in rows[0] if not name.startswith("_")]
    updatable = [name for name in columns if name not in key_columns]

    placeholders = ", ".join(["%s"] * len(columns))
    assignments = ", ".join(f"{name} = EXCLUDED.{name}" for name in updatable)
    statement = (
        f"INSERT INTO {table} ({', '.join(columns)}) VALUES ({placeholders}) "
        f"ON CONFLICT ({', '.join(key_columns)}) DO UPDATE SET {assignments}"
    )

    written = 0
    for start in range(0, len(rows), batch):
        chunk = rows[start:start + batch]
        cursor.executemany(statement,
                           [tuple(row[name] for name in columns) for row in chunk])
        written += len(chunk)
    return written


def load(dataset, dry_run=False):
    database_url = get_database_url()

    try:
        connection = psycopg.connect(database_url, autocommit=False)
    except psycopg.OperationalError as error:
        raise SystemExit(
            "Impossible de se connecter a PostgreSQL.\n"
            f"  {scrub(error, database_url)}\n"
            "Verifie ERP_DATABASE_URL, et que le schema existe "
            "(python ERP/scripts/init_db.py)."
        )

    failures = []
    try:
        with connection.cursor() as cursor:
            print("\nChargement (une seule transaction)")
            started = time.time()
            for table, key_columns in LOAD_ORDER:
                mark = time.time()
                written = upsert(cursor, table, key_columns, dataset[table])
                print(f"    {table:22} {written:>7,} lignes  "
                      f"{time.time() - mark:5.1f}s")
            print(f"  total {time.time() - started:.1f}s")

            print("\nControles d'integrite (avant COMMIT)")
            for label, count, sample in run_checks(cursor):
                if count:
                    failures.append((label, count, sample))
                    print(f"  [FAIL] {label}  -> {count} violation(s)")
                    for row in sample:
                        print(f"           {row}")
                else:
                    print(f"  [ok]   {label}")

            if failures:
                connection.rollback()
                print("\nROLLBACK : la base est inchangee.")
                return False

            if dry_run:
                connection.rollback()
                print("\n--dry-run : ROLLBACK volontaire, rien n'a ete ecrit.")
                return True

            connection.commit()
            print("\nCOMMIT : historique charge.")
            return True

    except psycopg.Error as error:
        connection.rollback()
        raise SystemExit(f"Chargement interrompu : {scrub(error, database_url)}")
    finally:
        connection.close()


def main():
    dry_run = "--dry-run" in sys.argv
    dataset = generate()
    ok = load(dataset, dry_run=dry_run)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())

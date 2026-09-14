"""
Simulate one business day of ERP activity.

    python ERP/scripts/simulate_day.py                # the next day
    python ERP/scripts/simulate_day.py --days 5       # five consecutive days
    python ERP/scripts/simulate_day.py --date 2026-10-06
    python ERP/scripts/simulate_day.py --status       # where the clock stands
    python ERP/scripts/simulate_day.py --dry-run      # simulate, then roll back

---------------------------------------------------------------------------
NOT IDEMPOTENT, BUT ATOMIC
---------------------------------------------------------------------------

The seed converges to a state; this does the opposite. Two runs are two
different days, and re-running yesterday is not a no-op - it would file
expense claims on a date the ledger has already closed. The clock in
`simulation_state` refuses to go backwards for exactly that reason.

Each day is still one transaction. Every invariant is checked before COMMIT
and a single failure rolls the whole day back, clock included: a day that
half-happened would leave the history unreadable, and the next day would build
on top of it.

---------------------------------------------------------------------------
--inject-fault
---------------------------------------------------------------------------

Deliberately corrupts `paid_amount` on one invoice, so that the rollback path
can be exercised on purpose rather than hoped for. A guarantee nobody has ever
seen fire is not a guarantee.
"""

import argparse
import random
import sys
from datetime import date, timedelta
from pathlib import Path

import psycopg

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from ERP.config import get_database_url, scrub  # noqa: E402
from ERP.seed.integrity import run_checks  # noqa: E402
from ERP.simulator import clock  # noqa: E402
from ERP.simulator.day import new_report, simulate_day  # noqa: E402


def parse_args():
    parser = argparse.ArgumentParser(description="Simuler une journee ERP.")
    parser.add_argument("--date", help="journee a simuler (AAAA-MM-JJ)")
    parser.add_argument("--days", type=int, default=1,
                        help="nombre de journees consecutives")
    parser.add_argument("--dry-run", action="store_true",
                        help="tout simuler puis annuler")
    parser.add_argument("--status", action="store_true",
                        help="afficher l'horloge et sortir")
    parser.add_argument("--inject-fault", action="store_true",
                        help="casser volontairement un invariant (test)")
    parser.add_argument("--seed", type=int,
                        help="graine aleatoire (par defaut : derivee du jour)")
    return parser.parse_args()


def show_status(cursor):
    last, run_at, days, business = clock.status(cursor)
    print("Horloge du simulateur ERP")
    print(f"  derniere journee simulee : {last}")
    print(f"  prochaine journee        : {last + timedelta(days=1)}")
    print(f"  journees simulees        : {days} (dont {business} ouvrees)")
    print(f"  derniere execution       : {run_at}")


def run_one_day(connection, day, args):
    """One day, one transaction. Returns True if it was committed."""
    cursor = connection.cursor()

    warning = clock.check_forward(cursor, day)
    if warning:
        print(f"  [note] {warning}")

    # The stream is derived from the DATE, so re-running a given day after a
    # rollback reproduces it exactly - which is what makes the fault-injection
    # test meaningful.
    rng = random.Random(args.seed if args.seed is not None else f"erp-day:{day}")

    report, was_business_day, _ = simulate_day(cursor, day, rng)
    clock.record(cursor, day, was_business_day)

    if args.inject_fault:
        # A fault the SCHEMA cannot see, on purpose.
        #
        # Changing `paid_amount` directly would trip a CHECK constraint, and
        # would prove nothing about this layer - `test_schema.py` already
        # proves the schema bites. What is exercised here is the applicative
        # half of the doctrine: an allocation amount is altered and the
        # invoice is left untouched, so the row stays perfectly valid on its
        # own and only the multi-row sum disagrees.
        cursor.execute("""
            UPDATE payment_allocations
            SET allocated_amount = allocated_amount + 5.00, updated_at = now()
            WHERE allocation_id = (
                SELECT pa.allocation_id
                FROM payment_allocations pa
                JOIN payments p ON p.payment_id = pa.payment_id
                WHERE pa.allocation_status = 'Active'
                  AND p.payment_status = 'Executed'
                ORDER BY pa.allocation_id LIMIT 1)
        """)
        print("  [!] faute injectee : montant d'allocation modifie sans "
              "recalcul du reglement")

    label = day.strftime("%A %d %B %Y")
    kind = "jour ouvre" if was_business_day else "hors activite"
    print(f"\n{label}  ({kind})")
    for note in report["notes"]:
        print(f"  . {note}")
    _print_section("creations", report["created"])
    _print_section("mises a jour", report["updated"])
    _print_section("transitions", report["transitions"])

    failures = []
    for check, count, sample in run_checks(cursor):
        if count:
            failures.append((check, count, sample))

    if failures:
        connection.rollback()
        print("\n  CONTROLES D'INTEGRITE EN ECHEC :")
        for check, count, sample in failures:
            print(f"    [FAIL] {check}  -> {count} violation(s)")
            for row in sample[:2]:
                print(f"             {row}")
        print("  ROLLBACK : la journee entiere est annulee, horloge comprise.")
        return False

    if args.dry_run:
        connection.rollback()
        print("  --dry-run : ROLLBACK volontaire.")
        return True

    connection.commit()
    print("  COMMIT")
    return True


def _print_section(title, counters):
    if not counters:
        return
    print(f"  {title} :")
    for key in sorted(counters):
        print(f"    {key:48} {counters[key]:>6,}")


def main():
    args = parse_args()
    database_url = get_database_url()

    try:
        connection = psycopg.connect(database_url, autocommit=False)
    except psycopg.OperationalError as error:
        raise SystemExit(f"Connexion impossible : {scrub(error, database_url)}")

    try:
        with connection.cursor() as cursor:
            if args.status:
                show_status(cursor)
                connection.commit()
                return 0

            if args.date:
                start = date.fromisoformat(args.date)
            else:
                start = clock.next_day(cursor)
            # The clock table may have just been created; keep it.
            connection.commit()

        for offset in range(args.days):
            day = start + timedelta(days=offset)
            try:
                if not run_one_day(connection, day, args):
                    return 1
            except ValueError as error:
                connection.rollback()
                raise SystemExit(f"\n{error}")

        with connection.cursor() as cursor:
            print()
            show_status(cursor)
            connection.commit()

    except psycopg.Error as error:
        connection.rollback()
        raise SystemExit(f"Journee interrompue : {scrub(error, database_url)}")
    finally:
        connection.close()

    return 0


if __name__ == "__main__":
    sys.exit(main())

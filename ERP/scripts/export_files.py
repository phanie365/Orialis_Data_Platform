"""
Produce the Orialis ERP file extracts.

    python ERP/scripts/export_files.py --expenses --date 2026-10-15
    python ERP/scripts/export_files.py --expenses --from 2026-10-01 --to 2026-10-31
    python ERP/scripts/export_files.py --commissions --month 2026-10
    python ERP/scripts/export_files.py --backfill        # the whole history
    python ERP/scripts/export_files.py --catch-up        # everything not yet written

---------------------------------------------------------------------------
INDEPENDENT OF THE SIMULATOR, ON PURPOSE
---------------------------------------------------------------------------

Nothing calls this from `simulate_day.py`, and nothing here advances the
business clock. Two reasons, both practical:

  - an export that only ever runs as the tail of a simulated day could never
    be re-run, and re-running a failed batch is the single most common thing
    anyone does with a file feed;

  - a day whose export failed should still be a day that HAPPENED. Coupling
    them would mean a disk-full error rolling back a day of business.

So the simulator writes the data and stops; this reads it and writes files.
The only thing they share is the database.

---------------------------------------------------------------------------
IDEMPOTENCE
---------------------------------------------------------------------------

One period, one path, always the same name. Re-running an export OVERWRITES
its file rather than adding `_v2` beside it: two files claiming to describe
the same day is the failure mode worth designing against.

With unchanged data, a re-run reproduces the CSV byte for byte. That holds
because every technical column is derived from the BUSINESS date rather than
from the clock - see `extract_identity()`. A `now()` in an export column is
the usual reason a feed cannot be verified.

`--skip-existing` switches to archival behaviour: a file already written is
left exactly as it is. That is the right mode for a backfill you intend to
keep, and the wrong one when a bug has just been fixed.

THE ONE CAVEAT, and it is a property of `updated_at` feeds rather than of
this script: regenerating an OLD day after the data has moved on produces a
SMALLER file. A row modified again since has left that day's window. The
daily extract is reproducible from unchanged data, not from any data.
"""

import argparse
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

import psycopg
from psycopg.rows import dict_row

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from ERP.config import get_database_url, scrub  # noqa: E402
from ERP.exporters import commissions_xlsx, expenses_csv  # noqa: E402

DEFAULT_ROOT = Path(__file__).resolve().parent.parent / "exports"

# Where the seeded history begins. Nothing exists before it.
HISTORY_START = date(2023, 10, 1)


def parse_args():
    parser = argparse.ArgumentParser(
        description="Generer les extraits fichiers de l'ERP Orialis.")
    parser.add_argument("--expenses", action="store_true",
                        help="extrait quotidien des depenses (CSV)")
    parser.add_argument("--commissions", action="store_true",
                        help="classeur mensuel des commissions (XLSX)")
    parser.add_argument("--date", help="une journee (AAAA-MM-JJ)")
    parser.add_argument("--from", dest="date_from", help="debut de plage")
    parser.add_argument("--to", dest="date_to", help="fin de plage")
    parser.add_argument("--month", help="un mois (AAAA-MM)")
    parser.add_argument("--backfill", action="store_true",
                        help="tout l'historique present en base")
    parser.add_argument("--catch-up", action="store_true",
                        help="uniquement ce qui n'a pas encore ete ecrit")
    parser.add_argument("--skip-existing", action="store_true",
                        help="ne pas reecrire un fichier deja present")
    parser.add_argument("--root", default=str(DEFAULT_ROOT),
                        help="racine des exports")
    parser.add_argument("--quiet", action="store_true")
    return parser.parse_args()


def data_horizon(cursor) -> date:
    """The last day the data reaches.

    The simulator clock when there is one - it is authoritative, and it knows
    about days that left no rows - otherwise the latest `updated_at` in the
    two exported tables.

    Every aggregate below carries an explicit alias. Rows come back as
    dictionaries (the exporters index columns by name), so there is no
    `row[0]` to read and the driver's default name for an unnamed expression
    would be guesswork.
    """
    present = cursor.execute(
        "SELECT to_regclass('public.simulation_state') IS NOT NULL AS present"
    ).fetchone()["present"]

    if present:
        clock = cursor.execute(
            "SELECT MAX(last_simulated_date) AS clock FROM simulation_state"
        ).fetchone()["clock"]
        if clock:
            return clock

    return cursor.execute("""
        SELECT GREATEST(
            COALESCE((SELECT MAX(updated_at)::date FROM expenses), DATE '2000-01-01'),
            COALESCE((SELECT MAX(updated_at)::date FROM commissions), DATE '2000-01-01')
        ) AS horizon
    """).fetchone()["horizon"]


def months_between(first: date, last: date):
    year, month = first.year, first.month
    while (year, month) <= (last.year, last.month):
        yield year, month
        month += 1
        if month == 13:
            year, month = year + 1, 1


def run(connection, args, root: Path):
    horizon = data_horizon(connection)
    written = {"expenses": 0, "commissions": 0, "skipped": 0, "rows": 0}

    # -- which days ---------------------------------------------------------
    days = []
    if args.date:
        days = [date.fromisoformat(args.date)]
    elif args.date_from or args.date_to:
        first = date.fromisoformat(args.date_from) if args.date_from else HISTORY_START
        last = date.fromisoformat(args.date_to) if args.date_to else horizon
        days = [first + timedelta(days=offset)
                for offset in range((last - first).days + 1)]
    elif args.backfill or args.catch_up:
        days = [HISTORY_START + timedelta(days=offset)
                for offset in range((horizon - HISTORY_START).days + 1)]

    # -- which months -------------------------------------------------------
    months = []
    if args.month:
        year, month = args.month.split("-")
        months = [(int(year), int(month))]
    elif args.backfill or args.catch_up:
        months = list(months_between(HISTORY_START, horizon))
    elif args.date_from or args.date_to or args.date:
        if days:
            months = list(months_between(days[0], days[-1]))

    want_expenses = args.expenses or args.backfill or args.catch_up or not args.commissions
    want_commissions = args.commissions or args.backfill or args.catch_up

    if want_expenses:
        for day in days:
            if day > horizon:
                continue
            path = expenses_csv.output_path(root, day)
            if args.skip_existing and path.exists():
                written["skipped"] += 1
                continue
            result = expenses_csv.export_day(connection, root, day)
            written["expenses"] += 1
            written["rows"] += result["rows"]
            if not args.quiet and result["rows"]:
                print(f"  {result['path'].relative_to(root)}  "
                      f"{result['rows']:>5} lignes")

    if want_commissions:
        for year, month in months:
            path = commissions_xlsx.output_path(root, year, month)
            if args.skip_existing and path.exists():
                written["skipped"] += 1
                continue
            result = commissions_xlsx.export_month(connection, root, year, month)
            written["commissions"] += 1
            written["rows"] += result["rows"]
            if not args.quiet and result["rows"]:
                print(f"  {result['path'].relative_to(root)}  "
                      f"{result['rows']:>5} lignes, "
                      f"{result['advisors']} conseillers")

    return written, horizon


def main():
    args = parse_args()
    root = Path(args.root)
    database_url = get_database_url()

    if not any((args.date, args.date_from, args.date_to, args.month,
                args.backfill, args.catch_up)):
        raise SystemExit(
            "Rien a exporter. Precise --date, --from/--to, --month, "
            "--backfill ou --catch-up.")

    try:
        # dict_row is not optional: every exporter reads its columns by name
        # (`row["expense_id"]`), which is what keeps the mapping from database
        # column to business column legible. Without it the first export
        # raises a TypeError on the first row.
        connection = psycopg.connect(database_url, autocommit=True,
                                     row_factory=dict_row)
    except psycopg.OperationalError as error:
        raise SystemExit(f"Connexion impossible : {scrub(error, database_url)}")

    # READ-ONLY, and the database is told so. A transaction declared read-only
    # is refused by PostgreSQL the moment it attempts a write - the guarantee
    # stops being a promise about this code and becomes one the server keeps.
    try:
        connection.execute("SET default_transaction_read_only = on")
        with connection.cursor() as cursor:
            written, horizon = run(cursor, args, root)
    except psycopg.Error as error:
        raise SystemExit(f"Export interrompu : {scrub(error, database_url)}")
    finally:
        connection.close()

    print()
    print(f"Horizon des donnees : {horizon}")
    print(f"Fichiers depenses   : {written['expenses']:>5}")
    print(f"Classeurs commissions : {written['commissions']:>3}")
    if written["skipped"]:
        print(f"Ignores (deja presents) : {written['skipped']}")
    print(f"Lignes ecrites      : {written['rows']:>5,}")
    print(f"Racine              : {root}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

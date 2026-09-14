"""
Automated verification of the Orialis ERP file extracts.

    python ERP/tests/test_exports.py

Exit code 0 when everything passes, 1 otherwise.

Writes its files into a TEMPORARY directory, never into `ERP/exports/`, so a
test run cannot disturb a real extract set. Reads the real database - the
whole question being whether the files agree with what is in PostgreSQL, and
a stubbed database would answer a different question.

Requires: ERP_DATABASE_URL, a seeded schema, and ideally a few simulated days
so that an expense has been modified more than once.
"""

import csv
import json
import shutil
import sys
import tempfile
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import psycopg
from psycopg.rows import dict_row

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from ERP.config import get_database_url  # noqa: E402
from ERP.exporters import commissions_xlsx, expenses_csv  # noqa: E402
from ERP.exporters.conventions import (DELIMITER, ENCODING,  # noqa: E402
                                       EXPENSE_STATUS_CODES)


class Results:
    def __init__(self):
        self.passed = 0
        self.failed = 0
        self.failures = []

    def check(self, label, condition, detail=""):
        if condition:
            self.passed += 1
            print(f"  [PASS] {label}")
        else:
            self.failed += 1
            self.failures.append((label, detail))
            print(f"  [FAIL] {label}")
            if detail:
                print(f"         {detail}")


def section(title):
    print()
    print("=" * 72)
    print(title)
    print("=" * 72)


def read_csv(path: Path):
    """Read an extract back the way a consumer would have to."""
    with path.open("r", encoding=ENCODING, newline="") as handle:
        return list(csv.DictReader(handle, delimiter=DELIMITER))


def to_decimal(text: str) -> Decimal:
    """Undo the European number convention."""
    return Decimal(text.replace(",", "."))


def main():
    results = Results()
    url = get_database_url()
    root = Path(tempfile.mkdtemp(prefix="orialis-exports-"))

    connection = psycopg.connect(url, autocommit=True, row_factory=dict_row)
    try:
        cursor = connection.cursor()
        before = _counts(cursor)

        busy_day, quiet_day = _pick_days(cursor)
        busy_month = _pick_month(cursor)

        _csv_schema(cursor, results, root, busy_day)
        _csv_matches_database(cursor, results, root, busy_day)
        _csv_window(cursor, results, root, busy_day)
        _csv_empty_day(cursor, results, root, quiet_day)
        _csv_determinism(cursor, results, root, busy_day)
        _xlsx(cursor, results, root, busy_month)
        _xlsx_determinism(cursor, results, root, busy_month)
        _no_duplicate_files(cursor, results, root, busy_day, busy_month)
        _read_only(cursor, results, before)
    finally:
        connection.close()
        shutil.rmtree(root, ignore_errors=True)

    total = results.passed + results.failed
    print()
    print("=" * 72)
    print(f"RESULTAT : {results.passed} PASS / {results.failed} FAIL "
          f"sur {total} verifications")
    print("=" * 72)
    if results.failures:
        print()
        for label, detail in results.failures:
            print(f"  FAIL {label}")
            if detail:
                print(f"       {detail}")
    return 1 if results.failed else 0


def _counts(cursor):
    return {
        table: cursor.execute(f"SELECT COUNT(*) AS n FROM {table}").fetchone()["n"]
        for table in ("expenses", "commissions", "employees",
                      "supplier_invoices", "payments")
    }


def _pick_days(cursor):
    """A day with plenty of movement, and a day with none.

    Chosen from the data rather than hardcoded, so the suite works whatever
    has been seeded and however many days have been simulated.
    """
    busy = cursor.execute("""
        SELECT updated_at::date AS d, COUNT(*) AS n
        FROM expenses GROUP BY 1 ORDER BY n DESC, d LIMIT 1
    """).fetchone()["d"]

    # The series ends the day AFTER the last modification, a day that by
    # construction has no activity - so the query always returns a row. A
    # fixed upper bound would return nothing, and crash on None["d"], once
    # every day up to it had seen a change.
    quiet = cursor.execute("""
        SELECT gs::date AS d
        FROM generate_series(
                 DATE '2023-10-01',
                 (SELECT COALESCE(MAX(updated_at)::date, DATE '2023-10-01') + 1
                  FROM expenses),
                 '1 day') gs
        WHERE NOT EXISTS (SELECT 1 FROM expenses
                          WHERE updated_at::date = gs::date)
        ORDER BY d LIMIT 1
    """).fetchone()["d"]
    return busy, quiet


def _pick_month(cursor):
    row = cursor.execute("""
        SELECT EXTRACT(YEAR FROM calculated_at)::int AS y,
               EXTRACT(MONTH FROM calculated_at)::int AS m, COUNT(*) AS n
        FROM commissions GROUP BY 1, 2 ORDER BY n DESC, y, m LIMIT 1
    """).fetchone()
    return row["y"], row["m"]


# ---------------------------------------------------------------------------

def _csv_schema(cursor, results, root, day):
    section(f"1. SCHEMA DU CSV QUOTIDIEN ({day})")
    result = expenses_csv.export_day(cursor, root, day)
    path = result["path"]

    results.check("le fichier est ecrit au chemin attendu",
                  path.exists() and path.name == f"EXPENSES_{day:%Y%m%d}.csv",
                  str(path))
    results.check("il est range par annee/mois",
                  path.parent.name == f"{day:%m}"
                  and path.parent.parent.name == f"{day:%Y}",
                  str(path.parent))

    raw = path.read_bytes()
    results.check("encodage UTF-8 avec BOM", raw.startswith(b"\xef\xbb\xbf"),
                  str(raw[:6]))
    results.check("fins de ligne CRLF", b"\r\n" in raw and b"\n\r" not in raw)

    text = raw.decode("utf-8-sig")
    header = text.split("\r\n")[0]
    results.check("separateur point-virgule", ";" in header and "," not in header,
                  header[:80])
    results.check("en-tete conforme au schema declare",
                  header.split(";") == expenses_csv.HEADER,
                  header[:120])

    rows = read_csv(path)
    results.check("toutes les lignes ont le bon nombre de colonnes",
                  all(len(row) == len(expenses_csv.HEADER) for row in rows),
                  f"{len(rows)} lignes")

    if rows:
        sample = rows[0]
        results.check("dates au format JJ/MM/AAAA",
                      _is_ddmmyyyy(sample["EXPENSE_DT"]), sample["EXPENSE_DT"])
        results.check("montants a virgule decimale",
                      "," in sample["GROSS_AMT"], sample["GROSS_AMT"])
        results.check("montants sans separateur de milliers",
                      " " not in sample["GROSS_AMT"], sample["GROSS_AMT"])
        results.check("statut code, pas libelle",
                      sample["STATUS_CODE"] in EXPENSE_STATUS_CODES.values(),
                      sample["STATUS_CODE"])
        results.check("colonne technique EXTRACT_ID presente",
                      sample["EXTRACT_ID"] == f"EXP-D-{day:%Y%m%d}",
                      sample["EXTRACT_ID"])
        results.check("EXTRACT_DT = lendemain du jour metier",
                      sample["EXTRACT_DT"] == (day + timedelta(days=1)).strftime("%d/%m/%Y"),
                      sample["EXTRACT_DT"])
        results.check("valeurs absentes = champ vide, pas 'None'",
                      all("None" not in (value or "") for value in sample.values()),
                      str(sample)[:150])

    manifest = json.loads(result["manifest"].read_text(encoding="utf-8"))
    results.check("manifeste ecrit a cote du fichier", result["manifest"].exists())
    results.check("le manifeste annonce le nombre de lignes",
                  manifest["row_count"] == len(rows),
                  f"{manifest['row_count']} vs {len(rows)}")
    results.check("le manifeste documente encodage et separateur",
                  manifest["encoding"] == "utf-8-sig"
                  and manifest["delimiter"] == ";",
                  str(manifest)[:150])
    results.check("le manifeste publie le sha256 du fichier",
                  len(manifest["sha256"]) == 64)
    results.check("le manifeste explique la maille",
                  "MODIFIED" in manifest["grain"], manifest["grain"])


def _is_ddmmyyyy(text):
    try:
        datetime.strptime(text, "%d/%m/%Y")
        return True
    except (ValueError, TypeError):
        return False


def _csv_matches_database(cursor, results, root, day):
    section("2. CORRESPONDANCE FICHIER / POSTGRESQL")
    path = expenses_csv.output_path(root, day)
    rows = read_csv(path)

    start = datetime.combine(day, datetime.min.time(), timezone.utc)
    expected = cursor.execute("""
        SELECT expense_id, expense_status, gross_amount, gross_amount_eur,
               currency_code, employee_ref
        FROM expenses
        WHERE updated_at >= %s AND updated_at < %s
        ORDER BY updated_at, expense_id
    """, (start, start + timedelta(days=1))).fetchall()

    results.check("meme nombre de lignes qu'en base",
                  len(rows) == len(expected),
                  f"{len(rows)} fichier vs {len(expected)} base")

    results.check("memes identifiants, dans le meme ordre",
                  [row["EXPENSE_REF"] for row in rows]
                  == [row["expense_id"] for row in expected],
                  "ordre ou contenu divergent")

    mismatched = []
    for file_row, db_row in zip(rows, expected):
        if to_decimal(file_row["GROSS_AMT"]) != db_row["gross_amount"]:
            mismatched.append((file_row["EXPENSE_REF"], "GROSS_AMT"))
        if file_row["STATUS_CODE"] != EXPENSE_STATUS_CODES[db_row["expense_status"]]:
            mismatched.append((file_row["EXPENSE_REF"], "STATUS_CODE"))
        if file_row["CURRENCY"] != db_row["currency_code"]:
            mismatched.append((file_row["EXPENSE_REF"], "CURRENCY"))
    results.check("montants, statuts et devises identiques a la base",
                  not mismatched, str(mismatched[:3]))

    # The total must survive the European number convention round trip.
    if rows:
        file_total = sum(to_decimal(row["GROSS_AMT_EUR"]) for row in rows)
        db_total = cursor.execute("""
            SELECT COALESCE(SUM(gross_amount_eur), 0) AS total FROM expenses
            WHERE updated_at >= %s AND updated_at < %s
        """, (start, start + timedelta(days=1))).fetchone()["total"]
        results.check("somme des montants EUR identique au centime",
                      file_total == db_total, f"{file_total} vs {db_total}")

    # The employee name is joined in - it is not in the expenses table, and
    # the API does not expose `employees` at all.
    if rows:
        names = cursor.execute(
            "SELECT last_name, first_name FROM employees WHERE employee_ref = %s",
            (rows[0]["EMPLOYEE_NUMBER"],)).fetchone()
        results.check("le nom du salarie correspond a la table employees",
                      rows[0]["EMPLOYEE_LAST_NAME"] == names["last_name"]
                      and rows[0]["EMPLOYEE_FIRST_NAME"] == names["first_name"],
                      f"{rows[0]['EMPLOYEE_LAST_NAME']} vs {names['last_name']}")


def _csv_window(cursor, results, root, day):
    section("3. FENETRE TEMPORELLE ET DELTA")
    path = expenses_csv.output_path(root, day)
    rows = read_csv(path)

    outside = [row for row in rows
               if datetime.strptime(row["LAST_MODIFIED_DT"],
                                    "%d/%m/%Y %H:%M:%S").date() != day]
    results.check("toutes les lignes ont ete modifiees CE jour-la",
                  not outside, f"{len(outside)} hors fenetre")

    results.check("aucun doublon d'identifiant dans un fichier",
                  len({row["EXPENSE_REF"] for row in rows}) == len(rows),
                  f"{len(rows) - len({r['EXPENSE_REF'] for r in rows})} doublons")

    # The half-open window must tile: consecutive days share no row.
    neighbour = day + timedelta(days=1)
    expenses_csv.export_day(cursor, root, neighbour)
    next_rows = read_csv(expenses_csv.output_path(root, neighbour))
    overlap = ({row["EXPENSE_REF"] for row in rows}
               & {row["EXPENSE_REF"] for row in next_rows})
    results.check("deux jours consecutifs : aucune ligne commune",
                  not overlap, f"{len(overlap)} en commun")

    # And they must not lose anything either: the union over the whole history
    # is exactly the table.
    total = cursor.execute("SELECT COUNT(*) AS n FROM expenses").fetchone()["n"]
    covered = cursor.execute("""
        SELECT COUNT(*) AS n FROM expenses
        WHERE updated_at::date BETWEEN DATE '2023-10-01' AND DATE '2030-12-31'
    """).fetchone()["n"]
    results.check("chaque depense tombe dans exactement un jour",
                  covered == total, f"{covered} vs {total}")


def _csv_empty_day(cursor, results, root, quiet_day):
    section(f"4. JOURNEE SANS ACTIVITE ({quiet_day})")
    result = expenses_csv.export_day(cursor, root, quiet_day)
    path = result["path"]

    results.check("un fichier est ecrit malgre l'absence de donnees",
                  path.exists(), str(path))
    results.check("il ne contient aucune ligne de donnees",
                  result["rows"] == 0, str(result["rows"]))

    text = path.read_bytes().decode("utf-8-sig")
    results.check("il contient quand meme l'en-tete",
                  text.split("\r\n")[0].split(";") == expenses_csv.HEADER)
    results.check("lu par un consommateur : 0 ligne, pas une erreur",
                  read_csv(path) == [])
    manifest = json.loads(result["manifest"].read_text(encoding="utf-8"))
    results.check("le manifeste annonce row_count = 0",
                  manifest["row_count"] == 0, str(manifest["row_count"]))


def _csv_determinism(cursor, results, root, day):
    section("5. DETERMINISME DU CSV")
    path = expenses_csv.output_path(root, day)
    first = path.read_bytes()
    first_manifest = (path.with_suffix(path.suffix + ".manifest.json")
                      .read_bytes())

    expenses_csv.export_day(cursor, root, day)
    second = path.read_bytes()
    second_manifest = (path.with_suffix(path.suffix + ".manifest.json")
                       .read_bytes())

    results.check("relancer le meme jour produit un fichier identique AU BYTE",
                  first == second,
                  f"{len(first)} vs {len(second)} octets")
    results.check("le manifeste aussi est identique au byte",
                  first_manifest == second_manifest)

    # This only holds because no technical column carries a wall clock.
    rows = read_csv(path)
    if rows:
        results.check("aucune colonne ne porte l'heure reelle d'execution",
                      rows[0]["EXTRACT_DT"]
                      == (day + timedelta(days=1)).strftime("%d/%m/%Y"),
                      rows[0]["EXTRACT_DT"])


def _xlsx(cursor, results, root, period):
    year, month = period
    section(f"6. CLASSEUR MENSUEL ({year}-{month:02d})")
    from openpyxl import load_workbook

    result = commissions_xlsx.export_month(cursor, root, year, month)
    path = result["path"]

    results.check("le classeur est ecrit au chemin attendu",
                  path.exists()
                  and path.name == f"COMMISSIONS_{year}{month:02d}.xlsx",
                  str(path))

    workbook = load_workbook(path, data_only=True)
    results.check("deux feuilles, nommees comme documente",
                  workbook.sheetnames == ["Commissions", "Summary"],
                  str(workbook.sheetnames))

    detail = workbook["Commissions"]
    results.check("en-tete du detail conforme",
                  [cell.value for cell in detail[1]]
                  == commissions_xlsx.DETAIL_HEADER,
                  str([cell.value for cell in detail[1]])[:120])

    expected = cursor.execute("""
        SELECT COUNT(*) AS n,
               COUNT(DISTINCT employee_ref) AS advisors,
               COALESCE(SUM(commission_amount_eur), 0) AS total
        FROM commissions
        WHERE calculated_at >= %s AND calculated_at < %s
    """, commissions_xlsx.month_bounds(year, month)).fetchone()

    results.check("nombre de lignes identique a la base",
                  detail.max_row - 1 == expected["n"],
                  f"{detail.max_row - 1} vs {expected['n']}")

    summary = workbook["Summary"]
    results.check("en-tete du resume conforme",
                  [cell.value for cell in summary[1]]
                  == commissions_xlsx.SUMMARY_HEADER)
    results.check("une ligne de resume par conseiller",
                  summary.max_row - 1 == expected["advisors"],
                  f"{summary.max_row - 1} vs {expected['advisors']}")

    # The two sheets must agree - a summary that disagrees with its own
    # detail is worse than no summary.
    detail_total = sum(row[15] for row in
                       detail.iter_rows(min_row=2, values_only=True)
                       if row[15] is not None)
    summary_total = sum(row[7] for row in
                        summary.iter_rows(min_row=2, values_only=True)
                        if row[7] is not None)
    results.check("le resume totalise exactement le detail",
                  abs(detail_total - summary_total) < 0.01,
                  f"{detail_total} vs {summary_total}")
    results.check("et le total correspond a la base",
                  abs(Decimal(str(round(detail_total, 2))) - expected["total"]) < Decimal("0.05"),
                  f"{detail_total} vs {expected['total']}")

    # Real types, not strings - the whole difference from the CSV.
    if detail.max_row > 1:
        sample = next(detail.iter_rows(min_row=2, max_row=2))
        results.check("les dates sont de vraies dates Excel",
                      isinstance(sample[8].value, (datetime, date)),
                      str(type(sample[8].value)))
        results.check("les montants sont de vrais nombres",
                      isinstance(sample[13].value, (int, float)),
                      str(type(sample[13].value)))
        results.check("le taux est une fraction affichee en pourcentage",
                      sample[11].number_format == commissions_xlsx.RATE_FORMAT
                      and 0 < sample[11].value < 1,
                      f"{sample[11].value} / {sample[11].number_format}")
        results.check("les statuts sont des libelles, pas des codes",
                      sample[16].value in ("Calculated", "Validated", "Paid",
                                           "Cancelled"),
                      str(sample[16].value))
        results.check("format d'affichage des dates JJ/MM/AAAA",
                      sample[8].number_format == commissions_xlsx.DATE_FORMAT,
                      sample[8].number_format)

    # Periods must fall inside the month the file claims.
    outside = [row[17] for row in detail.iter_rows(min_row=2, values_only=True)
               if row[17] is not None
               and (row[17].year, row[17].month) != (year, month)]
    results.check("toutes les commissions ont ete calculees dans le mois",
                  not outside, f"{len(outside)} hors periode")

    manifest = json.loads(result["manifest"].read_text(encoding="utf-8"))
    results.check("le manifeste decrit les deux feuilles",
                  set(manifest["sheets"]) == {"Commissions", "Summary"},
                  str(manifest["sheets"]))
    results.check("le manifeste annonce un snapshot restatable",
                  manifest["restatable"] is True
                  and "snapshot" in manifest["extraction_mode"],
                  manifest["extraction_mode"])
    workbook.close()


def _xlsx_determinism(cursor, results, root, period):
    year, month = period
    section("7. DETERMINISME DU CLASSEUR")
    path = commissions_xlsx.output_path(root, year, month)

    first_content = commissions_xlsx.content_digest(path)
    commissions_xlsx.export_month(cursor, root, year, month)
    second_content = commissions_xlsx.content_digest(path)

    results.check("relancer le meme mois produit le meme CONTENU",
                  first_content == second_content,
                  f"{first_content[:16]} vs {second_content[:16]}")

    manifest = json.loads(
        path.with_suffix(path.suffix + ".manifest.json")
        .read_text(encoding="utf-8"))
    results.check("le manifeste publie ce checksum de contenu",
                  manifest["content_sha256"] == second_content)
    results.check("les proprietes du classeur ne portent pas d'horloge reelle",
                  _properties_stamp(path).year == year,
                  str(_properties_stamp(path)))


def _properties_stamp(path):
    from openpyxl import load_workbook
    workbook = load_workbook(path)
    stamp = workbook.properties.created
    workbook.close()
    return stamp


def _no_duplicate_files(cursor, results, root, day, period):
    section("8. ABSENCE DE FICHIERS CONTRADICTOIRES")
    year, month = period

    for _ in range(3):
        expenses_csv.export_day(cursor, root, day)
        commissions_xlsx.export_month(cursor, root, year, month)

    csv_files = list((root / "daily" / "expenses").rglob(
        f"EXPENSES_{day:%Y%m%d}*"))
    csv_data = [path for path in csv_files if path.suffix == ".csv"]
    results.check("trois executions : un seul CSV pour la journee",
                  len(csv_data) == 1, str([p.name for p in csv_data]))

    xlsx_files = [path for path in
                  (root / "monthly" / "commissions").rglob("*.xlsx")
                  if f"{year}{month:02d}" in path.name]
    results.check("trois executions : un seul classeur pour le mois",
                  len(xlsx_files) == 1, str([p.name for p in xlsx_files]))

    results.check("aucun suffixe de version parasite (_v2, _1, copy)",
                  not any(marker in path.name.lower()
                          for path in csv_files + xlsx_files
                          for marker in ("_v2", "(1)", "copy", "-bis")),
                  str([p.name for p in csv_files]))


def _read_only(cursor, results, before):
    section("9. AUCUNE MODIFICATION DE LA BASE")
    after = _counts(cursor)
    results.check("aucun compteur de table n'a bouge", before == after,
                  f"{before} -> {after}")

    # The export script asks PostgreSQL itself to refuse writes. Prove the
    # setting actually bites rather than trusting the flag.
    cursor.execute("SET default_transaction_read_only = on")
    refused = False
    try:
        cursor.execute(
            "UPDATE expenses SET updated_at = updated_at "
            "WHERE expense_id = (SELECT expense_id FROM expenses LIMIT 1)")
    except psycopg.Error:
        refused = True
    cursor.execute("SET default_transaction_read_only = off")
    results.check("une ecriture est refusee par le serveur en mode lecture seule",
                  refused, "l'UPDATE est passe")


if __name__ == "__main__":
    sys.exit(main())

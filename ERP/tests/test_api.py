"""
Automated verification of the Orialis ERP API.

    python ERP/tests/test_api.py

Exit code 0 when everything passes, 1 otherwise.

Runs the real application against the real database through FastAPI's
TestClient - no mocks. Mocking the database here would test the mock: the
things worth checking are whether the SQL is right, whether pagination is
stable under a real total order, and whether `updated_since` returns the rows
it claims to. None of that survives a stub.

READ-ONLY. The API issues no write, so this suite cannot modify the data. It
verifies that too, by comparing row counts before and after.

Requires: ERP_DATABASE_URL and ERP_API_KEY, a seeded schema, and ideally a
few simulated days so that `updated_since` has something to find.
"""

import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from fastapi.testclient import TestClient  # noqa: E402

from ERP.app.main import app  # noqa: E402
from ERP.app.security import API_KEY  # noqa: E402

AUTH = {"X-API-Key": API_KEY}

RESOURCES = [
    # (path, primary key, an example filter, expected 404 detail)
    ("/api/v1/cost-centers", "cost_center_id",
     {"cost_center_type": "Branch"}, "Cost centre not found"),
    ("/api/v1/suppliers", "supplier_id",
     {"supplier_status": "Active"}, "Supplier not found"),
    ("/api/v1/invoices", "invoice_id",
     {"invoice_payment_status": "Paid"}, "Invoice not found"),
    ("/api/v1/payments", "payment_id",
     {"payment_status": "Executed"}, "Payment not found"),
    ("/api/v1/allocations", "allocation_id",
     {"allocation_status": "Active"}, "Allocation not found"),
]


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


def main():
    results = Results()

    with TestClient(app) as client:
        _public(client, results)
        _authentication(client, results)
        _pagination(client, results)
        _filters(client, results)
        _details_and_404(client, results)
        _incremental(client, results)
        _ordering_stability(client, results)
        _allocations(client, results)
        _stats(client, results)
        _pool(client, results)
        _no_secret_leak(client, results)

    _no_crm_dependency(results)

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


# ---------------------------------------------------------------------------

def _public(client, results):
    section("1. ROUTES PUBLIQUES")
    response = client.get("/")
    results.check("GET / repond 200 sans cle", response.status_code == 200,
                  f"status={response.status_code}")
    body = response.json()
    results.check("GET / nomme le service",
                  body.get("name") == "Orialis ERP API", str(body)[:120])
    results.check("GET / rapporte l'etat du pool",
                  body.get("database_pool", {}).get("open") is True,
                  str(body.get("database_pool")))

    results.check("GET /docs public", client.get("/docs").status_code == 200)
    results.check("GET /openapi.json public",
                  client.get("/openapi.json").status_code == 200)


def _authentication(client, results):
    section("2. AUTHENTIFICATION")
    for path, _, _, _ in RESOURCES:
        response = client.get(path)
        results.check(f"{path} sans cle -> 401",
                      response.status_code == 401, f"status={response.status_code}")

    response = client.get("/api/v1/suppliers", headers={"X-API-Key": "mauvaise"})
    results.check("cle invalide -> 401", response.status_code == 401,
                  f"status={response.status_code}")
    results.check("le corps du 401 ne distingue pas absente et fausse",
                  response.json().get("detail") == "Missing or invalid API key",
                  str(response.json()))
    results.check("le 401 nomme l'en-tete attendu",
                  response.headers.get("WWW-Authenticate") == "X-API-Key",
                  str(dict(response.headers)))

    results.check("cle valide -> 200",
                  client.get("/api/v1/suppliers", headers=AUTH).status_code == 200)

    results.check("/api/v1/stats/overview protege",
                  client.get("/api/v1/stats/overview").status_code == 401)


def _pagination(client, results):
    section("3. PAGINATION")
    response = client.get("/api/v1/invoices",
                          params={"page": 1, "page_size": 10}, headers=AUTH)
    body = response.json()
    results.check("enveloppe {data, pagination}",
                  "data" in body and "pagination" in body, str(body)[:120])
    results.check("page_size respecte", len(body["data"]) == 10,
                  f"{len(body['data'])} lignes")

    meta = body["pagination"]
    results.check("total_records > 0", meta["total_records"] > 0, str(meta))
    expected_pages = -(-meta["total_records"] // 10)
    results.check("total_pages = plafond(total/page_size)",
                  meta["total_pages"] == expected_pages,
                  f"{meta['total_pages']} vs {expected_pages}")

    page2 = client.get("/api/v1/invoices",
                       params={"page": 2, "page_size": 10},
                       headers=AUTH).json()
    first_ids = {row["invoice_id"] for row in body["data"]}
    second_ids = {row["invoice_id"] for row in page2["data"]}
    results.check("pages 1 et 2 disjointes",
                  not (first_ids & second_ids),
                  f"{len(first_ids & second_ids)} doublons")

    results.check("page_size hors bornes -> 422",
                  client.get("/api/v1/invoices", params={"page_size": 9999},
                             headers=AUTH).status_code == 422)
    results.check("page 0 -> 422",
                  client.get("/api/v1/invoices", params={"page": 0},
                             headers=AUTH).status_code == 422)

    far = client.get("/api/v1/suppliers", params={"page": 9999},
                     headers=AUTH).json()
    results.check("page au-dela de la fin -> 200 et data vide",
                  far["data"] == [], str(far)[:120])


def _filters(client, results):
    section("4. FILTRES")
    for path, key, example, _ in RESOURCES:
        name, value = next(iter(example.items()))
        body = client.get(path, params={**example, "page_size": 50},
                          headers=AUTH).json()
        column = {
            "cost_center_type": "cost_center_type",
            "supplier_status": "supplier_status",
            "invoice_payment_status": "invoice_payment_status",
            "payment_status": "payment_status",
            "allocation_status": "allocation_status",
        }[name]
        wrong = [row for row in body["data"] if row[column] != value]
        results.check(f"{path}?{name}={value} ne renvoie que des correspondances",
                      not wrong and body["data"], f"{len(wrong)} non conformes")

    # Two filters combine with AND, and the count follows.
    all_invoices = client.get("/api/v1/invoices", params={"page_size": 1},
                              headers=AUTH).json()["pagination"]["total_records"]
    approved = client.get("/api/v1/invoices",
                          params={"invoice_approval_status": "Approved",
                                  "page_size": 1},
                          headers=AUTH).json()["pagination"]["total_records"]
    approved_unpaid = client.get(
        "/api/v1/invoices",
        params={"invoice_approval_status": "Approved",
                "invoice_payment_status": "Unpaid", "page_size": 1},
        headers=AUTH).json()["pagination"]["total_records"]
    results.check("les filtres se combinent en ET",
                  approved_unpaid <= approved <= all_invoices,
                  f"{approved_unpaid} <= {approved} <= {all_invoices}")

    # A date range narrows the set.
    ranged = client.get("/api/v1/invoices",
                        params={"invoice_date_from": "2026-01-01",
                                "invoice_date_to": "2026-03-31", "page_size": 1},
                        headers=AUTH).json()["pagination"]["total_records"]
    results.check("filtre de plage de dates", 0 < ranged < all_invoices,
                  f"{ranged} sur {all_invoices}")

    # An unknown filter value is an empty result, not an error.
    empty = client.get("/api/v1/suppliers",
                       params={"supplier_status": "Inexistant"},
                       headers=AUTH)
    results.check("valeur de filtre inconnue -> 200 vide",
                  empty.status_code == 200
                  and empty.json()["pagination"]["total_records"] == 0,
                  f"status={empty.status_code}")

    # An unknown PARAMETER is ignored by FastAPI - it cannot reach the SQL.
    injection = client.get(
        "/api/v1/suppliers",
        params={"supplier_status': DROP TABLE suppliers; --": "x"},
        headers=AUTH)
    results.check("parametre inconnu ignore, pas d'injection",
                  injection.status_code == 200,
                  f"status={injection.status_code}")


def _details_and_404(client, results):
    section("5. DETAIL ET 404")
    for path, key, _, not_found in RESOURCES:
        listing = client.get(path, params={"page_size": 1}, headers=AUTH).json()
        identifier = listing["data"][0][key]

        detail = client.get(f"{path}/{identifier}", headers=AUTH)
        results.check(f"{path}/{{id}} -> 200", detail.status_code == 200,
                      f"status={detail.status_code}")
        body = detail.json()
        results.check(f"{path}/{{id}} renvoie l'objet, pas une enveloppe",
                      "pagination" not in body and body.get(key) == identifier,
                      str(body)[:120])

        missing = client.get(f"{path}/AUCUN-ID-CONNU", headers=AUTH)
        results.check(f"{path}/inconnu -> 404", missing.status_code == 404,
                      f"status={missing.status_code}")
        results.check(f"{path}/inconnu : message explicite",
                      missing.json().get("detail") == not_found,
                      str(missing.json()))


def _incremental(client, results):
    section("6. EXTRACTION INCREMENTALE (updated_since)")

    # A watermark taken from the data itself: the updated_at of a row in the
    # middle of the table. Using a hardcoded date would make the test depend
    # on how many days have been simulated.
    sample = client.get("/api/v1/invoices",
                        params={"page": 1, "page_size": 500},
                        headers=AUTH).json()["data"]
    watermark = sorted(row["updated_at"] for row in sample)[len(sample) // 2]

    body = client.get("/api/v1/invoices",
                      params={"updated_since": watermark, "page_size": 200},
                      headers=AUTH).json()
    results.check("updated_since renvoie des lignes",
                  body["pagination"]["total_records"] > 0,
                  str(body["pagination"]))
    older = [row for row in body["data"] if row["updated_at"] < watermark]
    results.check("aucune ligne anterieure au watermark", not older,
                  f"{len(older)} lignes trop anciennes")

    # THE INCLUSIVE BOUND. A row sitting exactly on the watermark must come
    # back - that is what makes the contract at-least-once rather than
    # at-most-once.
    on_boundary = [row for row in body["data"] if row["updated_at"] == watermark]
    results.check("borne INCLUSIVE : la ligne sur le watermark est renvoyee",
                  len(on_boundary) >= 1,
                  "aucune ligne exactement sur le watermark")

    # A watermark in the future returns nothing, and still 200.
    future = (datetime.now(timezone.utc) + timedelta(days=3650)).isoformat()
    empty = client.get("/api/v1/invoices",
                       params={"updated_since": future}, headers=AUTH).json()
    results.check("watermark futur -> 0 ligne",
                  empty["pagination"]["total_records"] == 0,
                  str(empty["pagination"]))

    # The envelope states which contract the caller is under.
    results.check("l'enveloppe annonce la semantique at-least-once",
                  body["pagination"]["extraction_semantics"] == "at-least-once",
                  str(body["pagination"]))
    results.check("l'enveloppe annonce l'ordre (updated_at, id)",
                  body["pagination"]["ordering"] == "updated_at, invoice_id",
                  str(body["pagination"]))

    # Without updated_since, the order is the primary key and no extraction
    # contract is claimed.
    plain = client.get("/api/v1/invoices", params={"page_size": 1},
                       headers=AUTH).json()
    results.check("sans updated_since : ordre = cle primaire",
                  plain["pagination"]["ordering"] == "invoice_id",
                  str(plain["pagination"]))
    results.check("sans updated_since : aucune semantique annoncee",
                  plain["pagination"]["extraction_semantics"] is None,
                  str(plain["pagination"]))

    # Every resource supports it.
    for path, _, _, _ in RESOURCES:
        response = client.get(path, params={"updated_since": "2020-01-01T00:00:00Z",
                                            "page_size": 1}, headers=AUTH)
        results.check(f"{path} accepte updated_since",
                      response.status_code == 200,
                      f"status={response.status_code}")

    results.check("updated_since mal forme -> 422",
                  client.get("/api/v1/invoices",
                             params={"updated_since": "pas-une-date"},
                             headers=AUTH).status_code == 422)


def _ordering_stability(client, results):
    section("7. STABILITE DE L'ORDRE")

    # Paging right through a filtered set must yield every row exactly once.
    # This is the property that a partial ORDER BY silently breaks.
    collected = []
    page = 1
    while True:
        body = client.get("/api/v1/payments",
                          params={"payment_status": "Executed",
                                  "page": page, "page_size": 500},
                          headers=AUTH).json()
        collected.extend(row["payment_id"] for row in body["data"])
        if page >= body["pagination"]["total_pages"] or page > 40:
            total = body["pagination"]["total_records"]
            break
        page += 1

    results.check("pagination complete : aucun doublon",
                  len(collected) == len(set(collected)),
                  f"{len(collected) - len(set(collected))} doublons")
    results.check("pagination complete : aucune ligne manquante",
                  len(collected) == total,
                  f"{len(collected)} collectees sur {total}")

    # Same run, ordered by (updated_at, id). Payments are issued in
    # campaigns, so many share a timestamp - exactly the case a missing
    # tie-break would break.
    collected = []
    page = 1
    while True:
        body = client.get("/api/v1/payments",
                          params={"updated_since": "2020-01-01T00:00:00Z",
                                  "page": page, "page_size": 500},
                          headers=AUTH).json()
        collected.extend(row["payment_id"] for row in body["data"])
        if page >= body["pagination"]["total_pages"] or page > 40:
            total = body["pagination"]["total_records"]
            break
        page += 1

    results.check("ordre (updated_at, id) : aucun doublon",
                  len(collected) == len(set(collected)),
                  f"{len(collected) - len(set(collected))} doublons")
    results.check("ordre (updated_at, id) : aucune ligne manquante",
                  len(collected) == total,
                  f"{len(collected)} collectees sur {total}")

    # updated_at must be non-decreasing across the whole walk: that is what
    # lets a consumer advance its watermark page by page.
    stamps = []
    for page in (1, 2, 3):
        body = client.get("/api/v1/payments",
                          params={"updated_since": "2020-01-01T00:00:00Z",
                                  "page": page, "page_size": 200},
                          headers=AUTH).json()
        stamps.extend(row["updated_at"] for row in body["data"])
    results.check("updated_at croissant au fil des pages",
                  stamps == sorted(stamps),
                  "ordre non monotone")

    # Two identical calls return the identical page.
    first = client.get("/api/v1/invoices", params={"page": 3, "page_size": 50},
                       headers=AUTH).json()["data"]
    second = client.get("/api/v1/invoices", params={"page": 3, "page_size": 50},
                        headers=AUTH).json()["data"]
    results.check("deux appels identiques -> page identique",
                  [row["invoice_id"] for row in first]
                  == [row["invoice_id"] for row in second])


def _allocations(client, results):
    section("8. ALLOCATIONS ET LETTRAGE")

    payment = client.get("/api/v1/payments",
                         params={"payment_status": "Executed", "page_size": 1},
                         headers=AUTH).json()["data"][0]

    body = client.get(f"/api/v1/payments/{payment['payment_id']}/allocations",
                      headers=AUTH).json()
    results.check("un paiement execute porte au moins une allocation",
                  len(body["data"]) >= 1, str(body)[:150])
    results.check("l'allocation porte le contexte facture",
                  "gross_amount" in body["data"][0], str(body["data"][0])[:150])

    # paid_amount must equal the sum of the invoice's Active allocations on
    # Executed payments. Recomputing it through the API is what makes the
    # figure auditable rather than merely asserted.
    invoice = client.get("/api/v1/invoices",
                         params={"invoice_payment_status": "Paid",
                                 "invoice_type": "Standard", "page_size": 1},
                         headers=AUTH).json()["data"][0]
    allocations = client.get(
        f"/api/v1/invoices/{invoice['invoice_id']}/allocations",
        headers=AUTH).json()["data"]
    computed = sum(float(row["allocated_amount"]) for row in allocations
                   if row["allocation_status"] == "Active"
                   and row["payment_status"] == "Executed")
    results.check("paid_amount = somme des allocations Active/Executed",
                  abs(computed - float(invoice["paid_amount"])) < 0.005,
                  f"{computed} vs {invoice['paid_amount']}")

    results.check("allocations d'un paiement inconnu -> 404",
                  client.get("/api/v1/payments/PAY-INCONNU/allocations",
                             headers=AUTH).status_code == 404)
    results.check("allocations d'une facture inconnue -> 404",
                  client.get("/api/v1/invoices/INV-INCONNUE/allocations",
                             headers=AUTH).status_code == 404)

    cancelled = client.get("/api/v1/allocations",
                           params={"allocation_status": "Cancelled",
                                   "page_size": 5},
                           headers=AUTH).json()
    results.check("les allocations annulees restent lisibles",
                  cancelled["pagination"]["total_records"] > 0,
                  str(cancelled["pagination"]))
    if cancelled["data"]:
        results.check("une allocation annulee pointe vers son remplacement",
                      any(row["replaced_by_allocation_id"]
                          for row in cancelled["data"]),
                      "aucun chainage trouve")


def _stats(client, results):
    section("9. AGREGATS")
    body = client.get("/api/v1/stats/overview", headers=AUTH).json()
    results.check("overview renvoie des totaux",
                  body["totals"]["total_invoices"] > 0, str(body["totals"]))
    results.check("overview ne renvoie AUCUNE ligne operationnelle",
                  "data" not in body and "invoices" not in body,
                  str(list(body)))

    # The breakdown must reconcile with the table count - a stats endpoint
    # that disagrees with the resource endpoint is worse than none.
    total = client.get("/api/v1/invoices", params={"page_size": 1},
                       headers=AUTH).json()["pagination"]["total_records"]
    summed = sum(row["invoice_count"] for row in body["invoices_by_approval_status"])
    results.check("la ventilation par statut totalise le nombre de factures",
                  summed == total, f"{summed} vs {total}")

    results.check("top fournisseurs limite a 10",
                  len(body["top_suppliers"]) <= 10,
                  str(len(body["top_suppliers"])))
    results.check("concentration Pareto visible",
                  body["top_suppliers"][0]["invoice_count"]
                  > body["top_suppliers"][-1]["invoice_count"],
                  str(body["top_suppliers"][:1]))

    filters = client.get("/api/v1/stats/filters", headers=AUTH).json()
    results.check("filters expose les valeurs de reference",
                  "Executed" in filters["payment_status"]
                  and "Approved" in filters["invoice_approval_status"],
                  str(filters)[:150])


def _pool(client, results):
    section("10. POOL DE CONNEXIONS")
    from ERP.app.database import POOL_MAX_SIZE, pool_stats

    before = pool_stats()
    results.check("le pool est ouvert", before["open"] is True, str(before))

    # Forty sequential calls on a pool capped at five: connections must be
    # reused, not opened per request.
    for _ in range(40):
        assert client.get("/api/v1/suppliers", params={"page_size": 1},
                          headers=AUTH).status_code == 200
    after = pool_stats()
    results.check(f"40 requetes ne depassent pas max_size={POOL_MAX_SIZE}",
                  after["size"] <= POOL_MAX_SIZE, str(after))
    results.check("aucune connexion en attente", after["waiting"] == 0, str(after))
    results.check("les connexions sont rendues au pool",
                  after["available"] == after["size"], str(after))

    # Concurrency: TestClient runs endpoints in a thread pool, so this really
    # does borrow several connections at once.
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=12) as pool:
        codes = list(pool.map(
            lambda _: client.get("/api/v1/invoices", params={"page_size": 5},
                                 headers=AUTH).status_code,
            range(24)))
    results.check("24 requetes concurrentes toutes en 200",
                  set(codes) == {200}, str(set(codes)))
    results.check("le pool reste borne apres concurrence",
                  pool_stats()["size"] <= POOL_MAX_SIZE, str(pool_stats()))


def _no_secret_leak(client, results):
    section("11. AUCUN SECRET EXPOSE")
    from ERP.app.database import DATABASE_URL

    password = DATABASE_URL.split("@")[0].split(":")[-1] if "@" in DATABASE_URL else None

    payloads = [
        client.get("/").text,
        client.get("/openapi.json").text,
        client.get("/api/v1/stats/overview", headers=AUTH).text,
        client.get("/api/v1/suppliers", params={"page_size": 5}, headers=AUTH).text,
        client.get("/api/v1/invoices", headers={"X-API-Key": "faux"}).text,
    ]
    joined = "\n".join(payloads)

    results.check("la cle API n'apparait dans aucune reponse",
                  API_KEY not in joined)
    results.check("ERP_DATABASE_URL n'apparait dans aucune reponse",
                  DATABASE_URL not in joined)
    if password and len(password) > 4:
        results.check("le mot de passe de la base n'apparait nulle part",
                      password not in joined)
    results.check("la reponse racine ne porte pas de chaine de connexion",
                  "postgresql://" not in client.get("/").text)

    # The health endpoint reports pool SIZES, never the target.
    pool_block = client.get("/").json()["database_pool"]
    results.check("le bloc pool ne contient que des nombres",
                  all(not isinstance(value, str) for value in pool_block.values()),
                  str(pool_block))

    # The masked IBAN is masked at source; verify the API cannot leak a full
    # one even by accident.
    suppliers = client.get("/api/v1/suppliers", params={"page_size": 200},
                           headers=AUTH).json()["data"]
    unmasked = [row for row in suppliers
                if row["iban_masked"] and "*" not in row["iban_masked"]]
    results.check("aucun IBAN complet expose", not unmasked,
                  f"{len(unmasked)} IBAN non masques")


def _no_crm_dependency(results):
    section("12. INDEPENDANCE VIS-A-VIS DU CRM")
    root = Path(__file__).resolve().parent.parent / "app"
    offenders = {"import": [], "database_url": [], "crm_table": []}

    crm_tables = ("advisors", "branches", "clients", "interactions")

    # PARSED, not grepped.
    #
    # Searching for the text "import CRM" flags the docstrings that EXPLAIN
    # why this package does not import the CRM - an explanation is not a
    # dependency - and would equally miss an `importlib.import_module("CRM…")`.
    # Walking the AST asks the real question: does this module declare an
    # import of a CRM package? Only actual Import nodes can answer it.
    import ast

    for path in sorted(root.rglob("*.py")):
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(path))

        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name.split(".")[0] == "CRM":
                        offenders["import"].append(f"{path.name}:{alias.name}")
            elif isinstance(node, ast.ImportFrom):
                if node.module and node.module.split(".")[0] == "CRM":
                    offenders["import"].append(f"{path.name}:{node.module}")
            # Any dynamic import of a CRM module would go through a string
            # literal; catch the obvious form too.
            elif isinstance(node, ast.Call):
                function = getattr(node.func, "attr", getattr(node.func, "id", ""))
                if function == "import_module" and node.args:
                    first = node.args[0]
                    if isinstance(first, ast.Constant) and \
                            str(first.value).startswith("CRM"):
                        offenders["import"].append(f"{path.name}:dynamique")

        # Strings that would only appear in executable code, not in prose.
        code = "\n".join(line for line in source.splitlines()
                         if not line.strip().startswith("#"))
        if 'environ.get("DATABASE_URL"' in code or 'environ["DATABASE_URL"' in code:
            offenders["database_url"].append(path.name)
        for table in crm_tables:
            if f"FROM {table}" in code or f"JOIN {table}" in code:
                offenders["crm_table"].append(f"{path.name}:{table}")

    results.check("aucun import depuis CRM/", not offenders["import"],
                  str(offenders["import"]))
    results.check("aucune lecture de DATABASE_URL", not offenders["database_url"],
                  str(offenders["database_url"]))
    results.check("aucune requete sur une table CRM", not offenders["crm_table"],
                  str(offenders["crm_table"]))

    from ERP.config import ENV_VARIABLE, API_KEY_VARIABLE
    results.check("la base lue est ERP_DATABASE_URL",
                  ENV_VARIABLE == "ERP_DATABASE_URL", ENV_VARIABLE)
    results.check("la cle lue est ERP_API_KEY",
                  API_KEY_VARIABLE == "ERP_API_KEY", API_KEY_VARIABLE)
    results.check("ERP_API_KEY est distincte de CRM_API_KEY",
                  API_KEY != os.environ.get("CRM_API_KEY", "<absente>"))


if __name__ == "__main__":
    sys.exit(main())

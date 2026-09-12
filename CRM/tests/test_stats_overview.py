"""Verification of GET /api/v1/stats/overview.

The whole point of this endpoint is that PostgreSQL computes the numbers.
So the only test worth writing is the one that asks PostgreSQL the same
questions independently and compares the two answers. A test that merely
checked the response had the right shape would pass just as happily on
wrong figures.

Every aggregate is therefore recomputed here with its own SQL, written
separately from the endpoint's - not imported from it. Importing the module's
constants would test that a string equals itself.

Run it with the API up:

    python -m uvicorn CRM.app.main:app          # in one terminal
    python CRM/tests/test_stats_overview.py     # in another

Read-only from start to finish: it opens its own connection, runs SELECTs,
and writes nothing.

Exit code 0 if every check passes, 1 otherwise.
"""

import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

import psycopg  # noqa: E402
from psycopg.rows import dict_row  # noqa: E402

from CRM.config import get_api_key, get_database_url, scrub  # noqa: E402

BASE_URL = os.environ.get("CRM_API_URL", "http://127.0.0.1:8000").rstrip("/")

# Collects one line per check so the summary can report a count rather than
# "it seemed fine".
results = []


def check(label, expected, actual):
    """Record one comparison and print it immediately."""
    passed = expected == actual
    results.append(passed)
    mark = "OK  " if passed else "FAIL"
    print(f"  [{mark}] {label}")
    if not passed:
        print(f"         PostgreSQL : {expected!r}")
        print(f"         API        : {actual!r}")
    return passed


def call_api(path, api_key):
    """GET one path from the API, returning (status, parsed body)."""
    request = urllib.request.Request(BASE_URL + path)
    request.add_header("X-API-Key", api_key)
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            return response.status, json.loads(response.read().decode())
    except urllib.error.HTTPError as error:
        return error.code, json.loads(error.read().decode())


def main():
    api_key = get_api_key()
    database_url = get_database_url()

    print("Verifying /api/v1/stats/overview")
    print(f"  API      : {BASE_URL}")
    print("  Database : the same PostgreSQL the API reads, queried directly")
    print()

    # -- The endpoint answers at all --------------------------------------
    try:
        status, payload = call_api("/api/v1/stats/overview", api_key)
    except urllib.error.URLError as error:
        print(f"  Cannot reach the API at {BASE_URL}: {error.reason}")
        print("  Start it first:  python -m uvicorn CRM.app.main:app")
        return 1

    if status != 200:
        print(f"  The endpoint answered {status}, expected 200: {payload}")
        return 1

    print("== Authentication ==")
    # No key at all must be refused. Declared once on the parent router, so
    # this also confirms the new router inherited the dependency instead of
    # quietly opening a hole in an otherwise protected API.
    unauthenticated = urllib.request.Request(BASE_URL + "/api/v1/stats/overview")
    try:
        with urllib.request.urlopen(unauthenticated, timeout=30) as response:
            check("no X-API-Key is rejected", 401, response.status)
    except urllib.error.HTTPError as error:
        check("no X-API-Key is rejected", 401, error.code)

    status_wrong, _ = call_api("/api/v1/stats/overview", "not-the-real-key")
    check("a wrong X-API-Key is rejected", 401, status_wrong)
    print()

    with psycopg.connect(database_url, row_factory=dict_row) as connection:
        cursor = connection.cursor

        # -- Headline counts ----------------------------------------------
        print("== Totals ==")
        totals = payload["totals"]

        with cursor() as cur:
            cur.execute("SELECT COUNT(*) AS n FROM clients")
            check("total_clients", cur.fetchone()["n"], totals["total_clients"])

            cur.execute(
                "SELECT COUNT(*) AS n FROM clients WHERE client_status = %s",
                ("Active",),
            )
            check("active_clients", cur.fetchone()["n"], totals["active_clients"])

            cur.execute("SELECT COUNT(*) AS n FROM advisors")
            check("total_advisors", cur.fetchone()["n"], totals["total_advisors"])

            cur.execute("SELECT COUNT(*) AS n FROM interactions")
            check(
                "total_interactions",
                cur.fetchone()["n"],
                totals["total_interactions"],
            )
        print()

        # -- Breakdowns ----------------------------------------------------
        # Compared as ordered lists, not as sets: the endpoint promises a
        # deterministic order (count descending, then label), and the charts
        # depend on it. Comparing sets would let a reshuffle through.
        print("== Client breakdowns ==")
        BREAKDOWNS = [
            ("clients_by_segment", "client_segment", "segment"),
            ("clients_by_country", "country_of_residence", "country"),
            ("clients_by_risk_profile", "risk_profile", "risk_profile"),
        ]
        for key, column, label in BREAKDOWNS:
            with cursor() as cur:
                cur.execute(
                    f"SELECT {column} AS label, COUNT(*) AS client_count "
                    f"FROM clients GROUP BY {column} "
                    f"ORDER BY client_count DESC, label"
                )
                expected = [
                    {label: row["label"], "client_count": row["client_count"]}
                    for row in cur.fetchall()
                ]
            check(f"{key} (values, counts and order)", expected, payload[key])

            # The parts must add up to the whole. This catches a GROUP BY
            # that silently dropped NULLs, which a row-by-row comparison
            # against the same faulty assumption would not.
            check(
                f"{key} sums to total_clients",
                totals["total_clients"],
                sum(row["client_count"] for row in payload[key]),
            )
        print()

        # -- Top advisors --------------------------------------------------
        print("== Top advisors ==")
        with cursor() as cur:
            cur.execute("""
                SELECT a.advisor_id, a.first_name, a.last_name,
                       COUNT(c.client_id) AS client_count
                FROM advisors a
                LEFT JOIN clients c ON c.advisor_id = a.advisor_id
                GROUP BY a.advisor_id, a.first_name, a.last_name
                ORDER BY client_count DESC, a.advisor_id
                LIMIT 5
            """)
            expected_top = [dict(row) for row in cur.fetchall()]
        check("top_advisors (default limit of 5)", expected_top, payload["top_advisors"])

        # The ranking must actually be a ranking.
        counts = [row["client_count"] for row in payload["top_advisors"]]
        check("top_advisors is sorted by client_count descending",
              sorted(counts, reverse=True), counts)

        # Only the fields that exist in the CRM, and no invented metric.
        expected_fields = {"advisor_id", "first_name", "last_name", "client_count"}
        check("top_advisors exposes exactly the available fields",
              expected_fields, set(payload["top_advisors"][0]))

        # The limit is a real parameter, not decoration.
        _, limited = call_api("/api/v1/stats/overview?top_advisors_limit=3", api_key)
        check("top_advisors_limit=3 returns 3 advisors",
              3, len(limited["top_advisors"]))

        status_invalid, _ = call_api(
            "/api/v1/stats/overview?top_advisors_limit=0", api_key
        )
        check("top_advisors_limit=0 is rejected", 422, status_invalid)

        status_invalid, _ = call_api(
            "/api/v1/stats/overview?top_advisors_limit=999", api_key
        )
        check("top_advisors_limit=999 is rejected", 422, status_invalid)
        print()

        # -- The separation this endpoint was designed around --------------
        print("== Aggregates stay separate from operational data ==")
        # Recent interactions deliberately live on /api/v1/interactions. If
        # an operational list ever appears here, the contract has drifted.
        operational_keys = [
            key for key, value in payload.items()
            if key not in {"totals"}
            and isinstance(value, list)
            and value
            and "client_count" not in value[0]
        ]
        check("no operational record list in the payload", [], operational_keys)
        check(
            "top-level keys are exactly the agreed aggregates",
            {"totals", "clients_by_segment", "clients_by_country",
             "clients_by_risk_profile", "top_advisors"},
            set(payload),
        )
        print()

        # -- The other endpoints still work --------------------------------
        print("== The eight existing endpoints are untouched ==")
        with cursor() as cur:
            cur.execute("SELECT client_id FROM clients ORDER BY client_id LIMIT 1")
            a_client = cur.fetchone()["client_id"]
            cur.execute("SELECT advisor_id FROM advisors ORDER BY advisor_id LIMIT 1")
            an_advisor = cur.fetchone()["advisor_id"]
            cur.execute("SELECT branch_id FROM branches ORDER BY branch_id LIMIT 1")
            a_branch = cur.fetchone()["branch_id"]
            cur.execute(
                "SELECT interaction_id FROM interactions ORDER BY interaction_id LIMIT 1"
            )
            an_interaction = cur.fetchone()["interaction_id"]

        for path in [
            "/api/v1/branches",
            f"/api/v1/branches/{a_branch}",
            "/api/v1/advisors?page_size=1",
            f"/api/v1/advisors/{an_advisor}",
            "/api/v1/clients?page_size=1",
            f"/api/v1/clients/{a_client}",
            "/api/v1/interactions?page_size=1",
            f"/api/v1/interactions/{an_interaction}",
        ]:
            status_existing, _ = call_api(path, api_key)
            check(f"GET {path}", 200, status_existing)

    print()
    passed = sum(results)
    total = len(results)
    print(f"{passed}/{total} checks passed")
    return 0 if passed == total else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except SystemExit:
        raise
    except Exception as error:  # noqa: BLE001
        # Driver errors name the host, which helps; they must never name the
        # password.
        print(f"  Unexpected failure: {scrub(error, get_database_url())}")
        sys.exit(1)

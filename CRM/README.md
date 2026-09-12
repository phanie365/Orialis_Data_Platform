# Orialis CRM

Orialis CRM is the customer relationship management source system of the
Orialis Data Platform project.

Orialis is a fictional international wealth management company created as part
of an end-to-end Data Engineering project.

This CRM simulates the operational system used to manage clients, advisors,
branches and client-advisor interactions. It is a **simulated operational
source**: its purpose is to behave like a real upstream system that a data
platform has to read from.

> Current version: V2 — PostgreSQL on Supabase

## Overview

The CRM currently manages four entities:

- **Branches** — Orialis offices across France, Switzerland, Belgium and Italy
- **Advisors** — wealth management advisors attached to a branch
- **Clients** — individual clients assigned to an advisor
- **Interactions** — historical and daily interactions between clients and advisors

## Current Dataset

| Table | Rows |
|---|---|
| `branches` | 7 |
| `advisors` | 100 |
| `clients` | 5,000 |
| `interactions` | **30,130** |

The interaction count is made of the 30,000 initial historical rows plus the
rows added by the daily activity simulator on each of its runs. It therefore
grows over time, which is the point: the CRM is meant to evolve, not to stay
still.

All data is synthetic and generated specifically for this project.

## Architecture

The CRM currently runs on:

- **Python**
- **FastAPI** — REST API
- **Uvicorn** — ASGI server
- **psycopg 3** — PostgreSQL driver, with **psycopg_pool** for the API
- **PostgreSQL hosted on Supabase** — the database

```
HTTP client  ──►  FastAPI  ──►  connection pool  ──►  PostgreSQL / Supabase

Seeds / Simulator  ─────────►  psycopg 3  ─────────►  PostgreSQL / Supabase
```

Both halves — the API and the command-line scripts — read the same connection
string from `CRM/config.py`, which loads it from a local `.env` file. The
scripts open a single connection and exit; only the API pools.

### Connection pool

The API borrows its connections from a `psycopg_pool.ConnectionPool` instead
of opening a new one on every request. The pool is opened when the
application starts and closed when it stops, through FastAPI's **lifespan** —
it is never created at import time, and never left behind with connections
still open.

| Setting | Value |
|---|---|
| `min_size` | 2 |
| `max_size` | 5 |
| `timeout` | 10 s |
| `max_idle` | 5 min |
| `max_lifetime` | 30 min |

These values follow a **measurement, not an anticipated stack choice**:
opening a connection to Supabase was timed at roughly 800 ms against about
150 ms for the query itself, so most of each request was being spent
connecting. The sizes stay small on purpose — this is a single-worker
demonstration backend sharing a Supabase project with the seeding scripts,
so requests beyond the fifth wait for a free connection rather than opening
a sixth.

`get_connection()` kept its signature through the change, which is why no
router had to be modified.

## From SQLite to PostgreSQL

The first version of this CRM ran on **SQLite**, and that was a deliberate
choice rather than a shortcut.

SQLite is a database in a single file: no server, no credentials, no network.
It made it possible to design the schema, write real SQL, enforce primary and
foreign keys, generate 35,000 rows of synthetic data and build the whole REST
API **without spending any time on infrastructure**. The SQL written then was
real SQL, and most of it transposed unchanged.

The limits appeared when the CRM had to stop being a local prototype. A source
system that a data platform is supposed to read from cannot live in a file on
one laptop: it needs to be reachable over the network, to accept concurrent
readers, and to exist independently of whoever is working on it. A
file-based database could not provide any of that.

That need — not a preference for a technology — is what led to the migration
to **PostgreSQL hosted on Supabase**.

The old SQLite file may still be present locally under `CRM/data/` as a
historical artefact. It is **no longer used by anything**: no runtime code
imports `sqlite3`, references the file, or depends on it in any way. It is
ignored by Git and can be deleted without consequence.

This is the Orialis philosophy in practice: technology choices follow the
problems actually encountered, they are not decided in advance to assemble a
stack.

## Data Model

```
branches  ──<  advisors  ──<  clients  ──<  interactions
                                  └──────────────┘
```

`interactions` references both `clients` and `advisors`.

| Table | Primary key | Foreign keys | Columns |
|---|---|---|---|
| `branches` | `branch_id` | — | 7 |
| `advisors` | `advisor_id` | → `branches` | 12 |
| `clients` | `client_id` | → `advisors` | 16 |
| `interactions` | `interaction_id` | → `clients`, → `advisors` | 9 |

Conventions applied throughout:

- **English only** — table names, column names, business values, comments
- **Status columns prefixed by their table** — `branch_status`, `advisor_status`,
  `client_status` — so a three-way join never carries an ambiguous `status`
- **`TIMESTAMPTZ`** for instants (`created_at`, `updated_at`,
  `interactions.interaction_date`), **`DATE`** for calendar days
  (`advisors.hire_date`, `clients.birth_date`)
- ISO 639-1 language codes (`FR`, `EN`, `IT`) and IANA time zones
  (`Europe/Zurich`)

The database session runs in UTC.

## Project Structure

```
CRM/
├── config.py       # Shared PostgreSQL configuration (.env, DATABASE_URL, scrubbing)
├── app/            # FastAPI application
│   ├── database.py #   connection layer
│   ├── main.py     #   application assembly
│   ├── security.py #   X-API-Key dependency
│   └── routers/    #   one module per resource, plus stats.py for aggregates
├── scripts/        # Schema, seeds, simulator, incremental test
├── data/           # Historical SQLite artefact only - not used, not versioned
└── tests/          # Verification against PostgreSQL
```

The React frontend that consumes this API lives in `frontend/` at the
repository root, outside `CRM/`. It has its own README. It never reaches
PostgreSQL: every figure it displays comes from the endpoints above.

`config.py` sits beside `app/` and `scripts/` on purpose: the configuration
belongs to neither, and both consume it. Putting it inside one would make the
other depend on it.

## Installation

```bash
python -m venv .venv
.venv\Scripts\activate          # Windows
source .venv/bin/activate       # macOS / Linux
pip install -r requirements.txt
```

Tested with Python 3.12.

## Configuration

The CRM reads two environment variables from a `.env` file at the repository
root. Copy the template and fill in your own values:

```bash
cp .env.example .env
```

```
DATABASE_URL=postgresql://<USER>:<PASSWORD>@<HOST>:<PORT>/<DATABASE>?sslmode=require
CRM_API_KEY=<a long random string>
```

| Variable | Used by | Purpose |
|---|---|---|
| `DATABASE_URL` | API and scripts | PostgreSQL connection string |
| `CRM_API_KEY` | API only | Shared secret protecting every `/api/v1` route |

Generate a key with
`python -c "import secrets; print(secrets.token_urlsafe(32))"`. The API
**refuses to start** without one: an API that silently runs with
authentication disabled is worse than one that will not boot.

If `sslmode` is absent from `DATABASE_URL`, `config.py` appends
`sslmode=require` so that no connection falls back to plaintext. An `sslmode`
you set yourself is never overwritten.

`.env.example` documents the exact shape, where to find each value in the
Supabase dashboard, and the two endpoints Supabase exposes. **`.env` is
ignored by Git and must never be committed.**

Two things worth knowing before the first run:

- Special characters in the password must be **percent-encoded**
  (`@` → `%40`, `:` → `%3A`, `/` → `%2F`, `#` → `%23`, `%` → `%25`).
- Supabase displays the password slot as `[YOUR-PASSWORD]`; the **square
  brackets are placeholder markers** and must be removed.

The scripts refuse to start on a connection string that cannot work, and
report the shape of the problem without ever printing the value.

## Database Initialization

The whole dataset can be rebuilt from scratch. Run the scripts in this order —
each table references the previous one:

```bash
python CRM/scripts/init_db.py           # creates the 4 tables
python CRM/scripts/seed_branches.py     # 7 branches
python CRM/scripts/seed_advisors.py     # 100 advisors
python CRM/scripts/seed_clients.py      # 5,000 clients
python CRM/scripts/seed_interactions.py # 30,000 interactions
```

The seed scripts are **idempotent**: they use a fixed random seed and an
`INSERT ... ON CONFLICT DO UPDATE`, so re-running one produces exactly the
same rows instead of duplicating them. Any row left over from an earlier,
different dataset is removed, so each table lands on its intended count.

## Running the API

From the repository root:

```bash
python -m uvicorn CRM.app.main:app --reload
```

| URL | Content |
|---|---|
| http://127.0.0.1:8000/ | health check |
| http://127.0.0.1:8000/docs | interactive Swagger documentation |

## Running the API with Docker

The API — and only the API — is containerised. The seeds and the simulator
stay outside the image: they are tooling, not part of the running service.

The image is based on **`python:3.12-slim`**. The build context is the
**repository root**, not `CRM/`, because the application is launched as
`CRM.app.main:app` and imports `CRM.config`, so the `CRM` package has to sit
at the root of the working directory inside the image.

```bash
docker build -t orialis-crm-api .
```

```bash
docker run --rm -p 8000:8000 -e DATABASE_URL="..." -e CRM_API_KEY="..." orialis-crm-api
```

Two variables are required at runtime: **`DATABASE_URL`** and
**`CRM_API_KEY`**. **No secret is baked into the image** — neither appears in
the `Dockerfile`, in a layer, or in the image's environment; both are injected
at run time only.

The container listens on **8000** by default and reads **`$PORT`** when it is
set, so a deployment platform that assigns a port is handled without changing
the image.

`.dockerignore` keeps `.env`, the historical SQLite database in `CRM/data/`,
`.git`, and the Python and tooling caches out of the build context. Patterns
are written `**/…` on purpose: a Docker pattern without `**/` only matches at
the root of the context, so `__pycache__/` alone would not exclude
`CRM/app/__pycache__/`.

The API runs as a **non-root user** (`orialis`, uid 10001), created after the
dependencies are installed so that the installed packages stay read-only to
the application.

## API Endpoints

The API is **read-only**: no `POST`, `PUT` or `DELETE` endpoint is exposed.
Writes go through the scripts.

Every `/api/v1` route requires the `X-API-Key` header. A missing or wrong key
answers **401**:

```bash
curl -H "X-API-Key: $CRM_API_KEY" http://127.0.0.1:8000/api/v1/branches
```

`/`, `/docs` and `/openapi.json` stay public, so an uptime probe can reach the
health check without holding a credential. The dependency is declared once, on
the parent router, which is why none of the four resource modules had to be
modified.

| Endpoint | Pagination | Filters |
|---|---|---|
| `GET /api/v1/branches` | none (7 rows) | `country`, `branch_status` |
| `GET /api/v1/branches/{branch_id}` | — | — |
| `GET /api/v1/advisors` | `page`, `page_size` (max 200) | `branch_id`, `advisor_status`, `specialization`, `spoken_language`, `updated_since` |
| `GET /api/v1/advisors/{advisor_id}` | — | — |
| `GET /api/v1/clients` | `page`, `page_size` (max 500) | `country`, `segment`, `risk_profile`, `client_status`, `advisor_id`, `updated_since` |
| `GET /api/v1/clients/{client_id}` | — | — |
| `GET /api/v1/interactions` | `page`, `page_size` (max 500) | `client_id`, `advisor_id`, `interaction_type`, `channel`, `date_from`, `date_to`, `created_since` |
| `GET /api/v1/interactions/{interaction_id}` | — | — |

Paginated endpoints answer with:

```json
{
  "data": [ { "...": "..." } ],
  "pagination": {
    "page": 1,
    "page_size": 100,
    "total_records": 5000,
    "total_pages": 50
  }
}
```

`/branches` returns `{"data": [...], "total_records": 7}` — it is a small
reference table, so it is returned whole.

An unknown id answers **404**; an invalid parameter answers **422**, produced
by FastAPI's own validation. `total_records` always reflects the filters in
use, never the size of the table.

### Aggregate endpoints

Two further routes live in `CRM/app/routers/stats.py`, deliberately apart from
the four resource routers. Those expose CRM **records**; these expose **facts
about** them, and return no operational row at all.

| Endpoint | Returns |
|---|---|
| `GET /api/v1/stats/overview` | Headline counts, the three client breakdowns, and the advisors holding the most clients |
| `GET /api/v1/stats/filters` | The distinct values of every filterable dimension |

Both are protected by `X-API-Key` like every other `/api/v1` route, and both
are computed by PostgreSQL with `COUNT` and `GROUP BY` — no table is pulled
into Python to be aggregated in a loop.

**`/stats/overview`** exists because the alternative was untenable. Ranking
advisors by client count from the paginated API meant one request per advisor —
100 round trips for a single panel — or downloading all 5,000 clients (about
2.1 MB) to group them in the browser. The endpoint answers the same question in
**one call of roughly 1 kB**:

```json
{
  "totals": { "total_clients": 5000, "active_clients": 4396,
              "total_advisors": 100, "total_interactions": 30130 },
  "clients_by_segment":      [ { "segment": "Standard", "client_count": 3250 } ],
  "clients_by_country":      [ { "country": "France", "client_count": 2500 } ],
  "clients_by_risk_profile": [ { "risk_profile": "Balanced", "client_count": 2501 } ],
  "top_advisors": [ { "advisor_id": "ADV012", "first_name": "Sophie",
                      "last_name": "Vidal", "client_count": 170 } ]
}
```

`top_advisors_limit` (1–20, default 5) sets how many advisors come back. The
breakdowns carry their own labels, so no consumer needs a private copy of the
CRM's reference values.

Recent interactions are deliberately **not** part of this payload: they are
records, and they are fetched from `/api/v1/interactions` like any other list.
An endpoint that answered with both counts and rows would force every caller
wanting a total to pay for rows it did not ask for.

**`/stats/filters`** returns the distinct value of each dimension the list
endpoints filter on, so a consumer can offer pickers without holding its own
copy of the reference data:

```json
{
  "client_country": ["Belgium", "France", "Italy", "Switzerland"],
  "client_segment": ["Patrimonial", "Private Banking", "Standard"],
  "client_risk_profile": ["Balanced", "Conservative", "Growth"],
  "client_status": ["Active", "Inactive", "Prospect"],
  "advisor_specialization": ["Estate Planning", "..." ],
  "advisor_status": ["Active"],
  "advisor_job_title": ["Branch Manager", "..." ],
  "advisor_spoken_language": ["EN", "FR", "IT"],
  "interaction_type": ["Administrative Update", "..." ],
  "interaction_channel": ["Client Portal", "Email", "In Person", "Phone", "Video Call"]
}
```

Note `advisor_spoken_language`. The column stores a delimited string —
`"FR,EN,IT"` — so it holds seven combinations for three actual languages. The
endpoint splits and flattens it, because the `spoken_language` filter matches
one language and offering `"FR,EN,IT"` as a choice would be offering something
that is not a language.

The whole payload is under a kilobyte. The heaviest scan behind it, `DISTINCT`
over the 30,130 interactions, was measured at **10.6 ms**.

## Daily Activity Simulation

```bash
python CRM/scripts/simulate_daily_activity.py
```

Each execution represents **one business day**: it inserts 35 to 80 new
interactions and updates 5 to 20 existing clients, refreshing their
`updated_at`.

Unlike the seed scripts, this one is **deliberately not idempotent**. Seed
scripts describe a *state* and converge to it; the simulator records *events*
and appends to history. Running it five times simulates five days, not one day
five times. Nothing is deleted, nothing is reset.

Everything a run writes happens inside **one transaction**, and seven
integrity checks execute *before* the commit — so a failed day leaves the
database untouched rather than half-updated.

This is what lets the CRM behave like an evolving operational source rather
than a fixed fixture.

## Incremental Extraction

Two columns let a consumer ask "what changed since?":

| Table | Nature | Column | API parameter |
|---|---|---|---|
| `interactions` | append-only journal | `created_at` | `created_since` |
| `clients` | mutable state | `updated_at` | `updated_since` |

A modified client creates no new row — same id, same place. Its timestamp is
the only evidence anything changed.

Note the distinction on `interactions`: `interaction_date` is **when the
meeting happened**, `created_at` is **when the record was written**. A meeting
held last week can be recorded today, so only `created_since` catches it. A
pipeline that filtered on `date_from` would lose those rows permanently.

### Current semantics: at-least-once

Both parameters use an **inclusive** bound (`>=`). A row sitting exactly on the
watermark is therefore **returned again** on the next extraction.

This is deliberate: re-reading a row is harmless, since a downstream load
deduplicates on the primary key, whereas missing one is irreversible. But it
is a real effect, not a rounding detail — in the historical dataset, 337 rows
share a single `created_at` value.

### Validated, not implemented

`CRM/scripts/test_incremental_behavior.py` captures the watermarks, runs one
simulated day, and compares three extraction strategies against the rows the
run actually produced. On the busiest second of the table it shows the
inclusive bound re-delivering rows, a strict `>` bound losing them, and a
**composite watermark `(timestamp, id)`** returning exactly the right set.

That composite strategy is **validated conceptually for append-only data, and
is not implemented** — neither in the API, which still exposes the
single-timestamp form, nor in any ingestion layer, since none exists yet. It
is a design decision recorded for when the ingestion architecture is built.

## Current Limitations

Deliberate for this version, and the honest starting point for what comes next.

- **A single shared API key**, compared in constant time and rotated by
  restarting the application. It identifies no caller and carries no scope,
  so it cannot express per-consumer permissions or be revoked individually.
  Adequate for one trusted downstream pipeline, not for several.
- **No write endpoints.** The API reads; the scripts write.
- **No deletion tracking**, so incremental extraction cannot detect a removed
  row. The usual remedy is a soft delete.
- **`spoken_languages` is a delimited string** (`"FR,EN,IT"`), which prevents
  index use on that filter. A normalised table would be the answer at scale.
- **No automated test suite.** `CRM/tests/` is still empty; verification lives
  inside the scripts today.

## Status

| Version | State |
|---|---|
| V1 — local SQLite prototype | done |
| V2 — PostgreSQL on Supabase | **done, current** |

Identified next steps, in order of impact: deployment of the container, and
the composite watermark at the ingestion layer.

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
- **psycopg 3** — PostgreSQL driver
- **PostgreSQL hosted on Supabase** — the database

```
HTTP client  ──►  FastAPI  ──►  psycopg 3  ──►  PostgreSQL / Supabase

Seeds / Simulator  ──────────►  psycopg 3  ──►  PostgreSQL / Supabase
```

Both halves — the API and the command-line scripts — read the same connection
string from `CRM/config.py`, which loads it from a local `.env` file.

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
│   └── routers/    #   one module per resource
├── scripts/        # Schema, seeds, simulator, incremental test
├── data/           # Historical SQLite artefact only - not used, not versioned
└── tests/          # Automated tests (not written yet)
```

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

The CRM reads a single environment variable, `DATABASE_URL`, from a `.env`
file at the repository root. Copy the template and fill in your own values:

```bash
cp .env.example .env
```

```
DATABASE_URL=postgresql://<USER>:<PASSWORD>@<HOST>:<PORT>/<DATABASE>?sslmode=require
```

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

## API Endpoints

The API is **read-only**: no `POST`, `PUT` or `DELETE` endpoint is exposed.
Writes go through the scripts.

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

- **No authentication.** No API key, no token, no `X-API-Key` header: every
  endpoint, plus `/` and `/docs`, answers to any caller. This must be
  addressed before the API is exposed publicly.
- **No connection pool.** `get_connection()` opens one psycopg connection per
  request and closes it afterwards. Establishing a connection to Supabase
  costs roughly 800 ms against about 150 ms for the query itself, so most of
  each request is spent connecting. Pooling is an identified optimisation, not
  something already in place.
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

Identified next steps, in order of impact: a connection pool, API
authentication, and the composite watermark at the ingestion layer.

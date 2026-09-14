# Orialis ERP

The **second source system** of the Orialis Data Platform, after the CRM.

Orialis is a fictional international wealth-management firm operating in
France, Belgium, Switzerland and Italy. The CRM holds the commercial
relationship — branches, advisors, clients, interactions. The ERP holds what
the firm **spends and owes**: suppliers, their invoices, the payments that
clear them, employee expense claims, advisor commissions, and the cost centres
everything is charged to.

**Status: schema only.** The nine tables exist; no data, no simulator, no API.

---

## The two rules the schema is built on

Everything below follows from two decisions, and reading them first makes the
rest obvious.

### 1. The constraint doctrine

> What can be verified on **one row** is declared to PostgreSQL.
> What requires a **sum across rows** is checked by the application before
> `COMMIT`.

PostgreSQL cannot express *"the sum of the allocations does not exceed the
invoice total"* in a `CHECK` — that is a multi-row invariant. It **can**
express *"the allocation currency equals the invoice currency"*, because a
composite foreign key turns it into a per-row fact.

So everything decidable per row is declared here — **108 CHECK constraints,
15 foreign keys, 8 unique keys** — and only the genuinely aggregate invariants
are left to the future application layer. They are listed at the bottom of this
file, and there are only four.

### 2. The ERP never deletes

> Cancellation is a **state**, never a missing row.

Every foreign key is `ON DELETE RESTRICT`. There is **not one `CASCADE`** in
this schema. An allocation that was wrong becomes
`allocation_status = 'Cancelled'`; a supplier in dispute becomes
`supplier_status = 'Blocked'`.

This is what makes incremental extraction trustworthy later. The CRM documents
the opposite as a known limitation — *"no deletion tracking, so incremental
extraction cannot detect a removed row"* — and the ERP does not repeat it,
because a cancellation is a visible `UPDATE` rather than a silent
disappearance.

---

## Independence from the CRM

The ERP exists to create a **realistic reconciliation problem**, so its
isolation from the CRM is a feature, not an accident:

| | CRM | ERP |
|---|---|---|
| Database | `DATABASE_URL` | `ERP_DATABASE_URL` — **a different database** |
| Person | `ADV012` | `EMP-0047` |
| Place | `BR001` | `FR-PAR-01` |
| Config | `CRM/config.py` | `ERP/config.py` — a deliberate copy, not an import |

Three rules hold throughout, and are verified:

- **No column names a CRM concept.** No `advisor_id`, no `branch_id`, no
  `crm_*`. Reading this schema alone, you cannot tell a CRM exists.
- **No foreign key crosses a database.** Technically possible through a
  foreign-data wrapper, which is precisely the coupling being avoided.
- **No import.** `ERP/config.py` duplicates its CRM counterpart rather than
  importing it. The duplication is the price of independence — two vendor
  systems never share a configuration module.

`commissions.source_system` holds the plain text `'CRM'`. It is a label
recording where a figure came from, not a reference to anything.

There is **no mapping table**, on purpose. Matching `EMP-0047` to `ADV012` is a
platform problem, and it will be a real one: the two registers overlap only
partially, roughly 5% of employees will have no email, and office codes are not
derivable from branch ids.

---

## The nine tables

```
                           cost_centers ◄─┐ (self-referencing hierarchy)
                                 ▲        │
              ┌──────────────────┼────────┴────────┬──────────────┐
              │                  │                 │              │
          employees              │                 │              │
              ▲                  │                 │              │
      ┌───────┴────────┐         │                 │              │
      │                │         │                 │              │
  expenses        commissions    │        supplier_invoices ──► suppliers
                                 │                 ▲              ▲
                                 └─────────────────┤              │
                                      payment_allocations ◄─┐     │
                                                   │        │(replacement)
                                                   ▼        │     │
                                              payments ─────┴─────┘

                                  fx_rates   (standalone, no foreign key)
```

| Table | Columns | Role |
|---|---|---|
| `cost_centers` | 13 | The analytical axis. Three levels: Group → Country → Branch/Function |
| `suppliers` | 14 | Who Orialis pays, and on what terms |
| `employees` | 14 | The ERP staff register — advisors **and** support functions |
| `supplier_invoices` | 24 | The debt. Two independent status columns |
| `payments` | 13 | Cash going out. One transfer, one supplier |
| `payment_allocations` | 12 | The matching. Makes partial and batch payments possible |
| `expenses` | 22 | Employee claims, from submission to reimbursement |
| `commissions` | 23 | The administrative **result** of a commission run |
| `fx_rates` | 5 | Monthly CHF→EUR reference rates |

---

## Three decisions worth understanding

### `supplier_invoices` carries two status columns

```
invoice_approval_status    a human decision    Draft → Approved / Rejected
invoice_payment_status     a treasury fact     Unpaid → Partially Paid → Paid
```

They advance **independently**. "Approved but unpaid" is the normal state of
every invoice that is not yet due — a single status column could not express it
without duplicating every value of the other cycle, and `Partially Paid` would
become inexpressible.

One constraint links them, and it is the most elementary internal control of an
accounts-payable cycle:

> `invoice_payment_status <> 'Unpaid'` ⟹ `invoice_approval_status = 'Approved'`
>
> **Nothing is paid that has not been approved.**

### `payment_allocations` has a surrogate key and a partial unique index

The natural key would be `(payment_id, invoice_id)`. It cannot be the primary
key, because deletion is forbidden: correcting a mis-matched allocation means
cancelling the old row and inserting a **new** one for the same pair, and a
composite primary key would refuse it.

So the uniqueness rule moved into a **partial index**:

> At most one **Active** allocation per `(payment_id, invoice_id)`.
> Cancelled rows are outside the index entirely, so any number may pile up.

Two independent mechanisms stop an allocation from counting, and they are
**not** interchangeable:

| | `allocation_status = 'Cancelled'` | `payment_status <> 'Executed'` |
|---|---|---|
| What happened | The **matching** was wrong | The **payment** did not go through |
| Example | €10 000 paid, split 6/4 instead of 5/5 | Transfer rejected, wrong IBAN |
| The payment | stays `Executed` | becomes `Failed` |
| Action on allocations | cancel and replace | **leave them alone** |

The second case needs no write at all: filtering on the payment status makes
the invoices fall back to `Unpaid` by itself. Cancelling the allocations as well
would make a rejected-then-reissued payment impossible to represent.

### The masked IBAN is a constraint, not a convention

```
iban_masked ~ '^[A-Z]{2}[0-9]{2}[*]{4,}[0-9]{4}$'
```

Synthetic IBANs are masked **at source**: the full value never exists in the
database, so no export, dump or API can reveal one. Leaving that to the
generator would make it a habit; the pattern makes it something the database
refuses to accept.

---

## Layout

```
ERP/
├── README.md            # this file
├── config.py            # ERP_DATABASE_URL loading, validation, scrubbing
├── seed/                # the generator - pure Python, touches no database
│   ├── toolkit.py       #   determinism, calendar, seasonality, distributions
│   ├── people.py        #   the 93 humans shared with the CRM, and name pools
│   ├── reference.py     #   cost centres (25), FX rates (36)
│   ├── organisation.py  #   suppliers (~180), employees (185)
│   ├── purchases.py     #   invoices, payments, allocations
│   ├── activity.py      #   expense claims, commissions
│   └── integrity.py     #   the multi-row invariants, checked before COMMIT
├── simulator/           # one day at a time, after the seed ends
│   ├── clock.py         #   the business date, and the no-going-back guard
│   └── day.py           #   a day's work: new activity, transitions, settlement
├── app/                 # the read-only FastAPI application
│   ├── database.py      #   the PostgreSQL pool, over ERP_DATABASE_URL
│   ├── security.py      #   the X-API-Key dependency, over ERP_API_KEY
│   ├── querying.py      #   filters, ordering, and the incremental contract
│   ├── main.py          #   the application and the public health endpoint
│   └── routers/         #   one module per resource, plus stats.py
├── scripts/
│   ├── init_db.py       # the 9 tables, their constraints and their indexes
│   ├── seed_all.py      # generate + load, one transaction
│   ├── report_seed.py   # measure the loaded data against the specification
│   └── simulate_day.py  # run one or more business days
└── tests/
    ├── test_schema.py   # 112 checks; proves the constraints actually bite
    └── test_api.py      # 103 checks; runs the real app against the real data
```

The generator returns plain dictionaries and never opens a connection. That is
what lets the whole history be built, checked, and discarded if one invariant
fails.

`config.py` sits beside `scripts/` rather than inside it, for the same reason
it does in the CRM: a future API and the tooling are two consumers of one
configuration, and putting it in either would make the other depend on it.

---

## Creating the schema

The ERP needs its **own** PostgreSQL database.

```bash
# 1. Point ERP_DATABASE_URL at it, in the repository-root .env
#    (see .env.example - it must NOT be the CRM database)

# 2. Create the tables
python ERP/scripts/init_db.py
```

The script is **idempotent**: `CREATE TABLE IF NOT EXISTS` and
`CREATE INDEX IF NOT EXISTS` mean it can be re-run as often as needed without
recreating or erasing anything. It creates **no data**.

DDL is transactional in PostgreSQL, so the nine tables and their 52 indexes
appear together or not at all.

For local work, a throwaway container is enough — `config.py` leaves loopback
connections unencrypted on purpose, since TLS to `127.0.0.1` protects nothing:

```bash
docker run -d --name orialis-erp-pg -e POSTGRES_PASSWORD=... \
    -e POSTGRES_DB=orialis_erp -p 5433:5432 postgres:16
```

---

## Seeding the history

```bash
python ERP/scripts/seed_all.py            # generate, check, commit
python ERP/scripts/seed_all.py --dry-run  # generate and check, write nothing
python ERP/scripts/report_seed.py         # measure the result against the spec
```

36 months — October 2023 to September 2026 — and about **41 450 rows**.

### The volumes are consequences, not targets

There is no `while len(rows) < 8000` anywhere. The inputs are business rates:
a tier-A supplier invoices about 9.7 times a month, an advisor files about 2.7
expense claims a month, a commission run happens on the last business day. The
row counts are whatever those rates produce across 36 months once seasonality,
hiring, supplier onboarding and deactivation have been applied.

The one thing imposed exactly is a **stated distribution**. When the
specification says "Approved 90%", that percentage is the rule itself, so
`split_exactly()` applies it by largest remainder rather than by sampling 8 000
independent draws and landing near it.

### Chronology is a hard wall

Every date is checked against the lives of the things it refers to. A supplier
does not invoice before it is onboarded or after it is deactivated; an employee
files no claim before being hired or after leaving; a payment never precedes
the approval of the invoice it settles; a commission covers only months its
advisor worked in full.

Three of these were caught as bugs by the pre-commit checks rather than
designed in from the start — an approver who had already left, a credit note
netted off a run that predated its own approval, timestamps drawn twice.

### `paid_amount` is summed, never asserted

```
paid_amount = SUM(allocated_amount)
              WHERE allocation_status = 'Active'
                AND payments.payment_status = 'Executed'
```

`settle()` is the only place it is written, and it consults nothing the
generator intended. A failed transfer leaves its invoice `Unpaid` with no
special case, because the sum simply does not see it.

### Idempotency

Every row is written with `INSERT ... ON CONFLICT (pk) DO UPDATE`, and the
generator is deterministic. Re-running converges to the identical state:
verified by md5 over every table before and after a replay, and again against
a completely fresh database — **identical on all nine tables**.

No `TRUNCATE`, no `DELETE`. A seed that began by emptying the tables would make
"the ERP never deletes" a fiction on its first line.

The honest limit: idempotency holds for the same **code** and the same
**seed**. Change a distribution and re-run, and rows from the previous shape
that no longer have a counterpart would remain — a deliberate consequence of
refusing to delete. Iterate with `--dry-run` and a fresh database.

### All or nothing

The whole load and its 17 multi-row integrity checks run inside a single
transaction. One failure rolls everything back: a half-loaded history is worse
than an empty database, because it looks usable.

---

## Simulating days

```bash
python ERP/scripts/simulate_day.py             # the next day
python ERP/scripts/simulate_day.py --days 30   # thirty consecutive days
python ERP/scripts/simulate_day.py --status    # where the clock stands
python ERP/scripts/simulate_day.py --dry-run   # simulate, then roll back
```

Continues the history where the seed stops, one day at a time. `ERP/simulator/`
imports its rates, seasonality and row builders from `ERP/seed/` rather than
restating them: a simulator with its own copy of the rules drifts from the
history it is supposed to continue, and the drift is invisible until someone
plots a metric across the boundary.

### A day depends on which day it is

| Day | What happens |
|---|---|
| Weekend | Nothing. A real no-op — no invoices, no claims, no bank settling |
| Public holiday | Suppressed **per country**: an Italian office works on 14 July, a French one does not |
| Any business day | New invoices and expense claims; every open item advances one step |
| **Tuesday / Friday** | Payment run — what is due, grouped by supplier |
| **Last business day of the month** | Commission run; the annual bonus rides the January run |
| First business day of a month | The month's FX rate, continuing the walk |

### Not idempotent, but atomic

The seed converges to a state; this does the opposite. Two runs are two days.
Re-running yesterday would file expense claims on a date the ledger has closed,
so the clock in `simulation_state` **refuses to go backwards**.

Each day is still one transaction, checked against the same 18 invariants the
seed uses. One failure rolls the whole day back, clock included.

### The clock needs a table, and it is not a tenth business table

Deriving the date from `MAX(invoice_date)` does not work: a weekend produces no
rows, so the maximum would not move and Saturday would repeat for ever. The
clock has to be stored precisely because some days legitimately leave no trace.

`simulation_state` is therefore **operational metadata, not ERP data**. It is
created by the simulator on first run — `init_db.py` is untouched, the nine
business tables are exactly as validated — it holds one row, has no foreign key
in either direction, and a data platform would never extract it.

### What a day actually does

Twelve steps, and the order carries meaning. Settlement is recomputed **before**
the payment run reads the ledger, not only at the end: an early version
recomputed once, at midnight, and a Friday run paid an invoice a second time
because its `paid_amount` still showed the balance from before that morning's
transfers cleared.

A failed transfer is **not** explicitly retried. The invoice stays approved and
unpaid, so the next payment run finds it again on its own — the retry is
emergent rather than scripted.

Payments created by a run start as `Initiated` and settle one to three days
later. That is what keeps a realistic population of in-flight transfers at any
moment, and what makes `paid_amount` lag the run as a real treasury does.

### Proving the rollback

```bash
python ERP/scripts/simulate_day.py --inject-fault
```

Alters one allocation amount and leaves the invoice untouched — a fault the
schema cannot see, because every row stays valid on its own and only the
multi-row sum disagrees. Three applicative checks fire and the day is rolled
back. A guarantee nobody has watched fire is not a guarantee.

---

## The API

```bash
uvicorn ERP.app.main:app --reload      # http://127.0.0.1:8000/docs
python ERP/tests/test_api.py           # 103 checks
```

Read-only, over `ERP_DATABASE_URL`, protected by `ERP_API_KEY` — a **different
credential from the CRM's**, because two source systems are two products.

| Route | |
|---|---|
| `GET /` | Health: service name, status, pool sizes. **Public** |
| `/docs`, `/openapi.json` | **Public** |
| `GET /api/v1/cost-centers` `/{id}` | 25 rows, the analytical axis |
| `GET /api/v1/suppliers` `/{id}` | Who Orialis pays |
| `GET /api/v1/invoices` `/{id}` | Supplier invoices |
| `GET /api/v1/invoices/{id}/allocations` | How this invoice was paid |
| `GET /api/v1/payments` `/{id}` | Cash going out |
| `GET /api/v1/payments/{id}/allocations` | What this transfer settled |
| `GET /api/v1/allocations` `/{id}` | The matching, flat and incremental |
| `GET /api/v1/stats/overview` | Aggregates only, computed in SQL |
| `GET /api/v1/stats/filters` | Distinct values, for building pickers |

Everything under `/api/v1` requires `X-API-Key`.

### Why allocations are exposed

Because **`payments` and `invoices` cannot be joined without them**. There is no
`invoice_id` on a payment and no `payment_id` on an invoice — the relationship
is genuinely many-to-many, and the allocation is the only place it exists. A
consumer given only the two parent tables could see 8 000 invoices and 8 000
payments and have no way to say which settled which.

It also makes `invoices.paid_amount` auditable: that figure is a derived
aggregate, and exposing a total while withholding the rows it comes from asks
the consumer to trust a number it cannot check.

### Incremental extraction — at-least-once

Every list endpoint accepts `updated_since`, with an **inclusive** bound
(`updated_at >= value`). A row sitting exactly on the watermark comes back
again. That is deliberate: re-reading a row is harmless when the load
deduplicates on the primary key, whereas missing one is irreversible.

**The ingestion layer owns idempotence and deduplication.** This API does not
solve it and does not pretend to.

When `updated_since` is set, the order becomes `(updated_at, <primary key>)`,
and that is not decoration:

| Ordered by | A row you have already passed gets updated |
|---|---|
| primary key | It stays in place. You never see it again — **the change is lost** |
| `updated_at` | It moves to the end. You see it again — at-least-once holds |

The tie-break makes the order *total*. Payments are issued in campaigns, so
hundreds share a timestamp; without it a row could appear on two pages, or on
none. Each response states the ordering it used and the semantics it offers.

### Not exposed in V1

`expenses` and `commissions` will be published as **file extracts** instead, on
purpose: a data platform whose sources are all REST APIs is not a realistic
one. `employees` has no consumer yet, `fx_rates` is already frozen onto every
document as `fx_rate_to_eur`, and `simulation_state` is simulator metadata
rather than ERP data.

One consequence worth stating: `approved_by_employee_ref` and
`validated_by_employee_ref` cannot be resolved to a person through this API.
That is a V1 scope choice, not an oversight.

---

## Verifying the schema

```bash
python ERP/tests/test_schema.py
```

Exit code 0 when everything passes, 1 otherwise — so it drops into CI as is.
It needs `ERP_DATABASE_URL` and a schema already created by `init_db.py`.
**112 checks**, in four families:

| Family | What it proves |
|---|---|
| **Structure** | The nine tables have the expected shape, and the load-bearing constraints exist **by name** — not merely "some constraints exist" |
| **Doctrine** | 15 foreign keys, all `ON DELETE RESTRICT`, zero `CASCADE`; the partial index exists, is unique **and** is partial |
| **Business cycles** | Invalid invoice, payment, expense, commission, supplier and cost-centre states are refused — and refused **by the right constraint** |
| **Deliberate limits** | PostgreSQL **accepts** the multi-row invariants that belong to the application layer |

A schema is not validated by reading it. `CREATE TABLE` succeeding proves the
DDL parses, nothing more — a CHECK with an inverted condition builds perfectly
and guarantees nothing. So the suite is mostly a list of deliberately invalid
rows, and each one **names the constraint that should reject it**. A row
refused by the wrong rule fails the run: a scenario rejected for the wrong
reason tests nothing.

### The backwards-looking tests

Six scenarios assert that the database **accepts** something business-invalid —
an over-allocated payment, a `paid_amount` that disagrees with its allocations,
a commission for an ineligible employee. They are the most important tests in
the file.

Without them, a future reader would eventually "harden" one of those gaps with
a trigger and would be undoing a validated decision rather than fixing a bug.
With them, the attempt turns the suite red and the message explains why. They
also double as the specification of what the seed and the simulator must check
themselves.

### Safe to run against a populated database

There is no `COMMIT` anywhere in the file. The whole run happens inside one
transaction that is always rolled back, each scenario inside its own
`SAVEPOINT`, and every test row carries a `TEST-` prefix (`ZZ-` for values
landing in unique columns) so it cannot collide with real data.

The isolation claim is verified rather than asserted: row counts are captured
before the run and re-read afterwards **on a new connection**, which only sees
committed data.

Both properties were checked on this schema:

- run against a populated database → 112/112, every real row untouched, zero
  `TEST-` residue
- three critical constraints dropped on purpose → 9 failures, exit code 1; each
  break caught by both a structural check and a behavioural scenario

---

## Conventions

Inherited from the CRM, so that two sources read the same way:

- **English only** — table names, column names, business values, comments
- **Status columns prefixed by their table** — `supplier_status`,
  `expense_status`, `allocation_status` — so a join never carries an ambiguous
  `status`
- **`TIMESTAMPTZ`** for instants (`created_at`, `approved_at`, `executed_at`),
  **`DATE`** for calendar days (`invoice_date`, `due_date`, `period_start`)
- **`NUMERIC(14,2)` for money, never `FLOAT`** — binary floating point cannot
  represent `0.10` exactly, so `gross = net + tax` would fail at random
- ISO country codes, ISO-4217 currency codes

Two conventions are **stricter** than the CRM's, and deliberately so:

- **`NOT NULL` is the default**, not the exception. The CRM leaves most columns
  nullable; the ERP requires a value wherever the business always has one.
- **`created_at` and `updated_at` carry no `DEFAULT`.** The validated decision
  is that `updated_at` is maintained by the application, not by a trigger. A
  `DEFAULT now()` would quietly stamp today onto a row a seed meant to backdate
  to 2023, and the mistake would be invisible. Without a default, `NOT NULL`
  turns "I forgot" into an immediate error.

---

## What PostgreSQL cannot enforce

The specification planned fourteen pre-commit checks. **Eight of them turned
out to be per-row facts** and were lifted into the database — including two the
specification had assumed would need application code:

- `paid_amount` stays within `[0, gross_amount]`, in the direction of the sign
- `invoice_payment_status` agrees with `paid_amount` versus `gross_amount`

Eight invariants remain genuinely multi-row and belong to the application
layer, inside the transaction, before `COMMIT` — the same pattern the CRM
simulator already uses with its seven pre-commit checks.

| # | Invariant | Why it cannot be declared |
|---|---|---|
| 1 | `paid_amount` = Σ active allocations of executed payments | A sum over another table |
| 2 | Σ allocations of a payment ≤ `payment_amount` | A sum over another table |
| 3 | An allocation's sign matches its invoice's sign | The sign lives on another row |
| 4 | No active allocation toward a `Rejected` / `Cancelled` invoice | A status on another row |
| 5 | No cycle in the replacement chain | Recursive; only self-reference is blocked |
| 6 | No new invoice or payment for an `Inactive` / `Blocked` supplier | A status on another row, at a point in time |
| 7 | `employee_ref` belongs to a commission-eligible employee | A flag on another row — see below |
| 8 | No row count ever decreases | Global, across the whole transaction |

Number 7 is deliberately **not** enforced by a composite foreign key, although
it could be. Eligibility changes over time: an advisor who stops being eligible
in 2026 does not invalidate the commissions they legitimately earned in 2024.
Freezing the flag onto 5 900 historical rows would make the past depend on the
present, which is exactly what the frozen `fx_rate_to_eur` columns exist to
avoid elsewhere.

### The rule the application must implement

```
paid_amount(invoice) = SUM(allocated_amount)
                       WHERE allocation_status = 'Active'
                         AND payments.payment_status = 'Executed'
```

Both conditions are required. Recomputation is triggered by **three** events,
and the third is the one that gets forgotten:

1. an allocation is created
2. an allocation is cancelled
3. **a payment changes status** — `Initiated → Executed` credits every invoice
   it covers; `Executed → Failed` debits them again

One ordering rule falls out of the partial unique index, and it is worth
knowing before writing the generator:

> To correct an allocation, **cancel the old row first, then insert the
> replacement.** Inserting while the old row is still `Active` is rejected.

---

## Not in this version

No data, no seeds, no activity simulator, no REST API, no CSV or Excel exports,
no frontend, and no remote database configured. The schema is the whole of this
step.

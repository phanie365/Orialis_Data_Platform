# Orialis ERP — Frontend

A deliberately minimal, internal **Finance / Operations** screen set over the
Orialis ERP API: Dashboard, Invoices, Payments, Suppliers. Read-only. Compact
tables, system font, no design system, no charts, no UI kit, no router
library — React 18 and Vite 6, nothing else.

It is not a second CRM showcase, and it is independent of `frontend/` (the
CRM frontend): no shared code, no shared variables, no shared ports.

## Running it locally

```bash
# 1. The ERP API, on port 8001 (the CRM API keeps 8000)
uvicorn ERP.app.main:app --port 8001

# 2. The frontend, on port 5174 (the CRM frontend keeps 5173)
cd ERP/frontend
cp .env.example .env        # set ERP_API_KEY - the same value the API reads
npm install
npm run dev                 # http://127.0.0.1:5174
```

| Command | |
|---|---|
| `npm run dev` | Dev server on **5174**, with the API proxy |
| `npm run build` | Production bundle in `dist/` |
| `npm run preview` | Serves `dist/` on **4174**, with the same proxy |
| `npm test` | `node --test` — no extra test dependency |

## The API key never reaches the browser

The React code calls only the relative path `/api/v1/...`. The Vite server
(Node) forwards it to `ERP_API_URL` and attaches `ERP_API_KEY` as
`X-API-Key` on the way out — see `apiProxy.js`. A key sent by the browser is
dropped. Nothing under `src/` reads an environment variable, and the
configuration refuses to start if a `VITE_*` variable looks like a secret,
since Vite would inline it into the bundle. `tests/security.test.js` checks
all of this against the real dev server and a real build.

There is no production server here: serving `dist/` in production would need a
proxy that holds the key, which belongs to a later deployment step.

## Screens

| Screen | Endpoints | Notes |
|---|---|---|
| Dashboard | `/stats/overview`, `/invoices?…&page_size=1` | Whole history. Overdue count needs a **business date you type**: the ERP runs on simulated time, so the browser date is never used. Payments by status are **counts only** — EUR and CHF are never summed. |
| Invoices | `/invoices`, `/invoices/{id}`, `/invoices/{id}/allocations` | Server-side filters and pagination, "overdue as of" shortcut, detail panel with the allocations and links to payments and supplier. |
| Payments | `/payments`, `/payments/{id}`, `/payments/{id}/allocations` | Server-side filters and pagination, Failed / Initiated shortcuts, detail panel with the invoices settled. |
| Suppliers | `/suppliers?page_size=200`, `/suppliers/{id}`, counts on `/invoices` and `/payments` | The ~180 suppliers are loaded once; search and filters run **in the browser**. Detail panel with activity counts and links to the filtered lists. |

Reference data (`/stats/filters`, all suppliers, all 25 cost centres) is
loaded once at start-up, to show names instead of ids and to fill the pickers.
Screen, filters, page and open detail live in the URL hash
(`#/invoices?supplier_id=SUP-00002&selected=INV-…`), so every view can be
linked, reloaded or bookmarked.

Expenses and commissions have no screen: they are published as the daily CSV
and monthly Excel extracts, not through the API.

## Accepted limits of the current API (V1)

- Lists are sorted by id, ascending: page 1 shows the **oldest** records. No
  sort parameter exists, and no workaround is applied.
- No text search on invoices or payments; one value per filter (an "Unpaid or
  Partially Paid" view is not possible, so overdue means Approved + Unpaid).
- No per-supplier amounts or balance: the supplier panel shows counts.
- No period filter on the dashboard, and no EUR amount on payments.

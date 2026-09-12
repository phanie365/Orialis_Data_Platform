# Orialis CRM — Frontend

The internal web interface Orialis advisors would open to consult the client
book: an overview of the network, and browsable lists of clients, advisors,
interactions and branches.

It is a **read-only consumer of the CRM API**. It holds no business logic of
its own, computes no figure the API does not return, and **never reaches
PostgreSQL** — it has no database driver, no connection string, and no way to
acquire one. Every number on screen comes from an HTTP response.

---

## Architecture

**React 18 + Vite 6.** That is the whole dependency list: `react`,
`react-dom`, and Vite with its React plugin for the build.

There is no UI kit, no CSS framework, no chart library, no state manager and
no routing library. Each was considered and left out for the same reason: the
work they would do here is small enough to read in one file, and a dependency
that hides twenty lines behind a hundred kilobytes is a poor trade. The
distribution bars are `<div>` elements with a percentage width; the design
system is CSS custom properties; the router is ~140 lines of the History API.

### How it talks to the API

```
Browser ──GET /api/v1/clients──▶ Vite dev server (Node)
   (no key, same origin)              │ adds X-API-Key
                                      ▼
                             FastAPI :8000 ──▶ PostgreSQL
```

The React code only ever requests the **relative path** `/api/v1/…`, so every
request goes back to the origin that served the page. The dev server proxies
it onward and attaches the credential server-side.

This solves two problems at once:

- **The API sends no CORS headers.** `CRM/app/main.py` declares no
  `CORSMiddleware`, so a browser on `:5173` calling `:8000` directly would
  have its response blocked. Proxying means the browser never makes a
  cross-origin request, and no backend change was needed.
- **The API key never reaches the browser.** See below — this is the part
  that matters most.

### Data loading

Filtering and pagination are done **by the API**, never in the browser. A list
page holds one page of rows — 25 by default — and `total_records` comes from a
server-side `COUNT` over the same filters. The 5,000 clients and the 30,130
interactions stay in PostgreSQL.

| Page | Requests | Payload |
|---|---|---|
| Overview | 2 | 3.4 kB |
| Clients | 3 | 45.4 kB |
| Advisors | 3 | 10.3 kB |
| Interactions | 3 | 41.3 kB |
| Branches | 2 | 34.6 kB |
| Client detail | 3 | 3.6 kB |

The heaviest page load is **45 kB**, against 2.2 MB if the client table were
downloaded.

The one deliberate exception is `GET /advisors?page_size=200`, which returns
all **100** advisors in a single ~33 kB request. It is what lets a list show
"Sophie Vidal" instead of "ADV012" and turns the advisor filter into a picker.
It is bounded to the one table small enough to justify it — the same approach
applied to clients would be indefensible, which is why `client_id` on the
interaction list is a text field rather than a dropdown.

---

## Structure

```
frontend/
├── index.html           # Entry document; loads Cormorant Garamond + Inter
├── vite.config.js       # Dev server, and the API-key proxy (see below)
├── .env.example         # Template — no real values
└── src/
    ├── main.jsx         # Mounts the app inside the RouterProvider
    ├── App.jsx          # Route table
    ├── router.jsx       # Minimal History-API router (~140 lines)
    ├── format.js        # Shared date, count and status formatting
    ├── api/
    │   ├── client.js    #   The only module that touches the network
    │   ├── useApi.js    #   Fetch-on-mount hook, with abort on unmount
    │   └── useListPage.js #  Filter + pagination state for a list page
    ├── components/      # Shell, DataTable, FilterBar, Pagination, Wordmark,
    │                    # KpiTile, BreakdownBars, TopAdvisors, RecentInteractions
    ├── pages/           # One file per route
    └── styles/
        ├── tokens.css   #   The Orialis design system
        └── global.css   #   Reset and shared primitives
```

`src/api/client.js` is the **only** module that performs a request. Nothing
else in `src/` knows a URL, a header, or a credential — which is what makes
the guarantee below structural rather than a matter of discipline.

---

## Running locally

The API must be running first: the frontend displays nothing on its own.

```bash
# Terminal 1 — from the repository root
python -m uvicorn CRM.app.main:app --reload

# Terminal 2
cd frontend
cp .env.example .env        # then fill in CRM_API_KEY
npm install
npm run dev                 # http://localhost:5173
```

| Command | Effect |
|---|---|
| `npm run dev` | Dev server on `:5173`, with hot reload and the API proxy |
| `npm run build` | Production bundle into `dist/` |
| `npm run preview` | Serves the built bundle on `:4173`, proxy included |

Current build output: **179 kB JS (55 kB gzipped)** and **23 kB CSS
(4.5 kB gzipped)**.

A note on request counts: in development you will see **four** API calls per
page load where production makes **two**. React's StrictMode mounts every
component twice to expose effects that fail to clean up. The aborted duplicate
still reaches the server. `npm run preview` shows the real figure.

---

## Environment variables

```
CRM_API_URL=http://127.0.0.1:8000
CRM_API_KEY=<the same key the API reads>
```

Both are read by **`vite.config.js`**, which runs in **Node**, on your
machine, at config time. Neither is bundled and neither is served. The dev
server uses them to reach the API and to attach the `X-API-Key` header to each
proxied request.

### ⚠️ Never prefix these with `VITE_`

This is the single most important rule in this directory.

Vite exposes a variable to client code **only** when its name starts with
`VITE_`. Such a variable is substituted into the JavaScript bundle **at build
time**: `import.meta.env.VITE_X` literally becomes its value inside a `.js`
file served to every visitor. Anyone can read it with "View source".

A `VITE_CRM_API_KEY` would therefore be a **published key**. Keeping `.env`
out of Git changes nothing about it — the secret would not be in the file, it
would be in the build output.

Both variables are deliberately **unprefixed**, which means Vite refuses to
expose them to client code at all. The protection is enforced by the tool, not
by remembering to be careful.

Verified on the current build — 203,102 characters of `dist/` scanned for the
key, its first and last fragments, the database URL, the PostgreSQL password
and user, the Supabase host, `import.meta.env`, the string `X-API-Key`, and
the backend host and port:

```
absent  CRM_API_KEY (full value and fragments)
absent  DATABASE_URL, PostgreSQL password, PostgreSQL user
absent  Supabase host, 'supabase', 'postgresql', 'sslmode'
absent  'import.meta.env', 'X-API-Key', ':8000', '127.0.0.1'
```

`DATABASE_URL` must **never** appear in this directory, in any form. The
frontend has no database access and never will.

---

## Pages

| Route | What it shows |
|---|---|
| `/` | Overview — four KPIs, client breakdowns by segment, country and risk, top advisors, recent interactions |
| `/clients` | Client list, paginated, filtered on country, segment, risk profile, status and advisor |
| `/clients/:clientId` | One client: identity, contact, residence, classification, assigned advisor, recent history |
| `/advisors` | Advisor list, paginated, filtered on branch, specialization, language and status |
| `/interactions` | Interaction log, paginated, filtered on type, channel, advisor, client id and date range |
| `/branches` | The seven offices, as cards — no pagination for seven records |

Filters are seeded from the URL, so `/clients?advisor_id=ADV012` is a working
link: an advisor row points there to answer "who are this advisor's clients",
and a branch card points to `/advisors?branch_id=BR003`.

There is **no advisor detail page**. The list already shows every field
`/advisors/{id}` returns, so the page would be the same columns on their own
route. The one thing that would justify it — the advisor's client book — is
reachable from the list, as a real query.

Filter options come from `GET /api/v1/stats/filters`, so no business value
(segment, country, risk profile, specialization, language, interaction type,
channel) is written into this codebase. The CRM's reference data stays in the
database.

### Not in this version

No write operations — the API is read-only. No authentication of the person
using the interface: the API key authenticates the *application*, and anyone
who can reach the dev server can read the CRM. No search box, no column
sorting, no export. No portfolio value, assets under management or performance
figures anywhere: **the CRM stores none of them**, and a plausible-looking
number in a wealth-management interface is worse than an absent one.

---

## Design

The interface follows the Orialis moodboard: deep forest green, warm ivory
ground, sage and stone neutrals, a champagne accent. **Cormorant Garamond**
carries the wordmark, the headline figures and the editorial line; **Inter**
handles navigation, tables and operational labels. Both are freely licensed —
no proprietary typeface is reproduced.

Structure is expressed with **1px hairlines**, not shadows. Corner radius is
2px. There are no gradients, no glassmorphism and no filled badges. Charts use
one colour family at four intensities rather than arbitrary categorical
colours, and status indicators are muted enough to sit beside body text
without shouting.

Every colour, space and typographic value is a custom property in
`src/styles/tokens.css`. A hex code written directly in a component is how a
design system stops being one.

Two font-rendering details worth knowing, both found by looking at the
rendered page rather than the code:

- Cormorant Garamond ships **old-style figures** by default, where `1` is
  drawn as a small serifed I. `100` read as `IOO`. Every numeric element
  therefore sets `font-variant-numeric: lining-nums tabular-nums`.
- Its `0` is a very narrow oval that, set alone at 19px, reads as a pair of
  parentheses. The "no results" counter says **"No matching records"** in
  words rather than showing a lone zero.

---

## Deployment

**Not implemented, and not attempted.**

The proxy described above is Vite's **development** server. `npm run preview`
shares the same configuration so a production build can be exercised locally,
but `vite preview` is not a production server either.

A deployed frontend will need a server-side layer holding the API key — a
serverless function, a reverse proxy, or serving the built assets from FastAPI
itself. **That decision has not been made.** Nothing in this repository
deploys the frontend today.

Two things are already settled, and they are what makes the decision cheap:

- The React code knows only the relative path `/api`. Whatever the production
  proxy turns out to be, **no file under `src/` will change.**
- Deep links such as `/clients/CLT0001` work in development and under
  `npm run preview` because Vite serves `index.html` for unknown paths. A
  static host will need the same SPA fallback rule, or those URLs will 404 on
  a page refresh.

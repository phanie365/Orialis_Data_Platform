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
├── server.js            # Production server: static assets + API proxy
├── Dockerfile           # Multi-stage build; context is frontend/, not the root
├── .dockerignore        # Keeps node_modules, dist/ and every .env out of the image
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
| `npm start` | The **production** server (`server.js`) on `:$PORT`, default `:8080`. Requires a prior `npm run build` |

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

Both are read in **Node**, on a server — never in the browser. Which file
reads them depends on the environment, and that is the only difference:

| Environment | Reader | Listens on | Attaches `X-API-Key` |
|---|---|---|---|
| Development | `vite.config.js` | `127.0.0.1:5173` | in the `proxyReq` hook |
| Production | `server.js` | `0.0.0.0:$PORT` | in `buildUpstreamHeaders()` |

Neither is bundled and neither is served. A third variable, **`PORT`**, exists
only in production: Render injects it, and `server.js` falls back to `8080`
when it is absent. It must not be set manually on Render.

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

The same guarantee was re-verified on the **deployed artefact**, which is the
form that actually reaches a user. Against the running container, serving the
bundle over HTTP and scanning the image filesystem:

```
absent  the key in the served JS and HTML
absent  'supabase', 'onrender', 'X-API-Key' in the served JS
absent  any .env file anywhere in the image
absent  any credential in the image's ENV metadata
```

The container holds five files — three built assets, `server.js` and
`package.json` — and no `node_modules`.

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
using the interface: the API key authenticates the *application*, not the
visitor, so anyone who can reach the frontend can read the CRM. That is
acceptable for an internal prototype over fictional data and would not be for
anything else — a deployed URL is reachable by anyone who has it. No search
box, no column
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

Implemented, built and tested. Two independent Docker services on Render.

The prediction made when `src/api/client.js` was written held: the React code
knows only the relative path `/api`, so adding a production proxy changed
**no file under `src/`**. The deployment layer is `server.js`, `Dockerfile`
and `.dockerignore` — three new files, and nothing else.

### Architecture

```
   Browser
      │
      │  same origin only — https://<frontend>.onrender.com
      │  /clients, /assets/…            → the SPA
      │  /api/v1/…                      → proxied below
      │  never sends or receives X-API-Key
      ▼
┌─────────────────────────────────────────────────────┐
│  Frontend service — Render Web Service (Docker)     │
│  frontend/server.js, Node 22, 0.0.0.0:$PORT         │
│                                                     │
│   1. /api/*  → proxy, adds X-API-Key: $CRM_API_KEY  │
│   2. dist/   → static assets                        │
│   3. *.ext   → 404                                  │
│   4. else    → index.html  (SPA fallback)           │
└─────────────────────────────────────────────────────┘
      │
      │  server-to-server, X-API-Key attached HERE
      ▼
┌─────────────────────────────────────────────────────┐
│  API service — Render Web Service (Docker)          │
│  CRM/app/main.py, FastAPI + uvicorn, 0.0.0.0:$PORT  │
│  Validates X-API-Key on every /api/v1 route         │
└─────────────────────────────────────────────────────┘
      │
      │  DATABASE_URL, sslmode=require, pooled
      ▼
┌─────────────────────────────────────────────────────┐
│  PostgreSQL on Supabase                             │
└─────────────────────────────────────────────────────┘
```

**The browser talks to exactly one origin: the frontend's own.** It never
reaches FastAPI directly, and it never reaches Supabase — not through a
client library, not through a connection string, not at all. Both hops below
the browser happen server-side.

Two consequences follow, and both were confirmed rather than assumed:

- **No CORS configuration is needed.** `CRM/app/main.py` declares no
  `CORSMiddleware` and does not need one: cross-origin rules govern browsers,
  and the only cross-origin request here is made by Node.
- **The API key cannot leak.** It exists in the frontend container's
  environment and in one outbound header. It is absent from `dist/`, absent
  from the image, and never logged — `server.js` reports only whether it is
  *set*.

### Why a Node server rather than a static host

A static host can serve `dist/` and can do an SPA fallback. It cannot hold a
secret. The CRM API requires `X-API-Key`, and that header can only be attached
in the browser — which means publishing the key — or on a server the user
never sees. `server.js` is that server, and it is the whole reason the
frontend is a Web Service rather than a Static Site.

It has **zero runtime dependencies**: it is written against the Node standard
library, so the runtime image contains no `node_modules` at all.

### Order of the request pipeline

The four steps above are ordered deliberately, and the ordering is the design.

A naive SPA fallback — *anything I don't recognise gets `index.html`* — turns
`GET /api/v1/nonexistent` into `200 OK` with an HTML body. `client.js` then
sees `response.ok`, calls `response.json()` on a page of HTML, and reports an
unintelligible parse error instead of the 404 the API actually returned.

So `/api` is matched **first** and returns unconditionally: an unknown API
path is FastAPI's own 404, an unreachable backend is a JSON 502 from
`server.js`, and nothing below `/api` can ever receive `index.html`.

Step 3 exists for the mirror-image reason. `/assets/index-OLDHASH.js` after a
deploy is a stale *asset*, not a route; falling it back to `index.html` would
answer 200 with a document the browser then tries to execute as JavaScript. A
file extension is what separates an asset from a route — no route in this app
has one.

### Build context is `frontend/`, not the repository root

```bash
docker build -t orialis-crm-frontend ./frontend
```

The repository-root `.dockerignore` contains the line `frontend/`,
deliberately: it keeps 2,300 `node_modules` files out of the API build
context, which took that context from 42.7 MB to 76 kB.

A `.dockerignore` applies to the **context**, so building the frontend from
the root would make this directory invisible to the daemon and every `COPY`
would fail on a file that plainly exists. The frontend therefore has its own
context and its own `.dockerignore`, and the root one is left untouched. On
Render this is the setting **Root Directory = `frontend`**.

### Running the image locally

```bash
docker build -t orialis-crm-frontend ./frontend

docker run --rm -p 8080:8080 \
    -e CRM_API_URL="https://orialis-data-platform.onrender.com" \
    -e CRM_API_KEY="<the same key the API reads>" \
    orialis-crm-frontend
```

No secret is baked in. There is no `ARG` and no `ENV` carrying a credential
anywhere in the `Dockerfile` — an `ENV` is image metadata, readable by anyone
who can pull or `docker inspect` the image, so both variables are supplied at
run time and exist only in the running process.

The image is a multi-stage build: the builder runs `npm ci` and
`npm run build`, and only `dist/`, `server.js` and `package.json` cross into
the runtime stage — **266 kB of application files** on top of `node:22-slim`.
It runs as the unprivileged `node` user (uid 1000), and the exec-form `CMD`
makes Node **pid 1**, so it receives `SIGTERM` directly and shuts down
gracefully in well under a second.

### Render configuration

Two services, from the same repository, differing only in Root Directory.

| | Frontend | API |
|---|---|---|
| Type | Web Service | Web Service |
| Runtime | Docker | Docker |
| **Root Directory** | **`frontend`** | *(empty — repo root)* |
| Dockerfile Path | `frontend/Dockerfile` | `Dockerfile` |
| Health Check Path | `/` | `/` |
| Docker Command | *(empty — the `CMD` suffices)* | *(empty)* |

Environment variables:

| Service | Variable | Value |
|---|---|---|
| Frontend | `CRM_API_URL` | The API service's URL, no trailing slash |
| Frontend | `CRM_API_KEY` | 🔒 secret — **byte-identical** to the API's |
| API | `DATABASE_URL` | 🔒 secret — Supabase, `sslmode=require` |
| API | `CRM_API_KEY` | 🔒 secret — the same value |

`PORT` is injected by Render on both services and must not be set by hand.

A mismatch between the two `CRM_API_KEY` values is not a subtle failure: every
proxied call returns 401 and the dashboard says the API rejected the request.

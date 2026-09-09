# Orialis CRM

Orialis CRM is the customer relationship management source system of the
Orialis Data Platform project.

Orialis is a fictional international wealth management company created as part
of an end-to-end Data Engineering project.

This CRM simulates the operational system used to manage clients, advisors,
branches and client-advisor interactions.

> Current version: V1 — Local Prototype

## Overview

The CRM currently manages four main entities:

- **Branches** — Orialis offices across France, Switzerland, Belgium and Italy
- **Advisors** — wealth management advisors attached to a branch
- **Clients** — individual clients assigned to an advisor
- **Interactions** — historical and daily interactions between clients and advisors

## Current Dataset

The local prototype contains:

- 7 branches
- 100 advisors
- 5,000 clients
- 30,000 initial historical interactions

Additional interactions can be generated through the daily activity simulator.

All data is synthetic and generated specifically for this project.

## Architecture

The current V1 runs locally using:

- Python
- SQLite
- FastAPI
- Uvicorn

The SQLite database is generated locally and is not committed to the repository.

## Project Structure

```
CRM/
├── app/        # FastAPI application
├── scripts/    # Database initialization, seed and simulation scripts
├── data/       # Local SQLite database (not versioned)
└── tests/      # Automated tests (coming later)
```

## Database Initialization

The CRM database can be reconstructed from scratch using the provided scripts.

Run them in the following order:

1. `init_db.py`
2. `seed_branches.py`
3. `seed_advisors.py`
4. `seed_clients.py`
5. `seed_interactions.py`

This creates the initial CRM dataset.

## Daily Activity Simulation

After the initial dataset has been created, daily CRM activity can be simulated
using:

`simulate_daily_activity.py`

Each execution represents one business day and generates new client interactions
as well as updates to existing client records.

This allows the CRM to behave like an evolving operational source system rather
than a static dataset.

## API

The CRM exposes its data through a REST API built with FastAPI.

Main resources:

- `/api/v1/branches`
- `/api/v1/advisors`
- `/api/v1/clients`
- `/api/v1/interactions`

The API supports filtering, pagination and incremental extraction mechanisms.

Interactive API documentation is available through FastAPI Swagger UI when the
application is running locally.

## Incremental Data

The CRM was designed to support future incremental ingestion into the Orialis
Data Platform.

Examples:

- Clients can be extracted using their `updated_at` timestamp.
- Interactions can be extracted using their `created_at` timestamp.

A dedicated script is included to validate this incremental behaviour.

## Roadmap

V1 — Local CRM prototype ✅

Next iterations will introduce:

- PostgreSQL
- Cloud database hosting
- Docker
- Automated testing
- CI/CD
- Cloud API deployment
- CRM web interface
- Integration with the Orialis Data Platform

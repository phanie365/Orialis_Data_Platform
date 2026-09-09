# Orialis Data Platform

Orialis Data Platform is an end-to-end Data Engineering project built around
a fictional international wealth management company.

The objective is to design and build a realistic data ecosystem from the
ground up, starting from operational source systems and progressively
addressing the data challenges that emerge from them.

## Context

Orialis is a fictional wealth management company operating across several
European countries.

Like many organizations, its data is distributed across multiple operational
systems, each designed to serve a specific business purpose.

The project simulates this environment through several independent source
systems, including:

- a CRM for clients, advisors and customer interactions;
- an ERP for operational and financial processes;
- a Portfolio Management System for portfolios, positions and transactions;
- a Market Data source;
- business-managed files and manual data sources.

These systems will progressively form the source ecosystem of the Orialis
Data Platform.

## Project Approach

The project intentionally does not start with a predefined technology stack.

Instead, it follows a needs-driven approach:

```
Business context
        ↓
Operational systems
        ↓
Data flows and constraints
        ↓
Data engineering requirements
        ↓
Architecture decisions
        ↓
Technology selection
```

Each architectural and technological choice will therefore be introduced
and justified as new requirements emerge.

The objective is not simply to combine popular Data Engineering tools,
but to understand why and when each component becomes necessary.

## Repository

Each operational system is developed as an independent component of the
Orialis ecosystem and contains its own documentation.

The repository will evolve progressively as new systems and data engineering
components are introduced.

## Disclaimer

Orialis is a fictional company created for educational and portfolio purposes.

All organizations, people and operational data represented in this project
are synthetic.

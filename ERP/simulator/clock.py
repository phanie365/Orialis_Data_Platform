"""
The ERP business clock: which day is being simulated, and what came before.

---------------------------------------------------------------------------
WHY THIS NEEDS A TABLE, AND WHY IT IS NOT A TENTH BUSINESS TABLE
---------------------------------------------------------------------------

The simulator must never replay a day it has already run, and days must
advance one at a time. That requires remembering where it got to.

The obvious trick - derive the date from `MAX(invoice_date)` - does not work,
and the reason is instructive: a weekend produces no business rows at all, so
the maximum date would not move, and Saturday would be simulated for ever. The
clock has to be stored, not inferred, precisely because some days legitimately
leave no trace.

So `simulation_state` exists. It is **operational metadata, not ERP data**:

    - it is not one of the nine business tables the specification froze
    - `ERP/scripts/init_db.py` is untouched; this table is created by the
      simulator itself, on first run
    - nothing references it, and it references nothing - no foreign key in
      either direction
    - a future data platform would never extract it: it describes the
      simulation, not the firm

It holds exactly one row. Think of it as the simulator's own bookmark, stored
beside the data because that is the only place both survive together.
"""

from datetime import date, timedelta

STATE_TABLE = "simulation_state"

# A single row, pinned by a constant primary key. The CHECK is what makes
# "exactly one row" a property of the schema rather than a convention.
CREATE_STATE = f"""
CREATE TABLE IF NOT EXISTS {STATE_TABLE} (
    state_id            SMALLINT     NOT NULL,
    last_simulated_date DATE         NOT NULL,
    last_run_at         TIMESTAMPTZ  NOT NULL,
    days_simulated      INTEGER      NOT NULL DEFAULT 0,
    business_days       INTEGER      NOT NULL DEFAULT 0,

    CONSTRAINT simulation_state_pk PRIMARY KEY (state_id),
    CONSTRAINT simulation_state_single_row_chk CHECK (state_id = 1),
    CONSTRAINT simulation_state_counts_chk
        CHECK (days_simulated >= 0 AND business_days >= 0
               AND business_days <= days_simulated)
);
"""

# Where the history the seed produced stops. The first simulated day is the
# one after this.
SEED_LAST_DAY = date(2026, 9, 30)


def ensure_state(cursor):
    """Create the clock table if it is missing, and seed it from the data.

    On a database that has just been seeded there is no clock yet, so the
    starting point is taken from the history itself: the latest business date
    present. From then on the stored value is authoritative.
    """
    cursor.execute(CREATE_STATE)

    cursor.execute(f"SELECT last_simulated_date FROM {STATE_TABLE} WHERE state_id = 1")
    row = cursor.fetchone()
    if row:
        return row[0]

    cursor.execute("""
        SELECT GREATEST(
            COALESCE((SELECT MAX(invoice_date) FROM supplier_invoices), DATE '2000-01-01'),
            COALESCE((SELECT MAX(payment_date) FROM payments), DATE '2000-01-01'),
            COALESCE((SELECT MAX(expense_date) FROM expenses), DATE '2000-01-01')
        )
    """)
    latest = cursor.fetchone()[0]
    if latest < date(2001, 1, 1):
        latest = SEED_LAST_DAY

    cursor.execute(
        f"INSERT INTO {STATE_TABLE} "
        f"(state_id, last_simulated_date, last_run_at, days_simulated, business_days) "
        f"VALUES (1, %s, now(), 0, 0)",
        (latest,),
    )
    return latest


def next_day(cursor):
    """The day that should be simulated next: the one after the last one."""
    return ensure_state(cursor) + timedelta(days=1)


def check_forward(cursor, requested):
    """Refuse to simulate a day that is not strictly after the last one.

    This is the guard the specification asks for. Re-running yesterday would
    not merely duplicate rows - it would file expense claims on a date the
    ledger has already closed, and the resulting history would be unreadable.
    The simulator is deliberately not idempotent, which makes going backwards
    unrecoverable rather than merely untidy.
    """
    last = ensure_state(cursor)
    if requested <= last:
        raise ValueError(
            f"Journee demandee : {requested}. Derniere journee simulee : {last}.\n"
            f"Le simulateur n'avance que vers l'avant : une execution ne peut "
            f"pas revenir dans le passe.\n"
            f"Prochaine journee attendue : {last + timedelta(days=1)}."
        )
    if requested > last + timedelta(days=1):
        # A gap is allowed - it simply means the intervening days were not
        # simulated - but it should be a deliberate choice, so it is reported.
        return f"saut de {(requested - last).days - 1} jour(s) non simule(s)"
    return None


def record(cursor, day, was_business_day):
    """Advance the clock. Called inside the day's transaction, so a rolled
    back day leaves the clock exactly where it was."""
    cursor.execute(
        f"""
        UPDATE {STATE_TABLE}
        SET last_simulated_date = %s,
            last_run_at = now(),
            days_simulated = days_simulated + 1,
            business_days = business_days + %s
        WHERE state_id = 1
        """,
        (day, 1 if was_business_day else 0),
    )


def status(cursor):
    """The clock, for display."""
    ensure_state(cursor)
    cursor.execute(
        f"SELECT last_simulated_date, last_run_at, days_simulated, business_days "
        f"FROM {STATE_TABLE} WHERE state_id = 1"
    )
    return cursor.fetchone()

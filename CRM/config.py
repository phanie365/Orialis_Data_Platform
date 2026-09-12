"""
Shared PostgreSQL configuration for the Orialis CRM.

Both halves of the CRM need the same four things:

    - find the `.env` file at the repository root and load it
    - read DATABASE_URL out of the environment
    - refuse to start on a connection string that cannot work
    - keep credentials out of anything that gets printed

This module sits at the top of the `CRM` package, beside `app/` and
`scripts/`, because it belongs to neither: the API and the tooling are two
consumers of the same configuration. Putting it inside `app/` would make the
scripts depend on the API; putting it inside `scripts/` would make the API
depend on the tooling, which is backwards.

Usage from the API (a package inside CRM):

    from ..config import get_database_url, scrub

Usage from a script (run directly, so the repository root is put on the
import path first):

    sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
    from CRM.config import get_database_url, scrub

Importing this module loads the `.env` file as a side effect, which is what
every caller wants.
"""

import os
from pathlib import Path

from dotenv import load_dotenv

# ---------------------------------------------------------------------------
# Where the configuration lives
# ---------------------------------------------------------------------------
# __file__                      .../CRM/config.py
# .parent                       .../CRM
# .parent.parent                repository root, where .env lives
#
# Paths are derived from the file itself, not from the current working
# directory, so everything works no matter where it is launched from.

REPO_ROOT = Path(__file__).resolve().parent.parent
ENV_PATH = REPO_ROOT / ".env"

load_dotenv(ENV_PATH)


# ---------------------------------------------------------------------------
# Reading the connection string
# ---------------------------------------------------------------------------

def get_database_url():
    """Return DATABASE_URL, or stop with a clear message.

    The value itself is never printed: it carries the password.
    """
    url = os.environ.get("DATABASE_URL", "").strip()
    if not url:
        raise SystemExit(
            "DATABASE_URL is not set.\n"
            f"Expected it in: {ENV_PATH}\n"
            "Copy .env.example to .env and fill in the Supabase connection "
            "string (see that file for the exact shape)."
        )

    check_database_url(url)
    return url


def check_database_url(url):
    """Catch the connection strings that cannot possibly work.

    Only the SHAPE is inspected, and only the diagnosis is printed - never
    any part of the value itself.
    """
    problems = []

    if not url.startswith(("postgresql://", "postgres://")):
        problems.append(
            "it does not start with 'postgresql://'. A Supabase PostgreSQL "
            "connection string is expected, not an HTTP project URL."
        )

    credentials, _host_part = split_credentials(url)

    if credentials is None:
        problems.append(
            "no 'user:password@host' section could be found. Expected shape: "
            "postgresql://<USER>:<PASSWORD>@<HOST>:<PORT>/<DATABASE>"
        )
    else:
        # Supabase displays the password slot as [YOUR-PASSWORD]. The square
        # brackets are placeholder markers and must be deleted along with the
        # placeholder text - they are not part of the password.
        # Only the credentials are checked: a bracketed IPv6 host is legitimate.
        if "[" in credentials or "]" in credentials:
            problems.append(
                "the credentials still contain a square bracket. Supabase "
                "shows the slot as [YOUR-PASSWORD]; the brackets themselves "
                "must be removed, keeping only the password."
            )

        # A raw '@' inside the password splits the URL in the wrong place.
        _scheme, _separator, tail = url.partition("://")
        if tail.count("@") > 1:
            problems.append(
                "the password appears to contain an unencoded '@'. Special "
                "characters must be percent-encoded: @ -> %40, : -> %3A, "
                "/ -> %2F, ? -> %3F, # -> %23, %% -> %25."
            )

    if problems:
        raise SystemExit(
            "DATABASE_URL is malformed:\n"
            + "".join(f"  - {problem}\n" for problem in problems)
            + f"Fix it in: {ENV_PATH}"
        )

    if "sslmode=" not in url:
        print("  [note] DATABASE_URL has no 'sslmode' parameter; appending "
              "'?sslmode=require' is recommended for Supabase.")


# ---------------------------------------------------------------------------
# Keeping credentials out of the output
# ---------------------------------------------------------------------------

def split_credentials(url):
    """Return (credentials, host_part) from a connection URL, or (None, None).

    Deliberately hand-rolled instead of urllib.parse.urlsplit: urlsplit
    raises ValueError on a URL whose credentials contain square brackets,
    because it reads them as an IPv6 literal. This helper must never raise -
    it is used while reporting errors.
    """
    try:
        _scheme, separator, tail = url.partition("://")
        if not separator:
            return None, None
        credentials, at_sign, host_part = tail.rpartition("@")
        if not at_sign:
            return None, tail
        return credentials, host_part
    except Exception:
        return None, None


def get_password(url):
    """Return the password held in the URL, or None."""
    credentials, _host = split_credentials(url)
    if not credentials:
        return None
    _user, colon, password = credentials.partition(":")
    return password if colon and password else None


def scrub(message, url):
    """Remove the credentials from a message before it is displayed.

    Driver errors mention the host and port, which is useful for debugging,
    but they must never leak the password.
    """
    text = str(message)
    if url:
        text = text.replace(url, "<DATABASE_URL>")
    password = get_password(url)
    if password:
        text = text.replace(password, "********")
    return text

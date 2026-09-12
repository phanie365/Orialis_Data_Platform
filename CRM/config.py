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
    return ensure_sslmode(url)


# SSL modes that actually encrypt the connection. `prefer` and `allow` fall
# back to plaintext without telling anyone, and `disable` never encrypts.
ENCRYPTING_SSL_MODES = {"require", "verify-ca", "verify-full"}


def ensure_sslmode(url, default="require"):
    """Return the URL with an sslmode that encrypts, without touching a
    deliberate choice.

    Supabase accepts unencrypted-capable connections, and libpq defaults to
    `sslmode=prefer` - which means "encrypt if the server offers it, carry on
    in plaintext otherwise". That is a silent downgrade, so the connection
    string is made explicit here rather than left to a default.

    Three cases:

        no sslmode           -> `sslmode=require` is appended
        an encrypting mode   -> left exactly as written
        a weaker mode        -> left as written, with a loud warning

    The last case matters: overwriting an explicit `sslmode=disable` would
    silently undo a deliberate decision - someone tunnelling through a local
    proxy, for instance. The warning names the risk and leaves the choice.

    The URL itself is never printed, in any branch.
    """
    # Split on the FIRST "?" - that is where the query string starts, the
    # same rule libpq applies. Deliberately not urlsplit(), which raises on a
    # URL whose credentials contain square brackets.
    base, separator, query = url.partition("?")

    for parameter in query.split("&"):
        name, _, value = parameter.partition("=")
        if name.strip().lower() != "sslmode":
            continue

        mode = value.strip().lower()
        if mode in ENCRYPTING_SSL_MODES:
            return url  # already encrypting; nothing to say, nothing to do

        print(f"  [warning] DATABASE_URL sets sslmode={mode!r}, which does "
              f"not guarantee an encrypted connection. It has been left as "
              f"written rather than silently overridden - change it to "
              f"'{default}' unless this is intentional.")
        return url

    # No sslmode at all: add it, with the right separator depending on
    # whether the URL already carries query parameters.
    print(f"  [note] sslmode was missing from DATABASE_URL; connections use "
          f"sslmode={default}.")
    return f"{url}{'&' if separator else '?'}sslmode={default}"


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

    # sslmode is not checked here: `ensure_sslmode()` guarantees it instead of
    # merely recommending it.


# ---------------------------------------------------------------------------
# Reading the API key
# ---------------------------------------------------------------------------

def get_api_key():
    """Return CRM_API_KEY, or stop with a clear message.

    The value itself is never printed: it is the credential that protects
    every /api/v1 route.

    Missing is a hard stop rather than "run without protection". An API that
    silently starts with authentication disabled is worse than one that
    refuses to start: the failure would only be noticed once the endpoints
    are already public.
    """
    key = os.environ.get("CRM_API_KEY", "").strip()
    if not key:
        raise SystemExit(
            "CRM_API_KEY is not set.\n"
            f"Expected it in: {ENV_PATH}\n"
            "Every /api/v1 route is protected by it, so the API refuses to\n"
            "start without one. Generate a value and add it to .env:\n"
            '    python -c "import secrets; print(secrets.token_urlsafe(32))"'
        )

    if len(key) < 32:
        print("  [note] CRM_API_KEY is shorter than 32 characters; a short "
              "key is easier to guess. 32+ random characters are recommended.")

    return key


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

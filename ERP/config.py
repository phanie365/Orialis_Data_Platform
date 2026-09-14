"""
Shared PostgreSQL configuration for the Orialis ERP.

This module is the ERP counterpart of `CRM/config.py`, and it is deliberately
a SEPARATE FILE rather than an import.

---------------------------------------------------------------------------
WHY THIS DUPLICATES CRM/config.py INSTEAD OF IMPORTING IT
---------------------------------------------------------------------------

The obvious move would be `from CRM.config import get_database_url`. It is
refused here, and the refusal is the point of the whole ERP exercise.

The ERP simulates a SECOND SOURCE SYSTEM: a different application, bought or
built separately, running on its own database. Two such systems never share a
configuration module. If the ERP imported from the CRM package:

    - the ERP would stop working the day the CRM package moved or was renamed
    - a reader of the ERP would learn that a system called "CRM" exists
    - the two systems would share a single point of failure

All three are exactly what the separation is meant to prevent. The duplication
below is not an oversight: it is the price of independence, and it is what the
real situation looks like.

The ERP therefore reads its OWN variable:

    ERP_DATABASE_URL        this file
    DATABASE_URL            CRM/config.py - never read here

Pointing both at the same database would defeat the exercise. They are meant
to be two different PostgreSQL instances, and nothing in this file can reach
the CRM one.

---------------------------------------------------------------------------
WHAT THIS MODULE DOES
---------------------------------------------------------------------------

    - find the `.env` file at the repository root and load it
    - read ERP_DATABASE_URL out of the environment
    - refuse to start on a connection string that cannot work
    - guarantee the connection is encrypted
    - keep credentials out of anything that gets printed

It sits at the top of the `ERP` package, beside `scripts/`, so that a future
API and the tooling are two consumers of one configuration rather than one
depending on the other.

Usage from a script (run directly, so the repository root goes on the import
path first):

    sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
    from ERP.config import get_database_url, scrub

Importing this module loads the `.env` file as a side effect, which is what
every caller wants.
"""

import os
from pathlib import Path

from dotenv import load_dotenv

# ---------------------------------------------------------------------------
# Where the configuration lives
# ---------------------------------------------------------------------------
# __file__                      .../ERP/config.py
# .parent                       .../ERP
# .parent.parent                repository root, where .env lives
#
# Paths are derived from the file itself, not from the current working
# directory, so everything works no matter where it is launched from.

REPO_ROOT = Path(__file__).resolve().parent.parent
ENV_PATH = REPO_ROOT / ".env"

load_dotenv(ENV_PATH)

# The single environment variable this package reads. Named so that it can
# never be confused with the CRM one, and so that setting one has no effect on
# the other.
ENV_VARIABLE = "ERP_DATABASE_URL"


# ---------------------------------------------------------------------------
# Reading the connection string
# ---------------------------------------------------------------------------

def get_database_url():
    """Return ERP_DATABASE_URL, or stop with a clear message.

    The value itself is never printed: it carries the password.
    """
    url = os.environ.get(ENV_VARIABLE, "").strip()
    if not url:
        raise SystemExit(
            f"{ENV_VARIABLE} is not set.\n"
            f"Expected it in: {ENV_PATH}\n"
            "The ERP uses its OWN PostgreSQL database, separate from the CRM.\n"
            "See .env.example for the exact shape, and note that DATABASE_URL\n"
            "(the CRM one) is deliberately NOT used here."
        )

    check_database_url(url)
    return ensure_sslmode(url)


# SSL modes that actually encrypt the connection. `prefer` and `allow` fall
# back to plaintext without telling anyone, and `disable` never encrypts.
ENCRYPTING_SSL_MODES = {"require", "verify-ca", "verify-full"}

# Hosts for which an unencrypted connection is legitimate: a database running
# on this machine, reached over the loopback interface. TLS there protects
# nothing, and requiring it would make local schema validation impossible
# against a throwaway container.
LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1", "[::1]"}


def is_local(url):
    """True when the URL points at a database on this machine."""
    _credentials, host_part = split_credentials(url)
    if not host_part:
        return False
    # host_part looks like "host:port/database?params"
    host = host_part.split("/")[0].split("?")[0]
    # Strip the port, taking care not to break an IPv6 literal in brackets.
    if host.startswith("["):
        host = host.partition("]")[0] + "]"
    else:
        host = host.partition(":")[0]
    return host.lower() in LOCAL_HOSTS


def ensure_sslmode(url, default="require"):
    """Return the URL with an sslmode that encrypts, without touching a
    deliberate choice.

    A managed PostgreSQL accepts unencrypted-capable connections, and libpq
    defaults to `sslmode=prefer` - "encrypt if the server offers it, carry on
    in plaintext otherwise". That is a silent downgrade, so the connection
    string is made explicit here rather than left to a default.

    Four cases:

        a local host         -> left alone; TLS to 127.0.0.1 protects nothing
        no sslmode           -> `sslmode=require` is appended
        an encrypting mode   -> left exactly as written
        a weaker mode        -> left as written, with a loud warning

    The last case matters: overwriting an explicit `sslmode=disable` would
    silently undo a deliberate decision. The warning names the risk and leaves
    the choice.

    The URL itself is never printed, in any branch.
    """
    if is_local(url):
        return url

    # Split on the FIRST "?" - that is where the query string starts, the same
    # rule libpq applies. Deliberately not urlsplit(), which raises on a URL
    # whose credentials contain square brackets.
    base, separator, query = url.partition("?")

    for parameter in query.split("&"):
        name, _, value = parameter.partition("=")
        if name.strip().lower() != "sslmode":
            continue

        mode = value.strip().lower()
        if mode in ENCRYPTING_SSL_MODES:
            return url  # already encrypting; nothing to say, nothing to do

        print(f"  [warning] {ENV_VARIABLE} sets sslmode={mode!r}, which does "
              f"not guarantee an encrypted connection. It has been left as "
              f"written rather than silently overridden - change it to "
              f"'{default}' unless this is intentional.")
        return url

    print(f"  [note] sslmode was missing from {ENV_VARIABLE}; connections use "
          f"sslmode={default}.")
    return f"{url}{'&' if separator else '?'}sslmode={default}"


def check_database_url(url):
    """Catch the connection strings that cannot possibly work.

    Only the SHAPE is inspected, and only the diagnosis is printed - never any
    part of the value itself.
    """
    problems = []

    if not url.startswith(("postgresql://", "postgres://")):
        problems.append(
            "it does not start with 'postgresql://'. A PostgreSQL connection "
            "string is expected, not an HTTP project URL."
        )

    credentials, _host_part = split_credentials(url)

    if credentials is None:
        problems.append(
            "no 'user:password@host' section could be found. Expected shape: "
            "postgresql://<USER>:<PASSWORD>@<HOST>:<PORT>/<DATABASE>"
        )
    else:
        # A provider that displays the password slot as [YOUR-PASSWORD] leaves
        # the brackets in the copied string. They are placeholder markers and
        # must be deleted along with the placeholder text.
        # Only the credentials are checked: a bracketed IPv6 host is legitimate.
        if "[" in credentials or "]" in credentials:
            problems.append(
                "the credentials still contain a square bracket. The brackets "
                "around a placeholder password must be removed, keeping only "
                "the password itself."
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
            f"{ENV_VARIABLE} is malformed:\n"
            + "".join(f"  - {problem}\n" for problem in problems)
            + f"Fix it in: {ENV_PATH}"
        )


# ---------------------------------------------------------------------------
# Reading the API key
# ---------------------------------------------------------------------------

API_KEY_VARIABLE = "ERP_API_KEY"


def get_api_key():
    """Return ERP_API_KEY, or stop with a clear message.

    The value itself is never printed: it is the credential that protects
    every /api/v1 route.

    Missing is a HARD STOP rather than "run without protection". An API that
    silently starts with authentication disabled is worse than one that
    refuses to start: the failure would only be noticed once the endpoints
    were already public.

    Deliberately a DIFFERENT variable from the CRM's `CRM_API_KEY`. The two
    systems are separate products with separate credentials - a single key
    opening both would make the CRM's consumers automatically consumers of
    the ERP, which is precisely the coupling this project exists to avoid.
    """
    key = os.environ.get(API_KEY_VARIABLE, "").strip()
    if not key:
        raise SystemExit(
            f"{API_KEY_VARIABLE} is not set.\n"
            f"Expected it in: {ENV_PATH}\n"
            "Every /api/v1 route is protected by it, so the API refuses to\n"
            "start without one. It must be DIFFERENT from CRM_API_KEY.\n"
            "Generate a value and add it to .env:\n"
            '    python -c "import secrets; print(secrets.token_urlsafe(32))"'
        )

    if len(key) < 32:
        print(f"  [note] {API_KEY_VARIABLE} is shorter than 32 characters; a "
              f"short key is easier to guess. 32+ random characters are "
              f"recommended.")

    return key


# ---------------------------------------------------------------------------
# Keeping credentials out of the output
# ---------------------------------------------------------------------------

def split_credentials(url):
    """Return (credentials, host_part) from a connection URL, or (None, None).

    Deliberately hand-rolled instead of urllib.parse.urlsplit: urlsplit raises
    ValueError on a URL whose credentials contain square brackets, because it
    reads them as an IPv6 literal. This helper must never raise - it is used
    while reporting errors.
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
        text = text.replace(url, f"<{ENV_VARIABLE}>")
    password = get_password(url)
    if password:
        text = text.replace(password, "********")
    return text

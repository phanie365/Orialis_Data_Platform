"""API authentication for the Orialis ERP API.

A single shared key, sent in the `X-API-Key` header, protects every
`/api/v1` route. No user management, no token lifecycle, no session: one key,
checked on every call.

That is the right size for what this is - a read-only source system read by a
data pipeline, not an application with accounts. Anything more would be
machinery without a problem to solve.

The key comes from `ERP_API_KEY`, read through `ERP/config.py`. It is
deliberately a DIFFERENT credential from the CRM's: two source systems are
two products, and one key opening both would silently make every CRM consumer
an ERP consumer too.

It is never hardcoded, never printed, and never reaches anything that runs in
a browser.
"""

import secrets

from fastapi import HTTPException, Security, status
from fastapi.security import APIKeyHeader

from ..config import get_api_key

# Read and validate ONCE, at import - the same reasoning as DATABASE_URL in
# database.py. A missing key must stop the application at startup, not turn
# every request into a surprise.
API_KEY = get_api_key()

# `auto_error=False` hands us the missing-header case instead of letting
# FastAPI answer on its own. Its default would be 403; the contract here is
# 401 for BOTH a missing and a wrong key, so the check below owns the
# response.
#
# Declaring the scheme this way is also what puts the padlock in Swagger and
# the `securitySchemes` entry in the OpenAPI document.
api_key_header = APIKeyHeader(
    name="X-API-Key",
    auto_error=False,
    description="Shared API key. Required on every /api/v1 route.",
)


def require_api_key(provided: str | None = Security(api_key_header)) -> None:
    """Reject the request unless it carries the right API key.

    Used as a dependency on the parent router, so it runs before any endpoint
    function and no individual route has to remember it.
    """
    # `compare_digest` takes the same time whether the first character
    # differs or the last one does. A plain `==` returns faster on an early
    # mismatch, which leaks how much of a guess was correct.
    if provided is None or not secrets.compare_digest(provided, API_KEY):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing or invalid API key",
            # Names the header the caller should send, without hinting at the
            # value. The body is identical whether the header was absent or
            # wrong: distinguishing them would help an attacker.
            headers={"WWW-Authenticate": "X-API-Key"},
        )

# Orialis CRM API - container image
#
# Build context is the REPOSITORY ROOT, not CRM/. The application is launched
# as `uvicorn CRM.app.main:app`, and CRM/app/database.py imports `..config`,
# which resolves to `CRM.config` - so the CRM package has to sit at the root
# of the working directory inside the image, exactly as it does in the repo.
#
# Build:  docker build -t orialis-crm-api .
# Run:    docker run --rm -p 8000:8000 \
#             -e DATABASE_URL="..." -e CRM_API_KEY="..." orialis-crm-api
#
# No secret is baked in: DATABASE_URL and CRM_API_KEY are read from the
# environment at runtime, never from the image.

# Slim, not alpine: alpine uses musl, which has no manylinux wheels, so
# psycopg would have to be compiled. Slim keeps the prebuilt wheels and stays
# small. The tag is pinned to 3.12 to match the version the CRM was built and
# tested on.
FROM python:3.12-slim

# PYTHONDONTWRITEBYTECODE - no .pyc files; the container is rebuilt, not reused
# PYTHONUNBUFFERED        - logs reach Render's collector immediately instead
#                           of sitting in a buffer until the process exits
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Dependencies are installed BEFORE the source is copied. Docker caches each
# layer, so editing a router does not reinstall FastAPI and psycopg - only the
# last two COPY layers are rebuilt.
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# Only what the API actually imports. CRM/scripts/ is deliberately absent:
# the seeds and the simulator are not part of the running service, and
# leaving them out keeps the image to its runtime surface.
COPY CRM/config.py ./CRM/config.py
COPY CRM/app ./CRM/app

# Run as an unprivileged user. Created after pip install so that the packages
# are installed as root and stay read-only for the application.
RUN useradd --create-home --uid 10001 orialis
USER orialis

# Documentation only - it publishes nothing by itself. The real port comes
# from $PORT at runtime.
EXPOSE 8000

# Render injects the port to listen on as $PORT. An exec-form CMD does not
# expand shell variables, so the command runs through `sh -c` to read it,
# falling back to 8000 when the variable is absent (local runs).
#
# `exec` is what makes this safe: it REPLACES the shell with uvicorn, so
# uvicorn becomes PID 1 and receives SIGTERM directly when the platform stops
# the container. Without it, the shell would stay PID 1, swallow the signal,
# and the FastAPI lifespan would never run - leaving the PostgreSQL pool's
# connections open on Supabase until the server timed them out.
CMD ["sh", "-c", "exec uvicorn CRM.app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]

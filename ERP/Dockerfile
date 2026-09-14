# Orialis ERP API - container image
#
# ---------------------------------------------------------------------------
# BUILD CONTEXT IS THE REPOSITORY ROOT, FILTERED BY ERP/Dockerfile.dockerignore
# ---------------------------------------------------------------------------
#
#   docker build -f ERP/Dockerfile -t orialis-erp-api .
#
# The root is required: the application is launched as `ERP.app.main`, and
# ERP/app/database.py imports `..config`, i.e. `ERP.config`. The `ERP` package
# therefore has to sit at the root of the working directory inside the image,
# exactly as it does in the repository.
#
# The repository-root `.dockerignore` belongs to the CRM image and is left
# untouched. With BuildKit, an ignore file named after the Dockerfile
# (`ERP/Dockerfile.dockerignore`) takes precedence over it, so this build sends
# only the handful of files listed there - not ERP/exports, not the frontend,
# not a .env. On Render: Dockerfile Path = ERP/Dockerfile, build context = the
# repository root.
#
# Run:
#   docker run --rm -p 8001:8000 \
#       -e ERP_DATABASE_URL="postgresql://..." -e ERP_API_KEY="..." orialis-erp-api
#
# No secret is baked in: ERP_DATABASE_URL and ERP_API_KEY are read from the
# environment at runtime. There is no ARG and no ENV holding a credential.

# Slim, not alpine: psycopg's prebuilt wheels target glibc. Pinned to 3.12, the
# version the ERP was built and tested on.
FROM python:3.12-slim

# PYTHONDONTWRITEBYTECODE - no .pyc files; the container is rebuilt, not reused
# PYTHONUNBUFFERED        - logs reach the platform's collector immediately
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Dependencies before sources, so editing a router does not reinstall them.
#
# One requirements file serves the whole repository. `openpyxl` is filtered
# out here because only the file exports use it, and the exports are not part
# of the API image. Everything else - FastAPI, uvicorn, psycopg, the pool,
# python-dotenv - is imported by the running API.
COPY requirements.txt ./
RUN sed '/^openpyxl/d' requirements.txt > /tmp/requirements-api.txt \
    && pip install --no-cache-dir -r /tmp/requirements-api.txt \
    && rm /tmp/requirements-api.txt

# Only what the API imports. ERP/seed, ERP/simulator, ERP/scripts,
# ERP/exporters, ERP/tests, ERP/exports and ERP/frontend are absent on
# purpose: none of them runs inside the service.
COPY ERP/config.py ./ERP/config.py
COPY ERP/app ./ERP/app

# Unprivileged user, created after pip install so the installed packages stay
# owned by root and read-only to the application.
RUN useradd --create-home --uid 10001 orialis
USER orialis

# Documentation only. The real port comes from $PORT at runtime.
EXPOSE 8000

# `sh -c` to expand ${PORT:-8000}; `exec` so uvicorn REPLACES the shell and
# becomes PID 1. It then receives SIGTERM directly, runs the FastAPI lifespan
# shutdown and closes the PostgreSQL pool, instead of being killed ~30 s later
# with its connections left open on the database.
CMD ["sh", "-c", "exec uvicorn ERP.app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]

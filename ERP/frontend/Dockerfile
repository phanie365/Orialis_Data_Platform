# Orialis ERP frontend - container image
#
# BUILD CONTEXT IS ERP/frontend/, filtered by ERP/frontend/.dockerignore:
#
#   docker build -t orialis-erp-frontend ./ERP/frontend
#
# Run:
#   docker run --rm -p 8080:8080 \
#       -e ERP_API_URL="http://erp-api:8000" -e ERP_API_KEY="..." orialis-erp-frontend
#
# No secret is baked in: there is no ARG and no ENV holding a credential. An ENV
# is stored in the image metadata and readable by anyone who can pull the
# image; ERP_API_URL and ERP_API_KEY exist only in the running container.


# ===========================================================================
# Stage 1 - builder: Node + Vite, thrown away after `npm run build`
# ===========================================================================
# glibc-based slim image for the build: Vite's native dependencies (esbuild,
# rollup) ship prebuilt binaries for it, installed from the lockfile.
FROM node:22-slim AS builder

WORKDIR /build

# Lockfile first, so editing a component re-runs the build but not `npm ci`.
COPY package.json package-lock.json ./
RUN npm ci

# Only what Vite reads. vite.config.js imports apiProxy.js (the DEV proxy); it
# reads ERP_API_KEY only to configure that proxy, and no key exists in this
# stage anyway - the bundle is identical with or without one.
COPY index.html vite.config.js apiProxy.js ./
COPY src ./src
RUN npm run build


# ===========================================================================
# Stage 2 - runtime: the bundle, one server file, nothing else
# ===========================================================================
# server.js uses the Node standard library only, so the runtime needs no
# node_modules and no native binary - alpine is enough and keeps the image
# small.
FROM node:22-alpine AS runtime

# The only ENV in the image.
ENV NODE_ENV=production

WORKDIR /app

COPY --from=builder /build/dist ./dist
# package.json is needed for `"type": "module"` (server.js uses `import`).
COPY server.js package.json ./

# `node` (uid 1000) exists in the official image. The files stay owned by root,
# hence read-only to the process that serves them.
USER node

# Documentation only; server.js reads $PORT and falls back to 8080.
EXPOSE 8080

# Exec form: Node is PID 1 and receives SIGTERM itself, so the shutdown
# handlers in server.js run.
CMD ["node", "server.js"]

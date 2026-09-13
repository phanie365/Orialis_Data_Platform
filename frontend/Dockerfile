# Orialis CRM frontend - container image
#
# ---------------------------------------------------------------------------
# BUILD CONTEXT IS `frontend/`, NOT THE REPOSITORY ROOT
# ---------------------------------------------------------------------------
#
#   docker build -t orialis-crm-frontend ./frontend
#
# This is not a stylistic choice, and getting it wrong is the most likely way
# this deployment fails. The repository-root `.dockerignore` contains the line
# `frontend/`, deliberately: it keeps 2,300 node_modules files out of the API
# build context, which took that context from 42.7 MB back down to 76 kB.
#
# A `.dockerignore` applies to the CONTEXT, so building from the root would
# make this entire directory invisible to the daemon and every COPY below
# would fail on a file that plainly exists on disk. The frontend therefore
# gets its own context and its own .dockerignore, and the root one is left
# exactly as it is. On Render this means: Root Directory = frontend.
#
# Run:
#   docker run --rm -p 8080:8080 \
#       -e CRM_API_URL="https://orialis-data-platform.onrender.com" \
#       -e CRM_API_KEY="..." orialis-crm-frontend
#
# No secret is baked in. There is no ARG and no ENV holding a credential
# anywhere in this file - see the note above the runtime stage for why that
# distinction matters more here than it does for the API.


# ===========================================================================
# Stage 1 - builder
# ===========================================================================
#
# Node is needed to BUILD the bundle and is then thrown away. Everything in
# this stage - the toolchain, node_modules, the sources - exists only inside
# the builder layers, which are never pushed and never run. Only the files
# explicitly copied out of it in stage 2 reach the final image.
#
# The tag is pinned to the major version the project is developed on.
FROM node:22-slim AS builder

WORKDIR /build

# Dependencies first, sources second. Docker caches each layer, so editing a
# component re-runs `npm run build` but NOT `npm ci` - the slow step - as long
# as package.json and the lockfile are unchanged.
#
# `npm ci` rather than `npm install`: it installs the exact tree recorded in
# package-lock.json and fails if the lockfile disagrees with package.json,
# which is what makes the build reproducible instead of merely likely.
COPY package.json package-lock.json ./
RUN npm ci

# Only what Vite reads. Copying the directory wholesale would work, but naming
# the inputs keeps a stray local file - a scratch script, a .env that slipped
# past .dockerignore - from silently entering the build.
COPY index.html vite.config.js ./
COPY src ./src

# The bundle. Note what is NOT passed here: no secret, no ARG, no build-time
# environment. `vite.config.js` reads CRM_API_KEY, but only to configure a DEV
# proxy that never runs in production - and because the variable is not
# prefixed with VITE_, Vite would refuse to inline it into the bundle even if
# it were set. The output of this command is identical whether or not a key
# exists on the machine running it.
RUN npm run build


# ===========================================================================
# Stage 2 - runtime
# ===========================================================================
#
# The whole point of the split: this image contains the compiled bundle, one
# server file, and nothing else. No sources, no Vite, no npm tree.
#
# It also contains NO node_modules, because server.js has zero runtime
# dependencies - it is written against the Node standard library alone. There
# is nothing to install here and no third-party code in the deployed artefact.
FROM node:22-slim AS runtime

# Signals Node and its ecosystem that this is not a development process.
# Deliberately the only ENV in the image: an ENV is baked into the image
# metadata and readable by anyone who can `docker inspect` or pull it, so a
# credential must never become one. CRM_API_URL and CRM_API_KEY are supplied
# at `docker run` / by Render, at runtime, and exist only in the running
# container process.
ENV NODE_ENV=production

WORKDIR /app

# The build output, lifted out of the builder. This is the only thing that
# crosses the stage boundary besides the two files below.
COPY --from=builder /build/dist ./dist

# The server, and package.json for its `"type": "module"` declaration - without
# it Node would read server.js as CommonJS and reject its `import` statements.
# package.json also carries `npm start`. Its devDependencies are just text
# here: nothing installs them.
COPY server.js ./server.js
COPY package.json ./package.json

# Run as an unprivileged user. `node` (uid 1000) already exists in the official
# image, so no useradd is needed. The application files stay owned by root and
# are therefore read-only to this user - the process serves them and cannot
# rewrite them, which is exactly the posture wanted for static assets.
USER node

# Documentation only; it publishes nothing by itself. The real port comes from
# $PORT at runtime, and 8080 is only what server.js falls back to when the
# variable is absent.
EXPOSE 8080

# Exec form, with `node` as argv[0]: Node becomes pid 1 and receives SIGTERM
# DIRECTLY from the platform.
#
# The alternative - `CMD ["sh", "-c", "node server.js"]` - would leave the
# shell as pid 1, where it would swallow the signal, the SIGTERM handlers in
# server.js would never run, in-flight requests would be cut, and Render would
# resort to SIGKILL ~30s later on every single deploy.
#
# No `sh -c` is needed to read $PORT either: server.js reads process.env.PORT
# itself, in JavaScript, so there is no shell expansion to perform.
CMD ["node", "server.js"]

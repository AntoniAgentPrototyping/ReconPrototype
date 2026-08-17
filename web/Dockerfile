# The web BFF. Multi-stage, so the runtime image carries traced dependencies
# only — roughly 150 MB rather than ~1 GB with the whole node_modules tree.
#
# Unlike deploy/Dockerfile before M5, this one has been BUILT. See
# docs/07-VERIFICATION.md.

FROM node:26-alpine AS deps
WORKDIR /app
# Only the manifests, so a source edit does not re-resolve the dependency tree.
COPY package.json package-lock.json* ./
RUN npm ci --no-audit --no-fund

FROM node:26-alpine AS builder
WORKDIR /app
COPY --from=deps /app/node_modules ./node_modules
COPY . .
# NEXT_PUBLIC_* would be baked in here. Nothing is: the API address is read from
# process.env at request time on the server, so one image runs in every
# environment (see next.config.mjs).
ENV NEXT_TELEMETRY_DISABLED=1
RUN npm run build

FROM node:26-alpine AS runner
WORKDIR /app
ENV NODE_ENV=production \
    NEXT_TELEMETRY_DISABLED=1 \
    PORT=3000 \
    HOSTNAME=0.0.0.0

RUN addgroup -g 10001 -S nodejs && adduser -u 10001 -S nextjs -G nodejs

# `standalone` emits its own minimal server plus only the traced modules.
COPY --from=builder --chown=nextjs:nodejs /app/.next/standalone ./
COPY --from=builder --chown=nextjs:nodejs /app/.next/static ./.next/static

USER nextjs
EXPOSE 3000

# This is the only service that should get a public address. The api, the worker
# and the database stay on the private network — the BFF holds the bearer token
# server-side and the browser never sees it (web/lib/api.ts).
CMD ["node", "server.js"]

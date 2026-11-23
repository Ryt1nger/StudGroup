FROM node:22-alpine AS frontend

WORKDIR /src
RUN corepack enable

COPY package.json pnpm-lock.yaml pnpm-workspace.yaml ./
COPY apps/frontend/package.json apps/frontend/package.json
COPY packages/shared-types/package.json packages/shared-types/package.json
RUN pnpm install --frozen-lockfile --filter @studgroup/frontend...

COPY apps/frontend apps/frontend
COPY packages/shared-types packages/shared-types

ARG VITE_API_BASE_URL
ENV VITE_API_BASE_URL=${VITE_API_BASE_URL}
RUN pnpm --filter @studgroup/frontend build

FROM caddy:2-alpine
COPY deploy/server/Caddyfile /etc/caddy/Caddyfile
COPY --from=frontend /src/apps/frontend/dist /srv/frontend

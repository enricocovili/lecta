# Lecta frontend: Astro SSR (node adapter, middleware mode) + /api proxy.
FROM node:24.21.0-bookworm-slim AS build
ENV ASTRO_TELEMETRY_DISABLED=1 NEXT_TELEMETRY_DISABLED=1 DO_NOT_TRACK=1 CI=1
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY . .
RUN npm run typecheck && npm run build && npm prune --omit=dev

FROM node:24.21.0-bookworm-slim
ENV NODE_ENV=production ASTRO_TELEMETRY_DISABLED=1 DO_NOT_TRACK=1 PORT=4321 HOST=0.0.0.0
WORKDIR /app
COPY --from=build --chown=node:node /app/node_modules ./node_modules
COPY --from=build --chown=node:node /app/dist ./dist
COPY --from=build --chown=node:node /app/server.mjs /app/healthcheck.mjs /app/package.json ./
USER node
EXPOSE 4321
CMD ["node", "server.mjs"]

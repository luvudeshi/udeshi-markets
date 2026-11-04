# syntax=docker/dockerfile:1.7
# Builds the trading terminal's image. Build context: frontend/.
#   docker build -t udeshi-web frontend
#
# The API's address is read when the server starts (UDESHI_API_URL), not baked into the build:
# the browser connects to it directly, so it must be an address the browser can reach.

# ---- Dependencies: exactly the lock file's versions -----------------------------------------
FROM node:24-bookworm-slim AS deps
WORKDIR /app
COPY package.json package-lock.json ./
RUN --mount=type=cache,target=/root/.npm npm ci --no-audit --no-fund

# ---- Build: a self-contained server (output: "standalone") ------------------------------------
FROM node:24-bookworm-slim AS build
WORKDIR /app
ENV NEXT_TELEMETRY_DISABLED=1 NEXT_OUTPUT=standalone
COPY --from=deps /app/node_modules node_modules
COPY . .
RUN npm run build

# ---- Runtime: the standalone server and its static assets; non-root -------------------------
FROM node:24-bookworm-slim AS runtime
WORKDIR /app
ENV NODE_ENV=production NEXT_TELEMETRY_DISABLED=1 HOSTNAME=0.0.0.0 PORT=3000 \
    UDESHI_API_URL=http://localhost:8000
RUN useradd --create-home --uid 10001 app
COPY --from=build --chown=app /app/.next/standalone ./
COPY --from=build --chown=app /app/.next/static .next/static
USER app
EXPOSE 3000
HEALTHCHECK --interval=10s --timeout=3s --start-period=5s --retries=3 \
  CMD node -e "fetch('http://127.0.0.1:3000/').then(r => process.exit(r.ok ? 0 : 1), () => process.exit(1))"
CMD ["node", "server.js"]

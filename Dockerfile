# Single production image with three roles — `bot`, `web` and `migrate`.
# See docker-entrypoint.sh for why they share one image and one tag.
#
# The dev-only Mini App image (./Dockerfile.web, used by docker-compose.dev.yml)
# is unaffected and stays as it is.

# --- Frontend build: never reaches the final image, Node stays here ---
FROM node:22-alpine AS web-builder

WORKDIR /web
# Manifests first, so the npm layer survives changes to application code.
COPY web/package.json web/package-lock.json ./
RUN npm ci
COPY web/ ./
RUN npm run build

# --- Builder: production dependencies only ---
FROM python:3.14-slim AS builder
COPY --from=ghcr.io/astral-sh/uv:0.11.31 /uv /usr/local/bin/uv

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_NO_CACHE=1

WORKDIR /app
# Only the lock and the manifest, so the dependency layer is reused whenever
# application code changes. --frozen fails the build if they disagree, rather
# than silently resolving something the lockfile never pinned.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project --extra web

# --- Final image: no build tools, no tests, no Node, non-root ---
FROM python:3.14-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    DATABASE_URL=sqlite+aiosqlite:////data/school_bot.db \
    HEARTBEAT_FILE=/data/.heartbeat \
    WEB_DIST_DIR=/app/web_dist \
    PATH="/app/.venv/bin:$PATH"

RUN groupadd --gid 1000 appuser \
    && useradd --uid 1000 --gid appuser --shell /bin/false --create-home appuser

COPY --from=builder /app/.venv /app/.venv

WORKDIR /app
COPY bot.py config.py utils.py alembic.ini ./
COPY database ./database
COPY handlers ./handlers
COPY keyboards ./keyboards
COPY services ./services
COPY middleware ./middleware
COPY alembic ./alembic
# Shared by both roles: the bot mints /web launch tokens through
# web_api.security, and application/ holds the DTOs the API returns.
COPY application ./application
COPY web_api ./web_api
COPY --from=web-builder /web/dist ./web_dist

COPY docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh
RUN chmod +x /usr/local/bin/docker-entrypoint.sh \
    && mkdir -p /data && chown -R appuser:appuser /app /data

USER appuser

VOLUME ["/data"]
EXPOSE 8000

# No HEALTHCHECK: the roles are checked differently (the bot touches
# HEARTBEAT_FILE, the API answers /api/v1/health), and the directive is static.
# Set --health-cmd on each container instead.

ENTRYPOINT ["docker-entrypoint.sh"]
CMD ["bot"]

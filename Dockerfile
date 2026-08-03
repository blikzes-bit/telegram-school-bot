# --- Builder: install production dependencies only ---
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
# The `web` extra is included ahead of the image gaining its `web` role, so the
# dependency layer does not have to be rebuilt for it a commit later.
RUN uv sync --frozen --no-dev --no-install-project --extra web

# --- Final image: no build tools, no tests, non-root ---
FROM python:3.14-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    DATABASE_URL=sqlite+aiosqlite:////data/school_bot.db \
    HEARTBEAT_FILE=/data/.heartbeat \
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
# handlers/web.py (the /web command) mints launch tokens via web_api.security /
# web_api.settings, so the bot image needs them even though it never serves the
# API. Both modules are stdlib-only — no FastAPI in this image.
COPY web_api ./web_api

RUN mkdir -p /data && chown -R appuser:appuser /app /data

USER appuser

VOLUME ["/data"]

HEALTHCHECK --interval=60s --timeout=5s --start-period=30s --retries=3 \
    CMD python -c "import os,sys,time; p=os.environ['HEARTBEAT_FILE']; sys.exit(0 if os.path.exists(p) and time.time()-os.path.getmtime(p)<150 else 1)"

CMD ["python", "bot.py"]

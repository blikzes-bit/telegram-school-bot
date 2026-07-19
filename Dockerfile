# --- Builder: install production dependencies only ---
FROM python:3.12-slim AS builder

ENV PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_ROOT_USER_ACTION=ignore

WORKDIR /app
COPY requirements.txt .
RUN pip install --prefix=/install -r requirements.txt

# --- Final image: no build tools, no tests, non-root ---
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    DATABASE_URL=sqlite+aiosqlite:////data/school_bot.db \
    HEARTBEAT_FILE=/data/.heartbeat

RUN groupadd --gid 1000 appuser \
    && useradd --uid 1000 --gid appuser --shell /bin/false --create-home appuser

COPY --from=builder /install /usr/local

WORKDIR /app
COPY bot.py config.py utils.py alembic.ini ./
COPY database ./database
COPY handlers ./handlers
COPY keyboards ./keyboards
COPY services ./services
COPY middleware ./middleware
COPY alembic ./alembic

RUN mkdir -p /data && chown -R appuser:appuser /app /data

USER appuser

VOLUME ["/data"]

HEALTHCHECK --interval=60s --timeout=5s --start-period=30s --retries=3 \
    CMD python -c "import os,sys,time; p=os.environ['HEARTBEAT_FILE']; sys.exit(0 if os.path.exists(p) and time.time()-os.path.getmtime(p)<150 else 1)"

CMD ["python", "bot.py"]

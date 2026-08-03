"""
Safe administrator diagnostics for the ``/status`` command.

Everything here is read-only and returns *derived* health signals only — never
secrets. It deliberately exposes no ``DATABASE_URL``, token, filesystem path or
any personal data: only counts, versions and coarse timing. The collection is
split from the presentation so it can be unit-tested without a running bot.
"""
import datetime
import logging
import os

from sqlalchemy import func, select

from config import APP_VERSION, HEARTBEAT_FILE
from database import db
from database.migrate import get_db_revision, get_script_head
from database.models import ReminderJob

logger = logging.getLogger(__name__)

# An outbox row still "in_progress" for longer than this is treated as stuck
# (a crash between chunks) and reported separately as a failed/stalled job.
STALE_JOB_MINUTES = 15
# The scheduler touches the heartbeat every minute; older than this means the
# background tick is not running.
HEARTBEAT_HEALTHY_SECONDS = 180


def _utcnow() -> datetime.datetime:
    return datetime.datetime.now(datetime.timezone.utc)


def _parse_iso(value: str) -> datetime.datetime | None:
    try:
        parsed = datetime.datetime.fromisoformat(value)
    except (ValueError, TypeError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=datetime.timezone.utc)
    return parsed


def heartbeat_age_seconds() -> float | None:
    """Seconds since the scheduler last touched the heartbeat file, or None if
    it has never been written (process just started / file missing)."""
    try:
        mtime = os.path.getmtime(HEARTBEAT_FILE)
    except OSError:
        return None
    return max(0.0, _utcnow().timestamp() - mtime)


async def _job_counts() -> dict[str, int]:
    """Counts of outbox reminder jobs by health bucket (global, not per-chat:
    this is an operational metric, not user data)."""
    now = _utcnow()
    stale_before = now - datetime.timedelta(minutes=STALE_JOB_MINUTES)
    pending = in_progress = stale = 0
    async with db.AsyncSessionLocal() as session:
        result = await session.execute(
            select(ReminderJob.status, ReminderJob.updated_at, func.count())
            .group_by(ReminderJob.status, ReminderJob.updated_at)
        )
        for status, updated_at, count in result.all():
            if status == "pending":
                pending += count
            elif status == "in_progress":
                in_progress += count
                parsed = _parse_iso(updated_at) if updated_at else None
                if parsed is not None and parsed < stale_before:
                    stale += count
    return {"pending": pending, "in_progress": in_progress, "stale": stale}


async def collect_status() -> dict:
    """Gather every diagnostic value shown by ``/status``. Individual failures
    degrade gracefully to ``None`` so the command never raises."""
    heartbeat = heartbeat_age_seconds()
    scheduler_alive = heartbeat is not None and heartbeat <= HEARTBEAT_HEALTHY_SECONDS
    return {
        "app_version": APP_VERSION,
        "scheduler_alive": scheduler_alive,
        "heartbeat_age": heartbeat,
        "db_revision": await get_db_revision(),
        "head_revision": get_script_head(),
        "jobs": await _job_counts(),
    }


def _format_age(age: float | None) -> str:
    if age is None:
        return "нет данных"
    if age < 90:
        return f"{int(age)} с назад"
    return f"{int(age // 60)} мин назад"


def format_status(data: dict) -> str:
    """Render the collected status as a Russian HTML message. All values are
    numeric/enumerated, so no user text is interpolated."""
    jobs = data["jobs"]
    scheduler = "🟢 работает" if data["scheduler_alive"] else "🔴 не отвечает"
    db_rev = data["db_revision"] or "—"
    head_rev = data["head_revision"] or "—"
    migrations = "✅ актуальна" if db_rev == head_rev and data["db_revision"] else "⚠️ проверьте"
    lines = [
        "🛠 <b>Статус бота</b>",
        "",
        f"Версия приложения: <code>{data['app_version']}</code>",
        f"Планировщик: {scheduler}",
        f"Последний тик: {_format_age(data['heartbeat_age'])}",
        f"Миграция БД: <code>{db_rev}</code> / <code>{head_rev}</code> ({migrations})",
        "",
        "<b>Очередь напоминаний</b>",
        f"⏳ в ожидании: {jobs['pending']}",
        f"🔄 в процессе: {jobs['in_progress']}",
        f"⚠️ зависших: {jobs['stale']}",
    ]
    return "\n".join(lines)

"""
Tests for the admin ``/status`` diagnostics command and its collector.

Covered:
  * ``format_status`` renders versions, scheduler state and job buckets;
  * ``collect_status`` counts outbox jobs by health bucket (pending /
    in_progress / stale) and never raises when the alembic table is absent;
  * heartbeat freshness drives the scheduler-alive signal;
  * the command is admin-only in a group but open in a private chat, and never
    leaks secrets.
"""
import datetime
from types import SimpleNamespace

import services.status as status_service
from database.models import Chat, ReminderJob
from handlers.status import cmd_status

CHAT_ID = -100123
ADMIN_ID = 555
USER_ID = 777


class FakeBot:
    def __init__(self, admins=None):
        self.admins = admins or set()

    async def get_chat_member(self, chat_id, user_id):
        status = "administrator" if user_id in self.admins else "member"
        return SimpleNamespace(status=status)


class FakeMessage:
    def __init__(self, chat_type="supergroup", user_id=ADMIN_ID, bot=None):
        self.chat = SimpleNamespace(id=CHAT_ID, type=chat_type)
        self.from_user = SimpleNamespace(id=user_id, full_name="Аня")
        self.bot = bot or FakeBot({ADMIN_ID})
        self.answers = []

    async def answer(self, text, **kwargs):
        self.answers.append((text, kwargs))
        return self


def _utcnow():
    return datetime.datetime.now(datetime.timezone.utc)


async def _make_chat(Session):
    async with Session() as session:
        session.add(Chat(chat_id=CHAT_ID, chat_type="supergroup", is_onboarded=True))
        await session.commit()


async def _add_job(Session, status, minutes_ago, kind="hw", day_offset=0):
    ts = (_utcnow() - datetime.timedelta(minutes=minutes_ago)).isoformat()
    async with Session() as session:
        session.add(ReminderJob(
            chat_id=CHAT_ID, kind=kind,
            job_date=datetime.date.today() + datetime.timedelta(days=day_offset),
            chunks_json="[]", chunks_total=1, chunks_sent=0,
            status=status, updated_at=ts,
        ))
        await session.commit()


def test_format_status_renders_all_fields():
    text = status_service.format_status({
        "app_version": "1.2.3",
        "scheduler_alive": True,
        "heartbeat_age": 5.0,
        "db_revision": "abc",
        "head_revision": "abc",
        "jobs": {"pending": 2, "in_progress": 1, "stale": 3},
    })
    assert "1.2.3" in text
    assert "🟢" in text
    assert "в ожидании: 2" in text
    assert "в процессе: 1" in text
    assert "зависших: 3" in text
    assert "актуальна" in text


def test_format_status_flags_migration_mismatch():
    text = status_service.format_status({
        "app_version": "1.0.0",
        "scheduler_alive": False,
        "heartbeat_age": None,
        "db_revision": "old",
        "head_revision": "new",
        "jobs": {"pending": 0, "in_progress": 0, "stale": 0},
    })
    assert "🔴" in text
    assert "проверьте" in text
    # Never leaks a path/token/DATABASE_URL.
    assert "sqlite" not in text.lower()
    assert "token" not in text.lower()


async def test_collect_status_counts_job_buckets(db):
    await _make_chat(db)
    await _add_job(db, "pending", 1, kind="hw", day_offset=0)
    await _add_job(db, "in_progress", 1, kind="sched", day_offset=0)      # fresh
    await _add_job(db, "in_progress", 999, kind="hw", day_offset=1)       # stale
    await _add_job(db, "done", 1, kind="sched", day_offset=1)             # ignored

    data = await status_service.collect_status()
    assert data["jobs"]["pending"] == 1
    assert data["jobs"]["in_progress"] == 2
    assert data["jobs"]["stale"] == 1
    # A dev DB built by init_db has no alembic_version table -> graceful None.
    assert data["db_revision"] is None


def test_heartbeat_age_missing_file(monkeypatch, tmp_path):
    monkeypatch.setattr(status_service, "HEARTBEAT_FILE", str(tmp_path / "nope"))
    assert status_service.heartbeat_age_seconds() is None


def test_heartbeat_age_fresh_file(monkeypatch, tmp_path):
    hb = tmp_path / ".heartbeat"
    hb.write_text("x")
    monkeypatch.setattr(status_service, "HEARTBEAT_FILE", str(hb))
    age = status_service.heartbeat_age_seconds()
    assert age is not None and age < 5


async def test_status_command_admin_gets_report(db, monkeypatch):
    await _make_chat(db)
    monkeypatch.setattr(status_service, "heartbeat_age_seconds", lambda: 3.0)
    msg = FakeMessage(chat_type="supergroup", user_id=ADMIN_ID, bot=FakeBot({ADMIN_ID}))
    await cmd_status(msg)
    assert len(msg.answers) == 1
    text, kwargs = msg.answers[0]
    assert "Статус бота" in text
    assert kwargs.get("parse_mode") == "HTML"


async def test_status_command_rejects_non_admin_in_group(db):
    await _make_chat(db)
    msg = FakeMessage(chat_type="supergroup", user_id=USER_ID, bot=FakeBot({ADMIN_ID}))
    await cmd_status(msg)
    assert len(msg.answers) == 1
    text, _ = msg.answers[0]
    assert "администратор" in text.lower()
    assert "Статус бота" not in text


async def test_status_command_allowed_in_private_chat(db, monkeypatch):
    monkeypatch.setattr(status_service, "heartbeat_age_seconds", lambda: 3.0)
    msg = FakeMessage(chat_type="private", user_id=USER_ID, bot=FakeBot())
    await cmd_status(msg)
    assert len(msg.answers) == 1
    text, _ = msg.answers[0]
    assert "Статус бота" in text

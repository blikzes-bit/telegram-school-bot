import datetime
from collections import defaultdict
from typing import List, Dict, Optional, Tuple
from sqlalchemy import select, update, delete, event, func, or_, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from database.models import (
    Base, Chat, LessonSlot, Schedule, Homework, ReminderJob, ExtraActivity,
    DayOverride, LessonOverride, AuditLog, HomeworkAttachment,
    WebUser, ChatMembership, WebLaunchToken, WebSession,
)
from config import DATABASE_URL

engine = create_async_engine(DATABASE_URL, echo=False)

@event.listens_for(engine.sync_engine, "connect")
def set_sqlite_pragma(dbapi_connection, connection_record):
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    # Until the Mini App the database file had a single writer (the bot). The
    # web API is a second process writing to it — sessions, launch tokens and
    # homework edits — so the default rollback journal would make readers and
    # the writer block each other and surface as "database is locked".
    # WAL lets readers run against a snapshot while a write is in flight, and
    # busy_timeout makes the short remaining overlaps wait instead of failing.
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA busy_timeout=5000")
    cursor.close()

AsyncSessionLocal = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

async def _ensure_column(conn, table: str, column: str, ddl: str):
    """
    Adds ``column`` to ``table`` via ``ALTER TABLE ... ADD COLUMN <ddl>`` only if
    it doesn't already exist. Used only as a dev/test convenience for brand-new
    databases created via ``create_all`` — production schema changes are managed
    by Alembic (see alembic/), not by this ad-hoc helper.
    """
    result = await conn.execute(text(f"PRAGMA table_info({table})"))
    existing_columns = {row[1] for row in result.fetchall()}
    if column not in existing_columns:
        await conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}"))

async def init_db():
    """
    Creates the schema from the current models for a brand-new (dev/test)
    database. Production deployments must run ``alembic upgrade head``
    instead (see bot.py / alembic/) so existing data is migrated rather than
    silently left on a stale schema.
    """
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        # Convenience for any pre-Alembic dev DB that predates these columns.
        await _ensure_column(conn, "chats", "last_hw_reminder_date", "DATE")
        await _ensure_column(conn, "chats", "last_sch_reminder_date", "DATE")
        await _ensure_column(conn, "chats", "hw_reminder_enabled", "BOOLEAN NOT NULL DEFAULT 1")
        await _ensure_column(conn, "chats", "schedule_reminder_enabled", "BOOLEAN NOT NULL DEFAULT 1")
        await _ensure_column(conn, "chats", "is_blocked", "BOOLEAN NOT NULL DEFAULT 0")
        await _ensure_column(conn, "chats", "week_mode", "BOOLEAN NOT NULL DEFAULT 0")
        await _ensure_column(conn, "chats", "week_anchor_monday", "DATE")
        await _ensure_column(conn, "schedule", "week_type", "VARCHAR NOT NULL DEFAULT 'all'")
        await _ensure_column(conn, "chats", "hw_duetoday_enabled", "BOOLEAN NOT NULL DEFAULT 1")
        await _ensure_column(conn, "chats", "hw_duetoday_time", "VARCHAR NOT NULL DEFAULT '07:30'")
        await _ensure_column(conn, "chats", "changes_reminder_enabled", "BOOLEAN NOT NULL DEFAULT 1")
        await _ensure_column(conn, "chats", "extra_reminder_enabled", "BOOLEAN NOT NULL DEFAULT 1")
        await _ensure_column(conn, "chats", "last_duetoday_reminder_date", "DATE")
        await _ensure_column(conn, "chats", "last_changes_reminder_date", "DATE")
        await _ensure_column(conn, "chats", "quiet_start", "VARCHAR")
        await _ensure_column(conn, "chats", "quiet_end", "VARCHAR")
        await _ensure_column(conn, "extra_activities", "reminder_enabled", "BOOLEAN NOT NULL DEFAULT 0")
        await _ensure_column(conn, "extra_activities", "reminder_minutes", "INTEGER NOT NULL DEFAULT 60")
        await _ensure_column(conn, "chats", "hw_edit_policy", "VARCHAR NOT NULL DEFAULT 'collaborative'")
        from services.timeservice import DEFAULT_TIMEZONE
        await _ensure_column(
            conn, "chats", "timezone",
            f"VARCHAR NOT NULL DEFAULT '{DEFAULT_TIMEZONE}'",
        )
        await _ensure_column(conn, "chats", "title", "VARCHAR")
        # Authorship columns; NULL for every pre-existing row by design.
        for table in ("homework", "extra_activities", "day_overrides", "lesson_overrides"):
            await _ensure_column(conn, table, "created_by_user_id", "BIGINT")
            await _ensure_column(conn, table, "created_by_name", "VARCHAR")
            await _ensure_column(conn, table, "updated_by_user_id", "BIGINT")
            await _ensure_column(conn, table, "updated_by_name", "VARCHAR")
            await _ensure_column(conn, table, "created_at", "VARCHAR")
            await _ensure_column(conn, table, "updated_at", "VARCHAR")

async def get_or_create_chat(chat_id: int, chat_type: str) -> Chat:
    """
    Fetch the Chat row for ``chat_id``, creating it if missing.

    Concurrent callers (e.g. two near-simultaneous updates for a chat that has
    never been seen before) can both observe "no row yet" and both attempt to
    insert. Rather than letting the second INSERT's IntegrityError propagate,
    we catch it, roll back, and re-SELECT — the row created by the winning
    transaction is then returned normally.
    """
    async with AsyncSessionLocal() as session:
        result = await session.execute(select(Chat).where(Chat.chat_id == chat_id))
        chat = result.scalar_one_or_none()
        if chat is not None:
            return chat

        chat = Chat(chat_id=chat_id, chat_type=chat_type)
        session.add(chat)
        try:
            await session.commit()
        except IntegrityError:
            await session.rollback()
            result = await session.execute(select(Chat).where(Chat.chat_id == chat_id))
            chat = result.scalar_one_or_none()
            if chat is None:
                raise
            return chat
        await session.refresh(chat)
        return chat

async def get_chat(chat_id: int) -> Optional[Chat]:
    """Read the Chat row without creating it (returns None if unknown)."""
    async with AsyncSessionLocal() as session:
        result = await session.execute(select(Chat).where(Chat.chat_id == chat_id))
        return result.scalar_one_or_none()


async def set_week_mode(chat_id: int, enabled: bool, anchor_monday: Optional[datetime.date] = None):
    """
    Enable/disable alternating (A/B) weeks for a chat. When enabling, an
    ``anchor_monday`` (the Monday that starts week A) should be supplied;
    disabling leaves the stored A/B templates in place (harmless) so they
    survive a later re-enable.
    """
    values: dict = {"week_mode": enabled}
    if anchor_monday is not None:
        values["week_anchor_monday"] = anchor_monday
    async with AsyncSessionLocal() as session:
        await session.execute(update(Chat).where(Chat.chat_id == chat_id).values(**values))
        await session.commit()


async def copy_schedule_week(chat_id: int, src_week: str, dst_week: str) -> int:
    """
    Replace the ``dst_week`` template with a copy of the ``src_week`` template
    for this chat (all days/lessons). Returns the number of rows copied. Used
    to seed weeks A and B from the regular ('all') schedule. Scoped to chat_id.
    """
    if src_week == dst_week:
        return 0
    async with AsyncSessionLocal() as session:
        src_rows = (await session.execute(
            select(Schedule)
            .where(Schedule.chat_id == chat_id)
            .where(Schedule.week_type == src_week)
        )).scalars().all()
        await session.execute(
            delete(Schedule)
            .where(Schedule.chat_id == chat_id)
            .where(Schedule.week_type == dst_week)
        )
        for row in src_rows:
            session.add(Schedule(
                chat_id=chat_id, week_type=dst_week, day_of_week=row.day_of_week,
                lesson_number=row.lesson_number, subject_name=row.subject_name,
            ))
        await session.commit()
        return len(src_rows)


async def mark_chat_seen(chat_id: int):
    """Clears the is_blocked flag once the chat interacts with the bot again."""
    async with AsyncSessionLocal() as session:
        await session.execute(
            update(Chat).where(Chat.chat_id == chat_id).values(is_blocked=False)
        )
        await session.commit()

async def set_onboarded(chat_id: int, status: bool):
    async with AsyncSessionLocal() as session:
        await session.execute(
            update(Chat).where(Chat.chat_id == chat_id).values(is_onboarded=status)
        )
        await session.commit()

async def get_lesson_slots(chat_id: int) -> List[LessonSlot]:
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(LessonSlot)
            .where(LessonSlot.chat_id == chat_id)
            .order_by(LessonSlot.lesson_number)
        )
        return list(result.scalars().all())

async def save_lesson_slots(chat_id: int, slots: List[Tuple[int, str, str]]):
    """
    slots: List of tuples (lesson_number, start_time, end_time)

    Replaces all lesson slots for the chat, and also prunes any Schedule rows
    whose lesson_number no longer has a matching slot (e.g. the lesson count
    was reduced during re-onboarding or the schedule-edit flow) so stale
    schedule entries can never outlive the slot they referred to.
    """
    async with AsyncSessionLocal() as session:
        await session.execute(delete(LessonSlot).where(LessonSlot.chat_id == chat_id))
        for num, start, end in slots:
            session.add(LessonSlot(chat_id=chat_id, lesson_number=num, start_time=start, end_time=end))

        max_lesson_number = max((num for num, _, _ in slots), default=0)
        await session.execute(
            delete(Schedule)
            .where(Schedule.chat_id == chat_id)
            .where(Schedule.lesson_number > max_lesson_number)
        )
        await session.commit()

async def get_schedule(
    chat_id: int, day_of_week: Optional[int] = None, week_type: str = "all"
) -> List[Schedule]:
    """
    Weekly template rows for a chat, scoped to a single ``week_type``
    ('all' by default — the single-template mode every chat starts in).
    """
    async with AsyncSessionLocal() as session:
        query = (
            select(Schedule)
            .where(Schedule.chat_id == chat_id)
            .where(Schedule.week_type == week_type)
        )
        if day_of_week is not None:
            query = query.where(Schedule.day_of_week == day_of_week)
        query = query.order_by(Schedule.lesson_number)
        result = await session.execute(query)
        return list(result.scalars().all())

async def save_schedule_day(
    chat_id: int, day_of_week: int, lessons: List[Tuple[int, str]], week_type: str = "all"
):
    """
    lessons: List of tuples (lesson_number, subject_name)

    Replaces the schedule for one day within a single ``week_type`` — the
    other week's template (and 'all') is left untouched.
    """
    async with AsyncSessionLocal() as session:
        # Clear existing schedule for this day + week
        await session.execute(
            delete(Schedule)
            .where(Schedule.chat_id == chat_id)
            .where(Schedule.day_of_week == day_of_week)
            .where(Schedule.week_type == week_type)
        )
        for num, subject in lessons:
            # We don't save empty/skipped lessons to schedule
            if subject and subject.strip().lower() != "skip":
                sch = Schedule(
                    chat_id=chat_id, week_type=week_type, day_of_week=day_of_week,
                    lesson_number=num, subject_name=subject.strip(),
                )
                session.add(sch)
        await session.commit()

async def finalize_onboarding(
    chat_id: int,
    chat_type: str,
    lesson_slots: List[Tuple[int, str, str]],
    schedule_by_day: Dict[int, List[Tuple[int, str]]],
):
    """
    Atomically persists the full result of onboarding (or re-onboarding):
    chat_type, lesson slots, the schedule for every day of the week, and the
    ``is_onboarded`` flag — all in one transaction. Either everything commits
    together, or (on any error) nothing is written and the chat's previous
    state is left completely untouched.

    Days absent from ``schedule_by_day`` (e.g. Saturday was configured before
    but is skipped this time) are cleared, matching the new configuration
    exactly rather than merging with stale leftovers.
    """
    max_lesson_number = max((num for num, _, _ in lesson_slots), default=0)

    async with AsyncSessionLocal() as session:
        result = await session.execute(select(Chat).where(Chat.chat_id == chat_id))
        chat = result.scalar_one_or_none()
        if chat is None:
            session.add(Chat(chat_id=chat_id, chat_type=chat_type))
        else:
            chat.chat_type = chat_type

        await session.execute(delete(LessonSlot).where(LessonSlot.chat_id == chat_id))
        for num, start, end in lesson_slots:
            session.add(LessonSlot(chat_id=chat_id, lesson_number=num, start_time=start, end_time=end))

        # Onboarding always (re)builds the single 'all' template; any A/B
        # templates a chat may have set up are left untouched.
        await session.execute(
            delete(Schedule).where(Schedule.chat_id == chat_id).where(Schedule.week_type == "all")
        )
        for day_of_week, lessons in schedule_by_day.items():
            for num, subject in lessons:
                if subject and subject.strip().lower() != "skip" and num <= max_lesson_number:
                    session.add(Schedule(
                        chat_id=chat_id, week_type="all", day_of_week=day_of_week,
                        lesson_number=num, subject_name=subject.strip(),
                    ))

        await session.execute(
            update(Chat).where(Chat.chat_id == chat_id).values(is_onboarded=True)
        )
        await session.commit()

async def update_schedule_slot(
    chat_id: int, day_of_week: int, lesson_number: int, subject_name: str, week_type: str = "all"
):
    async with AsyncSessionLocal() as session:
        # Delete first to overwrite (scoped to this week's template)
        await session.execute(
            delete(Schedule)
            .where(Schedule.chat_id == chat_id)
            .where(Schedule.day_of_week == day_of_week)
            .where(Schedule.lesson_number == lesson_number)
            .where(Schedule.week_type == week_type)
        )
        if subject_name and subject_name.strip() != "":
            sch = Schedule(
                chat_id=chat_id, week_type=week_type, day_of_week=day_of_week,
                lesson_number=lesson_number, subject_name=subject_name.strip(),
            )
            session.add(sch)
        await session.commit()

def _authorship_on_create(
    actor_user_id: Optional[int], actor_name: Optional[str]
) -> dict:
    """
    Column values stamping who created a record and when. ``created_*`` and
    ``updated_*`` start out identical; both stay NULL when there is no
    identifiable actor (background/system writes), matching legacy rows.
    """
    from services.audit import now_iso
    stamp = now_iso()
    return {
        "created_by_user_id": actor_user_id,
        "created_by_name": actor_name,
        "updated_by_user_id": actor_user_id,
        "updated_by_name": actor_name,
        "created_at": stamp,
        "updated_at": stamp,
    }


def _authorship_on_update(
    actor_user_id: Optional[int], actor_name: Optional[str]
) -> dict:
    """
    Column values stamping who last changed a record. ``created_*`` is never
    touched, so an old NULL author stays NULL rather than being rewritten to
    whoever happened to edit the row next.
    """
    from services.audit import now_iso
    return {
        "updated_by_user_id": actor_user_id,
        "updated_by_name": actor_name,
        "updated_at": now_iso(),
    }


async def add_homework(
    chat_id: int,
    subject_name: str,
    due_date: datetime.date,
    description: str,
    actor_user_id: Optional[int] = None,
    actor_name: Optional[str] = None,
) -> Homework:
    async with AsyncSessionLocal() as session:
        hw = Homework(
            chat_id=chat_id,
            subject_name=subject_name.strip(),
            due_date=due_date,
            description=description.strip(),
            is_completed=False,
            **_authorship_on_create(actor_user_id, actor_name),
        )
        session.add(hw)
        await session.commit()
        await session.refresh(hw)
        return hw

async def get_homework(chat_id: int, is_completed: Optional[bool] = None) -> List[Homework]:
    async with AsyncSessionLocal() as session:
        query = select(Homework).where(Homework.chat_id == chat_id)
        if is_completed is not None:
            query = query.where(Homework.is_completed == is_completed)
        query = query.order_by(Homework.due_date)
        result = await session.execute(query)
        return list(result.scalars().all())

async def mark_homework_completed(
    chat_id: int,
    homework_id: int,
    is_completed: bool = True,
    actor_user_id: Optional[int] = None,
    actor_name: Optional[str] = None,
) -> bool:
    """Returns False (no-op) if the homework doesn't exist for this chat."""
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            update(Homework)
            .where(Homework.chat_id == chat_id)
            .where(Homework.id == homework_id)
            .values(
                is_completed=is_completed,
                **_authorship_on_update(actor_user_id, actor_name),
            )
        )
        await session.commit()
        return result.rowcount > 0

async def delete_homework(chat_id: int, homework_id: int) -> bool:
    """Returns False (no-op) if the homework doesn't exist for this chat."""
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            delete(Homework)
            .where(Homework.chat_id == chat_id)
            .where(Homework.id == homework_id)
        )
        await session.commit()
        return result.rowcount > 0

async def get_homework_by_id(chat_id: int, homework_id: int) -> Optional[Homework]:
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(Homework)
            .where(Homework.chat_id == chat_id)
            .where(Homework.id == homework_id)
        )
        return result.scalar_one_or_none()

async def update_homework(
    chat_id: int,
    homework_id: int,
    subject_name: Optional[str] = None,
    description: Optional[str] = None,
    due_date: Optional[datetime.date] = None,
    actor_user_id: Optional[int] = None,
    actor_name: Optional[str] = None,
) -> bool:
    """
    Updates one or more fields of a homework entry, always scoped to both
    chat_id and homework_id. Returns False (no-op) if the homework does not
    belong to this chat, e.g. a stale button or an already-deleted entry.
    """
    values: dict = {}
    if subject_name is not None:
        values["subject_name"] = subject_name.strip()
    if description is not None:
        values["description"] = description.strip()
    if due_date is not None:
        values["due_date"] = due_date
    if not values:
        return False
    values.update(_authorship_on_update(actor_user_id, actor_name))

    async with AsyncSessionLocal() as session:
        result = await session.execute(
            update(Homework)
            .where(Homework.chat_id == chat_id)
            .where(Homework.id == homework_id)
            .values(**values)
        )
        await session.commit()
        return result.rowcount > 0

# --- Homework attachments (Telegram file references only, no binaries) ------

class AttachmentResult:
    """
    Outcome of trying to attach a file:

      * ``ok``        — stored; ``attachment`` is set;
      * ``missing``   — the homework doesn't exist for this chat (stale button);
      * ``limit``     — the per-homework attachment cap is already reached;
      * ``duplicate`` — this exact file is already attached to this homework.
    """
    __slots__ = ("status", "attachment")

    def __init__(self, status: str, attachment: Optional[HomeworkAttachment] = None):
        self.status = status
        self.attachment = attachment


async def get_homework_attachments(chat_id: int, homework_id: int) -> List[HomeworkAttachment]:
    """
    Attachments of one homework entry, oldest first. Joined against ``homework``
    and filtered by ``chat_id`` so a foreign chat can never read another chat's
    attachments by guessing a homework id.
    """
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(HomeworkAttachment)
            .join(Homework, Homework.id == HomeworkAttachment.homework_id)
            .where(Homework.chat_id == chat_id)
            .where(HomeworkAttachment.homework_id == homework_id)
            .order_by(HomeworkAttachment.id)
        )
        return list(result.scalars().all())


async def count_homework_attachments(chat_id: int, homework_id: int) -> int:
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(func.count())
            .select_from(HomeworkAttachment)
            .join(Homework, Homework.id == HomeworkAttachment.homework_id)
            .where(Homework.chat_id == chat_id)
            .where(HomeworkAttachment.homework_id == homework_id)
        )
        return int(result.scalar_one() or 0)


async def add_homework_attachment(
    chat_id: int,
    homework_id: int,
    file_id: str,
    file_unique_id: str,
    file_type: str,
    file_name: Optional[str] = None,
    file_size: Optional[int] = None,
    caption: Optional[str] = None,
    actor_user_id: Optional[int] = None,
    actor_name: Optional[str] = None,
) -> AttachmentResult:
    """
    Attach one file reference to a homework entry.

    The count check and the insert share one session, and the unique constraint
    on ``(homework_id, file_unique_id)`` is the real backstop: two near-
    simultaneous sends of the same file can both pass the check, and the loser's
    IntegrityError is reported as ``duplicate`` instead of surfacing as an error.
    """
    from services.audit import now_iso
    from utils import MAX_ATTACHMENTS_PER_HOMEWORK

    async with AsyncSessionLocal() as session:
        owner = (await session.execute(
            select(Homework.id)
            .where(Homework.chat_id == chat_id)
            .where(Homework.id == homework_id)
        )).scalar_one_or_none()
        if owner is None:
            return AttachmentResult("missing")

        existing = int((await session.execute(
            select(func.count())
            .select_from(HomeworkAttachment)
            .where(HomeworkAttachment.homework_id == homework_id)
        )).scalar_one() or 0)
        if existing >= MAX_ATTACHMENTS_PER_HOMEWORK:
            return AttachmentResult("limit")

        attachment = HomeworkAttachment(
            homework_id=homework_id,
            file_id=file_id,
            file_unique_id=file_unique_id,
            file_type=file_type,
            file_name=file_name,
            file_size=file_size,
            caption=caption,
            created_at=now_iso(),
            created_by_user_id=actor_user_id,
            created_by_name=actor_name,
        )
        session.add(attachment)
        try:
            await session.commit()
        except IntegrityError:
            await session.rollback()
            return AttachmentResult("duplicate")
        await session.refresh(attachment)
        return AttachmentResult("ok", attachment)


async def get_homework_attachment(
    chat_id: int, attachment_id: int
) -> Optional[HomeworkAttachment]:
    """One attachment by id, scoped to ``chat_id`` via its parent homework."""
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(HomeworkAttachment)
            .join(Homework, Homework.id == HomeworkAttachment.homework_id)
            .where(Homework.chat_id == chat_id)
            .where(HomeworkAttachment.id == attachment_id)
        )
        return result.scalar_one_or_none()


async def delete_homework_attachment(chat_id: int, attachment_id: int) -> bool:
    """
    Remove one attachment. Scoped to ``chat_id`` through its parent homework, so
    a stale or forged id from another chat is a no-op returning False.
    """
    async with AsyncSessionLocal() as session:
        owned = (await session.execute(
            select(HomeworkAttachment.id)
            .join(Homework, Homework.id == HomeworkAttachment.homework_id)
            .where(Homework.chat_id == chat_id)
            .where(HomeworkAttachment.id == attachment_id)
        )).scalar_one_or_none()
        if owned is None:
            return False
        result = await session.execute(
            delete(HomeworkAttachment).where(HomeworkAttachment.id == attachment_id)
        )
        await session.commit()
        return result.rowcount > 0


async def get_attachment_counts(chat_id: int) -> Dict[int, int]:
    """
    ``{homework_id: attachment_count}`` for one chat in a single query, so the
    homework list can show a 📎 marker without an N+1 per entry.
    """
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(HomeworkAttachment.homework_id, func.count())
            .join(Homework, Homework.id == HomeworkAttachment.homework_id)
            .where(Homework.chat_id == chat_id)
            .group_by(HomeworkAttachment.homework_id)
        )
        return {row[0]: int(row[1]) for row in result.all()}


# --- Extra activities (clubs / tutors / sections — NOT school lessons) ------

async def add_extra_activity(
    chat_id: int,
    title: str,
    kind: str,
    start_time: str,
    day_of_week: Optional[int] = None,
    activity_date: Optional[datetime.date] = None,
    end_time: Optional[str] = None,
    location: Optional[str] = None,
    note: Optional[str] = None,
    actor_user_id: Optional[int] = None,
    actor_name: Optional[str] = None,
) -> ExtraActivity:
    async with AsyncSessionLocal() as session:
        activity = ExtraActivity(
            chat_id=chat_id,
            title=title.strip(),
            kind=kind,
            day_of_week=day_of_week,
            activity_date=activity_date,
            start_time=start_time,
            end_time=end_time,
            location=location.strip() if location else None,
            note=note.strip() if note else None,
            **_authorship_on_create(actor_user_id, actor_name),
        )
        session.add(activity)
        await session.commit()
        await session.refresh(activity)
        return activity


async def get_extra_activities(chat_id: int) -> List[ExtraActivity]:
    """All extra activities for a chat, ordered by start time. Scoped to chat_id."""
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(ExtraActivity)
            .where(ExtraActivity.chat_id == chat_id)
            .order_by(ExtraActivity.start_time)
        )
        return list(result.scalars().all())


async def get_extra_activity_by_id(chat_id: int, activity_id: int) -> Optional[ExtraActivity]:
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(ExtraActivity)
            .where(ExtraActivity.chat_id == chat_id)
            .where(ExtraActivity.id == activity_id)
        )
        return result.scalar_one_or_none()


async def update_extra_activity(
    chat_id: int,
    activity_id: int,
    *,
    actor_user_id: Optional[int] = None,
    actor_name: Optional[str] = None,
    **values,
) -> bool:
    """
    Updates one or more fields of an extra activity, always scoped to both
    chat_id and activity_id. Returns False (no-op) if the activity does not
    belong to this chat (stale button / already-deleted / foreign chat).
    """
    if not values:
        return False
    values.update(_authorship_on_update(actor_user_id, actor_name))
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            update(ExtraActivity)
            .where(ExtraActivity.chat_id == chat_id)
            .where(ExtraActivity.id == activity_id)
            .values(**values)
        )
        await session.commit()
        return result.rowcount > 0


async def delete_extra_activity(chat_id: int, activity_id: int) -> bool:
    """Returns False (no-op) if the activity doesn't exist for this chat."""
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            delete(ExtraActivity)
            .where(ExtraActivity.chat_id == chat_id)
            .where(ExtraActivity.id == activity_id)
        )
        await session.commit()
        return result.rowcount > 0


async def get_extra_activities_for_chats(chat_ids: List[int]) -> Dict[int, List[ExtraActivity]]:
    """Batched fetch of extra activities for many chats (scheduler sweep)."""
    if not chat_ids:
        return {}
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(ExtraActivity)
            .where(ExtraActivity.chat_id.in_(chat_ids))
            .order_by(ExtraActivity.start_time)
        )
        grouped: Dict[int, List[ExtraActivity]] = defaultdict(list)
        for activity in result.scalars().all():
            grouped[activity.chat_id].append(activity)
        return grouped


async def set_extra_activity_reminder(
    chat_id: int,
    activity_id: int,
    enabled: Optional[bool] = None,
    minutes: Optional[int] = None,
    actor_user_id: Optional[int] = None,
    actor_name: Optional[str] = None,
) -> bool:
    """
    Update an extra activity's per-activity reminder config, scoped to chat_id.
    ``minutes`` is clamped to the allowed 0..10080 range. Returns False if the
    activity doesn't belong to this chat.
    """
    values: dict = {}
    if enabled is not None:
        values["reminder_enabled"] = enabled
    if minutes is not None:
        values["reminder_minutes"] = max(0, min(10080, minutes))
    if not values:
        return False
    values.update(_authorship_on_update(actor_user_id, actor_name))
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            update(ExtraActivity)
            .where(ExtraActivity.chat_id == chat_id)
            .where(ExtraActivity.id == activity_id)
            .values(**values)
        )
        await session.commit()
        return result.rowcount > 0


# --- Date overrides (per-date changes overlaying the weekly template) -------

async def get_day_override(chat_id: int, date: datetime.date) -> Optional[DayOverride]:
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(DayOverride)
            .where(DayOverride.chat_id == chat_id)
            .where(DayOverride.date == date)
        )
        return result.scalar_one_or_none()


async def set_day_override(
    chat_id: int,
    date: datetime.date,
    day_type: str,
    note: Optional[str] = None,
    actor_user_id: Optional[int] = None,
    actor_name: Optional[str] = None,
) -> DayOverride:
    """Upserts the whole-day setting for ``(chat_id, date)``. Scoped to chat_id."""
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(DayOverride)
            .where(DayOverride.chat_id == chat_id)
            .where(DayOverride.date == date)
        )
        row = result.scalar_one_or_none()
        if row is None:
            row = DayOverride(
                chat_id=chat_id, date=date, day_type=day_type, note=note,
                **_authorship_on_create(actor_user_id, actor_name),
            )
            session.add(row)
        else:
            row.day_type = day_type
            row.note = note
            for column, value in _authorship_on_update(actor_user_id, actor_name).items():
                setattr(row, column, value)
        await session.commit()
        await session.refresh(row)
        return row


async def clear_day_override(chat_id: int, date: datetime.date) -> bool:
    """Removes the whole-day setting (if any). Returns False if there was none."""
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            delete(DayOverride)
            .where(DayOverride.chat_id == chat_id)
            .where(DayOverride.date == date)
        )
        await session.commit()
        return result.rowcount > 0


async def get_lesson_overrides(chat_id: int, date: datetime.date) -> List[LessonOverride]:
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(LessonOverride)
            .where(LessonOverride.chat_id == chat_id)
            .where(LessonOverride.date == date)
            .order_by(LessonOverride.lesson_number)
        )
        return list(result.scalars().all())


async def set_lesson_override(
    chat_id: int,
    date: datetime.date,
    lesson_number: int,
    action: str,
    subject_name: Optional[str] = None,
    start_time: Optional[str] = None,
    end_time: Optional[str] = None,
    note: Optional[str] = None,
    actor_user_id: Optional[int] = None,
    actor_name: Optional[str] = None,
) -> LessonOverride:
    """
    Upserts the per-lesson change for ``(chat_id, date, lesson_number)``.
    Always scoped to chat_id so one chat can never touch another's data.
    """
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(LessonOverride)
            .where(LessonOverride.chat_id == chat_id)
            .where(LessonOverride.date == date)
            .where(LessonOverride.lesson_number == lesson_number)
        )
        row = result.scalar_one_or_none()
        if row is None:
            row = LessonOverride(
                chat_id=chat_id, date=date, lesson_number=lesson_number,
                action=action, subject_name=subject_name,
                start_time=start_time, end_time=end_time, note=note,
                **_authorship_on_create(actor_user_id, actor_name),
            )
            session.add(row)
        else:
            row.action = action
            row.subject_name = subject_name
            row.start_time = start_time
            row.end_time = end_time
            row.note = note
            for column, value in _authorship_on_update(actor_user_id, actor_name).items():
                setattr(row, column, value)
        await session.commit()
        await session.refresh(row)
        return row


async def delete_lesson_override(chat_id: int, date: datetime.date, lesson_number: int) -> bool:
    """Returns False (no-op) if there was no such override for this chat."""
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            delete(LessonOverride)
            .where(LessonOverride.chat_id == chat_id)
            .where(LessonOverride.date == date)
            .where(LessonOverride.lesson_number == lesson_number)
        )
        await session.commit()
        return result.rowcount > 0


async def clear_date_overrides(chat_id: int, date: datetime.date) -> bool:
    """
    Removes *all* overrides (the whole-day setting and every per-lesson change)
    for one date in a single transaction. Returns True if anything was removed.
    Extra activities are deliberately NOT touched here.
    """
    async with AsyncSessionLocal() as session:
        r1 = await session.execute(
            delete(LessonOverride)
            .where(LessonOverride.chat_id == chat_id)
            .where(LessonOverride.date == date)
        )
        r2 = await session.execute(
            delete(DayOverride)
            .where(DayOverride.chat_id == chat_id)
            .where(DayOverride.date == date)
        )
        await session.commit()
        return (r1.rowcount + r2.rowcount) > 0


async def get_override_dates(chat_id: int, since: Optional[datetime.date] = None) -> List[datetime.date]:
    """
    All distinct dates that carry any override (day-level or lesson-level) for
    this chat, sorted ascending. When ``since`` is given, only dates on/after it
    are returned (used to list upcoming changes).
    """
    async with AsyncSessionLocal() as session:
        day_q = select(DayOverride.date).where(DayOverride.chat_id == chat_id)
        lesson_q = select(LessonOverride.date).where(LessonOverride.chat_id == chat_id)
        if since is not None:
            day_q = day_q.where(DayOverride.date >= since)
            lesson_q = lesson_q.where(LessonOverride.date >= since)
        day_dates = (await session.execute(day_q)).scalars().all()
        lesson_dates = (await session.execute(lesson_q)).scalars().all()
        return sorted(set(day_dates) | set(lesson_dates))


async def get_day_overrides_for_chats(
    chat_ids: List[int], date: datetime.date
) -> Dict[int, DayOverride]:
    """Batched whole-day settings for many chats on one date (scheduler sweep)."""
    if not chat_ids:
        return {}
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(DayOverride)
            .where(DayOverride.chat_id.in_(chat_ids))
            .where(DayOverride.date == date)
        )
        return {row.chat_id: row for row in result.scalars().all()}


async def get_lesson_overrides_for_chats(
    chat_ids: List[int], date: datetime.date
) -> Dict[int, List[LessonOverride]]:
    """Batched per-lesson changes for many chats on one date (scheduler sweep)."""
    if not chat_ids:
        return {}
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(LessonOverride)
            .where(LessonOverride.chat_id.in_(chat_ids))
            .where(LessonOverride.date == date)
            .order_by(LessonOverride.lesson_number)
        )
        grouped: Dict[int, List[LessonOverride]] = defaultdict(list)
        for row in result.scalars().all():
            grouped[row.chat_id].append(row)
        return grouped


async def update_chat_reminder_times(chat_id: int, hw_time: Optional[str] = None, schedule_time: Optional[str] = None):
    async with AsyncSessionLocal() as session:
        values = {}
        if hw_time is not None:
            values["hw_reminder_time"] = hw_time
        if schedule_time is not None:
            values["schedule_reminder_time"] = schedule_time
        if values:
            await session.execute(
                update(Chat).where(Chat.chat_id == chat_id).values(**values)
            )
            await session.commit()

async def update_chat_reminder_flags(chat_id: int, hw_enabled: Optional[bool] = None, schedule_enabled: Optional[bool] = None):
    async with AsyncSessionLocal() as session:
        values = {}
        if hw_enabled is not None:
            values["hw_reminder_enabled"] = hw_enabled
        if schedule_enabled is not None:
            values["schedule_reminder_enabled"] = schedule_enabled
        if values:
            await session.execute(
                update(Chat).where(Chat.chat_id == chat_id).values(**values)
            )
            await session.commit()


# Column names of the extra reminder-category toggles, so the settings handler
# can flip any of them by key without a bespoke function each.
REMINDER_CATEGORY_FLAGS = {
    "hw": "hw_reminder_enabled",
    "sched": "schedule_reminder_enabled",
    "duetoday": "hw_duetoday_enabled",
    "changes": "changes_reminder_enabled",
    "extra": "extra_reminder_enabled",
}


async def set_reminder_category_enabled(chat_id: int, category: str, enabled: bool) -> bool:
    """Enable/disable one reminder category by key (see REMINDER_CATEGORY_FLAGS)."""
    column = REMINDER_CATEGORY_FLAGS.get(category)
    if column is None:
        return False
    async with AsyncSessionLocal() as session:
        await session.execute(
            update(Chat).where(Chat.chat_id == chat_id).values(**{column: enabled})
        )
        await session.commit()
        return True


async def update_duetoday_time(chat_id: int, hhmm: str):
    async with AsyncSessionLocal() as session:
        await session.execute(
            update(Chat).where(Chat.chat_id == chat_id).values(hw_duetoday_time=hhmm)
        )
        await session.commit()


async def set_quiet_hours(chat_id: int, quiet_start: Optional[str], quiet_end: Optional[str]):
    """Set (or, with both None, clear) the chat's quiet hours."""
    async with AsyncSessionLocal() as session:
        await session.execute(
            update(Chat).where(Chat.chat_id == chat_id).values(
                quiet_start=quiet_start, quiet_end=quiet_end
            )
        )
        await session.commit()


async def update_last_duetoday_reminder_date(chat_id: int, date: datetime.date):
    async with AsyncSessionLocal() as session:
        await session.execute(
            update(Chat).where(Chat.chat_id == chat_id).values(last_duetoday_reminder_date=date)
        )
        await session.commit()


async def update_last_changes_reminder_date(chat_id: int, date: datetime.date):
    async with AsyncSessionLocal() as session:
        await session.execute(
            update(Chat).where(Chat.chat_id == chat_id).values(last_changes_reminder_date=date)
        )
        await session.commit()

# --- Homework edit policy ---------------------------------------------------

async def set_hw_edit_policy(chat_id: int, policy: str) -> bool:
    """
    Set who may edit homework in this chat. Unknown values are rejected rather
    than written, so a stale/tampered callback can't put the chat into a state
    the permission service doesn't understand.
    """
    from services.permissions import HW_EDIT_POLICIES
    if policy not in HW_EDIT_POLICIES:
        return False
    async with AsyncSessionLocal() as session:
        await session.execute(
            update(Chat).where(Chat.chat_id == chat_id).values(hw_edit_policy=policy)
        )
        await session.commit()
        return True


# --- Per-chat timezone ------------------------------------------------------

async def set_chat_timezone(chat_id: int, tz_name: str) -> bool:
    """
    Set this chat's IANA timezone. Rejects anything pytz doesn't know rather
    than writing it, so a stale or hand-crafted callback can't leave the chat on
    a zone the scheduler would have to fall back from every tick.
    """
    from services.timeservice import normalize_timezone
    canonical = normalize_timezone(tz_name)
    if canonical is None:
        return False
    async with AsyncSessionLocal() as session:
        await session.execute(
            update(Chat).where(Chat.chat_id == chat_id).values(timezone=canonical)
        )
        await session.commit()
        return True


# --- Audit log --------------------------------------------------------------

async def add_audit_log(
    chat_id: int,
    entity_type: str,
    action: str,
    created_at: str,
    actor_user_id: Optional[int] = None,
    actor_name: Optional[str] = None,
    entity_id: Optional[int] = None,
    summary: Optional[str] = None,
) -> None:
    """
    Append one journal row. Callers go through services.audit.record, which
    validates ``entity_type``/``action`` and never lets a failure here break the
    user action being journalled.
    """
    async with AsyncSessionLocal() as session:
        session.add(AuditLog(
            chat_id=chat_id, entity_type=entity_type, action=action,
            actor_user_id=actor_user_id, actor_name=actor_name,
            entity_id=entity_id, summary=summary, created_at=created_at,
        ))
        await session.commit()


async def get_audit_logs(
    chat_id: int,
    entity_type: Optional[str] = None,
    limit: int = 10,
    offset: int = 0,
) -> List[AuditLog]:
    """
    One page of history for a chat, newest first, always scoped to ``chat_id``
    so no chat can ever read another chat's journal. ``entity_type`` filters to
    one kind of change.
    """
    async with AsyncSessionLocal() as session:
        query = select(AuditLog).where(AuditLog.chat_id == chat_id)
        if entity_type is not None:
            query = query.where(AuditLog.entity_type == entity_type)
        query = query.order_by(AuditLog.id.desc()).limit(limit).offset(offset)
        result = await session.execute(query)
        return list(result.scalars().all())


async def count_audit_logs(chat_id: int, entity_type: Optional[str] = None) -> int:
    """Total journal rows for a chat (optionally one entity type) — for paging."""
    async with AsyncSessionLocal() as session:
        query = select(func.count()).select_from(AuditLog).where(AuditLog.chat_id == chat_id)
        if entity_type is not None:
            query = query.where(AuditLog.entity_type == entity_type)
        result = await session.execute(query)
        return int(result.scalar_one() or 0)


async def cleanup_old_audit_logs(before_iso: str) -> int:
    """
    Delete journal rows whose ``created_at`` is strictly before ``before_iso``
    (an ISO-8601 UTC timestamp) so history doesn't grow without bound. Returns
    the number of rows removed. Timestamps are stored in a fixed ISO UTC format,
    so a string comparison is a chronological one.
    """
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            delete(AuditLog).where(AuditLog.created_at < before_iso)
        )
        await session.commit()
        return result.rowcount


# --- Bulk reads for export / backup ----------------------------------------
#
# The interactive screens deliberately read narrow slices (one day, one page).
# A backup needs *everything* for one chat, so these read the whole table for a
# single chat_id in one query each — still always scoped by chat_id.

async def get_all_schedule(chat_id: int) -> List[Schedule]:
    """Every weekly-template row of a chat, all week types ('all', 'A', 'B')."""
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(Schedule)
            .where(Schedule.chat_id == chat_id)
            .order_by(Schedule.week_type, Schedule.day_of_week, Schedule.lesson_number)
        )
        return list(result.scalars().all())


async def get_all_day_overrides(chat_id: int) -> List[DayOverride]:
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(DayOverride)
            .where(DayOverride.chat_id == chat_id)
            .order_by(DayOverride.date)
        )
        return list(result.scalars().all())


async def get_all_lesson_overrides(chat_id: int) -> List[LessonOverride]:
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(LessonOverride)
            .where(LessonOverride.chat_id == chat_id)
            .order_by(LessonOverride.date, LessonOverride.lesson_number)
        )
        return list(result.scalars().all())


async def get_all_homework_attachments(chat_id: int) -> Dict[int, List[HomeworkAttachment]]:
    """
    ``{homework_id: [attachment, ...]}`` for a whole chat in one query (joined on
    ``homework`` so the chat scoping still holds), avoiding an N+1 during export.
    """
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(HomeworkAttachment)
            .join(Homework, Homework.id == HomeworkAttachment.homework_id)
            .where(Homework.chat_id == chat_id)
            .order_by(HomeworkAttachment.id)
        )
        grouped: Dict[int, List[HomeworkAttachment]] = defaultdict(list)
        for row in result.scalars().all():
            grouped[row.homework_id].append(row)
        return grouped


async def get_all_audit_logs(chat_id: int, limit: int) -> List[AuditLog]:
    """Newest-first journal rows for a chat, hard-capped by ``limit``."""
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(AuditLog)
            .where(AuditLog.chat_id == chat_id)
            .order_by(AuditLog.id.desc())
            .limit(limit)
        )
        return list(result.scalars().all())


# --- Import (one transaction, all-or-nothing) -------------------------------

# Chat columns a backup may restore. Deliberately an allow-list: identity
# (chat_id / chat_type), delivery bookkeeping (is_blocked, last_*_reminder_date)
# and anything else are NOT importable, so a file can never change which chat it
# lands in or replay yesterday's reminders.
IMPORTABLE_CHAT_COLUMNS = (
    "hw_reminder_time", "schedule_reminder_time",
    "hw_reminder_enabled", "schedule_reminder_enabled",
    "hw_duetoday_enabled", "hw_duetoday_time",
    "changes_reminder_enabled", "extra_reminder_enabled",
    "quiet_start", "quiet_end",
    "hw_edit_policy", "timezone",
    "week_mode", "week_anchor_monday",
)


async def import_chat_data(
    chat_id: int, payload: dict, mode: str, chat_type: str = "private"
) -> Dict[str, int]:
    """
    Restore an already-validated backup ``payload`` into ``chat_id``.

    **One transaction.** Everything below happens in a single session and is
    committed once at the end; any error rolls the whole thing back, so a failed
    import can never leave a chat half-restored.

    ``chat_id`` is the *caller's* chat — the id inside the file is ignored by the
    validator and never reaches here, so a backup taken in one chat cannot write
    into another. Every statement is additionally filtered by ``chat_id``.

    Modes:
      * ``merge``   — keyed rows (lesson slots, template cells, per-date
        overrides) are updated in place or inserted; homework and extra
        activities are matched by content and skipped when an identical row
        already exists;
      * ``replace`` — all of this chat's schedule/homework/extra/override data is
        deleted first, then the file is inserted verbatim.

    Returns a counter dict (``created`` / ``updated`` / ``skipped`` / ``deleted``
    per collection) for the report shown to the user.
    """
    if mode not in ("merge", "replace"):
        raise ValueError(f"Unknown import mode: {mode!r}")

    counters: Dict[str, int] = defaultdict(int)

    def bump(key: str, n: int = 1):
        counters[key] += n

    async with AsyncSessionLocal() as session:
        try:
            chat = (await session.execute(
                select(Chat).where(Chat.chat_id == chat_id)
            )).scalar_one_or_none()
            if chat is None:
                # A brand-new chat row is created only for the chat we are
                # importing into; chat_type comes from the live Telegram chat,
                # never from the file.
                chat = Chat(chat_id=chat_id, chat_type=chat_type)
                session.add(chat)
                await session.flush()

            # --- Chat-level settings (allow-listed columns only) ---
            settings = payload.get("chat") or {}
            applied = 0
            for column in IMPORTABLE_CHAT_COLUMNS:
                if column in settings:
                    setattr(chat, column, settings[column])
                    applied += 1
            if applied:
                bump("settings_updated")

            if mode == "replace":
                for model in (Schedule, LessonSlot, ExtraActivity, DayOverride, LessonOverride):
                    result = await session.execute(
                        delete(model).where(model.chat_id == chat_id)
                    )
                    bump("deleted", result.rowcount or 0)
                # Attachments go with their homework via ON DELETE CASCADE.
                result = await session.execute(
                    delete(Homework).where(Homework.chat_id == chat_id)
                )
                bump("deleted", result.rowcount or 0)
                await session.flush()

            # --- Lesson slots (keyed by lesson_number) ---
            existing_slots = {}
            if mode == "merge":
                existing_slots = {
                    row.lesson_number: row for row in (await session.execute(
                        select(LessonSlot).where(LessonSlot.chat_id == chat_id)
                    )).scalars().all()
                }
            for item in payload.get("lesson_slots", []):
                row = existing_slots.get(item["lesson_number"])
                if row is None:
                    session.add(LessonSlot(chat_id=chat_id, **item))
                    bump("slots_created")
                else:
                    row.start_time = item["start_time"]
                    row.end_time = item["end_time"]
                    bump("slots_updated")

            # --- Weekly template (keyed by week_type + day + lesson) ---
            existing_cells = {}
            if mode == "merge":
                existing_cells = {
                    (row.week_type, row.day_of_week, row.lesson_number): row
                    for row in (await session.execute(
                        select(Schedule).where(Schedule.chat_id == chat_id)
                    )).scalars().all()
                }
            for item in payload.get("schedule", []):
                key = (item["week_type"], item["day_of_week"], item["lesson_number"])
                row = existing_cells.get(key)
                if row is None:
                    session.add(Schedule(chat_id=chat_id, **item))
                    bump("schedule_created")
                else:
                    row.subject_name = item["subject_name"]
                    bump("schedule_updated")

            # --- Whole-day overrides (keyed by date) ---
            existing_days = {}
            if mode == "merge":
                existing_days = {
                    row.date: row for row in (await session.execute(
                        select(DayOverride).where(DayOverride.chat_id == chat_id)
                    )).scalars().all()
                }
            for item in payload.get("day_overrides", []):
                row = existing_days.get(item["date"])
                if row is None:
                    session.add(DayOverride(chat_id=chat_id, **item))
                    bump("day_overrides_created")
                else:
                    for column, value in item.items():
                        setattr(row, column, value)
                    bump("day_overrides_updated")

            # --- Per-lesson overrides (keyed by date + lesson_number) ---
            existing_lesson_ov = {}
            if mode == "merge":
                existing_lesson_ov = {
                    (row.date, row.lesson_number): row
                    for row in (await session.execute(
                        select(LessonOverride).where(LessonOverride.chat_id == chat_id)
                    )).scalars().all()
                }
            for item in payload.get("lesson_overrides", []):
                row = existing_lesson_ov.get((item["date"], item["lesson_number"]))
                if row is None:
                    session.add(LessonOverride(chat_id=chat_id, **item))
                    bump("lesson_overrides_created")
                else:
                    for column, value in item.items():
                        setattr(row, column, value)
                    bump("lesson_overrides_updated")

            # --- Extra activities (no natural key → matched by content) ---
            existing_extra = set()
            if mode == "merge":
                existing_extra = {
                    (row.title, row.kind, row.day_of_week, row.activity_date, row.start_time)
                    for row in (await session.execute(
                        select(ExtraActivity).where(ExtraActivity.chat_id == chat_id)
                    )).scalars().all()
                }
            for item in payload.get("extra_activities", []):
                key = (
                    item["title"], item["kind"], item.get("day_of_week"),
                    item.get("activity_date"), item["start_time"],
                )
                if key in existing_extra:
                    bump("extra_skipped")
                    continue
                existing_extra.add(key)
                session.add(ExtraActivity(chat_id=chat_id, **item))
                bump("extra_created")

            # --- Homework + its attachments (matched by content) ---
            existing_hw = set()
            if mode == "merge":
                existing_hw = {
                    (row.subject_name, row.due_date, row.description)
                    for row in (await session.execute(
                        select(Homework).where(Homework.chat_id == chat_id)
                    )).scalars().all()
                }
            for item in payload.get("homework", []):
                # Never mutate the caller's payload: the same dict is used for
                # the dry-run preview and then for the real import.
                attachments = item.get("attachments") or []
                item = {k: v for k, v in item.items() if k != "attachments"}
                key = (item["subject_name"], item["due_date"], item["description"])
                if key in existing_hw:
                    bump("homework_skipped")
                    bump("attachments_skipped", len(attachments))
                    continue
                existing_hw.add(key)
                hw = Homework(chat_id=chat_id, **item)
                session.add(hw)
                await session.flush()  # need hw.id for the attachment rows
                bump("homework_created")
                seen_files = set()
                for attachment in attachments:
                    # The unique constraint is (homework_id, file_unique_id);
                    # a file duplicated inside the file itself is dropped here
                    # rather than aborting the whole transaction.
                    if attachment["file_unique_id"] in seen_files:
                        bump("attachments_skipped")
                        continue
                    seen_files.add(attachment["file_unique_id"])
                    session.add(HomeworkAttachment(homework_id=hw.id, **attachment))
                    bump("attachments_created")

            await session.commit()
        except Exception:
            await session.rollback()
            raise

    return dict(counters)


async def get_all_chats(include_blocked: bool = False) -> List[Chat]:
    async with AsyncSessionLocal() as session:
        query = select(Chat)
        if not include_blocked:
            query = query.where(Chat.is_blocked == False)  # noqa: E712
        result = await session.execute(query)
        return list(result.scalars().all())

async def get_incomplete_homework_for_chats(chat_ids: List[int]) -> Dict[int, List[Homework]]:
    """
    Fetches all not-yet-completed homework for every chat in ``chat_ids`` in a
    single query, grouped by chat_id. Used by the scheduler sweep so it issues
    one query for N chats instead of one query per chat.
    """
    if not chat_ids:
        return {}
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(Homework)
            .where(Homework.chat_id.in_(chat_ids))
            .where(Homework.is_completed == False)  # noqa: E712
            .order_by(Homework.due_date)
        )
        grouped: Dict[int, List[Homework]] = defaultdict(list)
        for hw in result.scalars().all():
            grouped[hw.chat_id].append(hw)
        return grouped

async def get_schedule_for_chats(chat_ids: List[int], day_of_week: int) -> Dict[int, List[Schedule]]:
    """
    Batched schedule for one day-of-week across many chats. Returns rows of
    *all* week types (the caller picks the right ``week_type`` per chat based
    on that chat's alternating-week settings), so the sweep still issues one
    query for N chats.
    """
    if not chat_ids:
        return {}
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(Schedule)
            .where(Schedule.chat_id.in_(chat_ids))
            .where(Schedule.day_of_week == day_of_week)
            .order_by(Schedule.lesson_number)
        )
        grouped: Dict[int, List[Schedule]] = defaultdict(list)
        for item in result.scalars().all():
            grouped[item.chat_id].append(item)
        return grouped

async def get_lesson_slots_for_chats(chat_ids: List[int]) -> Dict[int, List[LessonSlot]]:
    """Same batching idea as :func:`get_incomplete_homework_for_chats`, for lesson slots across many chats."""
    if not chat_ids:
        return {}
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(LessonSlot)
            .where(LessonSlot.chat_id.in_(chat_ids))
            .order_by(LessonSlot.lesson_number)
        )
        grouped: Dict[int, List[LessonSlot]] = defaultdict(list)
        for slot in result.scalars().all():
            grouped[slot.chat_id].append(slot)
        return grouped

async def get_homework_due_on(chat_id: int, due_date: datetime.date) -> List[Homework]:
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(Homework)
            .where(Homework.chat_id == chat_id)
            .where(Homework.due_date == due_date)
            .where(Homework.is_completed == False)  # noqa: E712
        )
        return list(result.scalars().all())

async def get_overdue_homework(chat_id: int, today: datetime.date) -> List[Homework]:
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(Homework)
            .where(Homework.chat_id == chat_id)
            .where(Homework.due_date < today)
            .where(Homework.is_completed == False)  # noqa: E712
            .order_by(Homework.due_date)
        )
        return list(result.scalars().all())

async def delete_chat(chat_id: int):
    async with AsyncSessionLocal() as session:
        await session.execute(delete(Chat).where(Chat.chat_id == chat_id))
        await session.commit()

async def migrate_chat(old_chat_id: int, new_chat_id: int) -> bool:
    """
    Moves all data from ``old_chat_id`` to ``new_chat_id`` in one transaction —
    used when Telegram upgrades a basic group to a supergroup (new chat_id).
    Children are re-pointed to the new id *before* the old Chat row is
    deleted, so the ON DELETE CASCADE never fires against them. Returns False
    if there is nothing to migrate (old chat unknown) or the new id is already
    a distinct existing chat (ambiguous — left untouched).
    """
    if old_chat_id == new_chat_id:
        return False

    async with AsyncSessionLocal() as session:
        result = await session.execute(select(Chat).where(Chat.chat_id == old_chat_id))
        old_chat = result.scalar_one_or_none()
        if old_chat is None:
            return False

        result = await session.execute(select(Chat).where(Chat.chat_id == new_chat_id))
        if result.scalar_one_or_none() is not None:
            return False

        new_chat = Chat(
            chat_id=new_chat_id,
            chat_type=old_chat.chat_type,
            hw_reminder_time=old_chat.hw_reminder_time,
            schedule_reminder_time=old_chat.schedule_reminder_time,
            is_onboarded=old_chat.is_onboarded,
            last_hw_reminder_date=old_chat.last_hw_reminder_date,
            last_sch_reminder_date=old_chat.last_sch_reminder_date,
            hw_reminder_enabled=old_chat.hw_reminder_enabled,
            schedule_reminder_enabled=old_chat.schedule_reminder_enabled,
            is_blocked=old_chat.is_blocked,
            week_mode=old_chat.week_mode,
            week_anchor_monday=old_chat.week_anchor_monday,
            hw_duetoday_enabled=old_chat.hw_duetoday_enabled,
            hw_duetoday_time=old_chat.hw_duetoday_time,
            changes_reminder_enabled=old_chat.changes_reminder_enabled,
            extra_reminder_enabled=old_chat.extra_reminder_enabled,
            last_duetoday_reminder_date=old_chat.last_duetoday_reminder_date,
            last_changes_reminder_date=old_chat.last_changes_reminder_date,
            quiet_start=old_chat.quiet_start,
            quiet_end=old_chat.quiet_end,
            hw_edit_policy=old_chat.hw_edit_policy,
            timezone=old_chat.timezone,
        )
        session.add(new_chat)
        await session.flush()

        # AuditLog moves too: the chat's history (and the authorship it records)
        # must survive the group → supergroup upgrade, not be cascade-deleted
        # along with the old Chat row.
        for model in (
            LessonSlot, Schedule, Homework, ExtraActivity, DayOverride,
            LessonOverride, AuditLog,
        ):
            await session.execute(
                update(model).where(model.chat_id == old_chat_id).values(chat_id=new_chat_id)
            )

        await session.execute(delete(Chat).where(Chat.chat_id == old_chat_id))
        await session.commit()
        return True

async def update_last_hw_reminder_date(chat_id: int, date: datetime.date):
    async with AsyncSessionLocal() as session:
        await session.execute(
            update(Chat).where(Chat.chat_id == chat_id).values(last_hw_reminder_date=date)
        )
        await session.commit()

async def update_last_sch_reminder_date(chat_id: int, date: datetime.date):
    async with AsyncSessionLocal() as session:
        await session.execute(
            update(Chat).where(Chat.chat_id == chat_id).values(last_sch_reminder_date=date)
        )
        await session.commit()

async def set_chat_blocked(chat_id: int, blocked: bool = True):
    async with AsyncSessionLocal() as session:
        await session.execute(
            update(Chat).where(Chat.chat_id == chat_id).values(is_blocked=blocked)
        )
        await session.commit()

# --- Reminder outbox (idempotent multi-chunk delivery) ---------------------

# A job claimed ("in_progress") but not touched for this long is assumed to
# belong to a crashed run and may be safely reclaimed/resumed by another tick.
REMINDER_JOB_STALE_SECONDS = 600


class ReminderClaim:
    """
    Outcome of trying to claim an outbox job:

      * ``claimed`` — we now exclusively own it; ``job`` is set → send it;
      * ``done``    — already fully delivered by an earlier attempt → nothing
        to send, safe to treat as delivered;
      * ``busy``    — another running instance holds it right now (freshly
        ``in_progress``) → skip; this is NOT a delivery, so the caller must not
        record it as sent.
    """
    __slots__ = ("status", "job")

    def __init__(self, status: str, job: Optional[ReminderJob] = None):
        self.status = status
        self.job = job


def _staleness_seconds(now_iso: str, updated_at: str) -> Optional[float]:
    try:
        now_dt = datetime.datetime.fromisoformat(now_iso)
        claimed = datetime.datetime.fromisoformat(updated_at)
    except (ValueError, TypeError):
        return None
    return (now_dt.astimezone(datetime.timezone.utc) - claimed.astimezone(datetime.timezone.utc)).total_seconds()


async def claim_reminder_job(
    chat_id: int, kind: str, job_date: datetime.date, chunks: List[str], now_iso: str
) -> ReminderClaim:
    """
    Create (if needed) and atomically claim the ReminderJob for
    ``(chat_id, kind, job_date)``. See :class:`ReminderClaim` for the outcomes.

    The claim is a compare-and-swap: the UPDATE matches on the *exact* status
    (and, when reclaiming a stale in-progress job, its ``updated_at``) observed
    a moment earlier, so two bot instances racing on the same job can never both
    win — exactly one UPDATE affects a row, the other sees ``rowcount == 0`` and
    is told ``busy``. Partly-sent jobs keep their ``chunks_sent`` so delivery
    resumes where it left off after a crash/restart.
    """
    import json

    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(ReminderJob)
            .where(ReminderJob.chat_id == chat_id)
            .where(ReminderJob.kind == kind)
            .where(ReminderJob.job_date == job_date)
        )
        job = result.scalar_one_or_none()

        if job is None:
            job = ReminderJob(
                chat_id=chat_id, kind=kind, job_date=job_date,
                chunks_json=json.dumps(chunks), chunks_total=len(chunks),
                chunks_sent=0, status="pending", updated_at=now_iso,
            )
            session.add(job)
            try:
                await session.commit()
            except IntegrityError:
                # Another instance/tick inserted it first; re-fetch and continue.
                await session.rollback()
                result = await session.execute(
                    select(ReminderJob)
                    .where(ReminderJob.chat_id == chat_id)
                    .where(ReminderJob.kind == kind)
                    .where(ReminderJob.job_date == job_date)
                )
                job = result.scalar_one_or_none()
                if job is None:
                    return ReminderClaim("busy")
            else:
                await session.refresh(job)

        if job.status == "done":
            return ReminderClaim("done")

        observed_status = job.status
        observed_updated = job.updated_at

        # Build the compare-and-swap predicate matching exactly what we saw.
        cas = (
            update(ReminderJob)
            .where(ReminderJob.id == job.id)
            .where(ReminderJob.status == observed_status)
        )
        if observed_status == "in_progress":
            secs = _staleness_seconds(now_iso, observed_updated)
            if secs is not None and secs < REMINDER_JOB_STALE_SECONDS:
                # Fresh in-progress: owned by another live run → busy.
                return ReminderClaim("busy")
            # Stale: only reclaim if nobody has touched it since we looked.
            cas = cas.where(ReminderJob.updated_at == observed_updated)

        result = await session.execute(cas.values(status="in_progress", updated_at=now_iso))
        await session.commit()
        if result.rowcount == 0:
            # Lost the race to another instance/tick.
            return ReminderClaim("busy")

        await session.refresh(job)
        return ReminderClaim("claimed", job)


async def cleanup_old_reminder_jobs(before_date: datetime.date) -> int:
    """
    Delete completed/stale outbox rows whose ``job_date`` is strictly before
    ``before_date`` so the table doesn't grow without bound. Returns the number
    of rows removed. Only old rows are touched — today's in-flight jobs stay.
    """
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            delete(ReminderJob).where(ReminderJob.job_date < before_date)
        )
        await session.commit()
        return result.rowcount


async def advance_reminder_job(job_id: int, chunks_sent: int, now_iso: str, done: bool = False):
    async with AsyncSessionLocal() as session:
        await session.execute(
            update(ReminderJob)
            .where(ReminderJob.id == job_id)
            .values(chunks_sent=chunks_sent, status="done" if done else "in_progress", updated_at=now_iso)
        )
        await session.commit()

async def get_reminder_job_chunks(job: ReminderJob) -> List[str]:
    import json
    return json.loads(job.chunks_json)


# ---------------------------------------------------------------------------
# Web / Telegram Mini App persistence
#
# All timestamps are ISO-8601 UTC strings supplied by the caller
# (services.timeservice.now_iso_utc), keeping this layer clock-free and easy to
# test. Launch tokens and sessions are stored only as sha256 hashes.
# ---------------------------------------------------------------------------


async def upsert_web_user(
    telegram_user_id: int, display_name: Optional[str], now_iso: str
) -> WebUser:
    """Create the WebUser for this Telegram id, or refresh its display name."""
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(WebUser).where(WebUser.telegram_user_id == telegram_user_id)
        )
        user = result.scalar_one_or_none()
        if user is None:
            user = WebUser(
                telegram_user_id=telegram_user_id,
                display_name=display_name,
                created_at=now_iso,
                updated_at=now_iso,
            )
            session.add(user)
            try:
                await session.commit()
            except IntegrityError:
                await session.rollback()
                result = await session.execute(
                    select(WebUser).where(WebUser.telegram_user_id == telegram_user_id)
                )
                user = result.scalar_one()
            else:
                await session.refresh(user)
                return user
        # Existing row: keep the freshest display name.
        user.display_name = display_name
        user.updated_at = now_iso
        await session.commit()
        await session.refresh(user)
        return user


async def get_web_user(telegram_user_id: int) -> Optional[WebUser]:
    """Read a WebUser by Telegram id (None if they never authenticated)."""
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(WebUser).where(WebUser.telegram_user_id == telegram_user_id)
        )
        return result.scalar_one_or_none()


async def upsert_membership(
    chat_id: int, user_id: int, role: str, now_iso: str
) -> ChatMembership:
    """Record (or re-verify) that ``user_id`` belongs to ``chat_id``.

    ``role`` is refreshed on every call so an admin who is later demoted (or
    promoted) is reflected the next time membership is verified.
    """
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(ChatMembership)
            .where(ChatMembership.chat_id == chat_id)
            .where(ChatMembership.user_id == user_id)
        )
        row = result.scalar_one_or_none()
        if row is None:
            row = ChatMembership(
                chat_id=chat_id,
                user_id=user_id,
                role=role,
                last_verified_at=now_iso,
                created_at=now_iso,
            )
            session.add(row)
            try:
                await session.commit()
            except IntegrityError:
                await session.rollback()
                result = await session.execute(
                    select(ChatMembership)
                    .where(ChatMembership.chat_id == chat_id)
                    .where(ChatMembership.user_id == user_id)
                )
                row = result.scalar_one()
            else:
                await session.refresh(row)
                return row
        row.role = role
        row.last_verified_at = now_iso
        await session.commit()
        await session.refresh(row)
        return row


async def touch_membership(
    chat_id: int, user_id: int, now_iso: str, default_role: str = "member"
) -> ChatMembership:
    """Re-verify a membership from the web side without changing its role.

    The bot is the authority on role (it can call Telegram's get_chat_member);
    the web login only refreshes ``last_verified_at``. If no row exists yet it is
    created with ``default_role`` so a direct launch link still works.
    """
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(ChatMembership)
            .where(ChatMembership.chat_id == chat_id)
            .where(ChatMembership.user_id == user_id)
        )
        row = result.scalar_one_or_none()
        if row is None:
            row = ChatMembership(
                chat_id=chat_id,
                user_id=user_id,
                role=default_role,
                last_verified_at=now_iso,
                created_at=now_iso,
            )
            session.add(row)
            try:
                await session.commit()
            except IntegrityError:
                await session.rollback()
                result = await session.execute(
                    select(ChatMembership)
                    .where(ChatMembership.chat_id == chat_id)
                    .where(ChatMembership.user_id == user_id)
                )
                row = result.scalar_one()
            else:
                await session.refresh(row)
                return row
        row.last_verified_at = now_iso
        await session.commit()
        await session.refresh(row)
        return row


async def get_membership(chat_id: int, user_id: int) -> Optional[ChatMembership]:
    """The membership row for this user in this chat, or None (→ 403)."""
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(ChatMembership)
            .where(ChatMembership.chat_id == chat_id)
            .where(ChatMembership.user_id == user_id)
        )
        return result.scalar_one_or_none()


async def get_memberships_for_user(user_id: int) -> List[ChatMembership]:
    """Every chat this user may see, newest verification first."""
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(ChatMembership)
            .where(ChatMembership.user_id == user_id)
            .order_by(ChatMembership.chat_id)
        )
        return list(result.scalars().all())


async def get_chats_by_ids(chat_ids: List[int]) -> Dict[int, Chat]:
    """Fetch several chats at once, keyed by id (for the class picker)."""
    if not chat_ids:
        return {}
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(Chat).where(Chat.chat_id.in_(chat_ids))
        )
        return {chat.chat_id: chat for chat in result.scalars().all()}


async def create_launch_token(
    token_hash: str,
    telegram_user_id: int,
    chat_id: int,
    now_iso: str,
    expires_iso: str,
) -> WebLaunchToken:
    """Persist a single-use launch token (hash only)."""
    async with AsyncSessionLocal() as session:
        token = WebLaunchToken(
            token_hash=token_hash,
            telegram_user_id=telegram_user_id,
            chat_id=chat_id,
            created_at=now_iso,
            expires_at=expires_iso,
        )
        session.add(token)
        await session.commit()
        await session.refresh(token)
        return token


async def consume_launch_token(token_hash: str, now_iso: str) -> Optional[WebLaunchToken]:
    """Atomically claim an unused launch token.

    The UPDATE ... WHERE used_at IS NULL is the single-use guarantee: a second
    concurrent (or later) attempt updates zero rows and gets ``None``. Expiry is
    validated by the caller against the returned row, so an expired token is
    rejected (its consumption is harmless).
    """
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            update(WebLaunchToken)
            .where(WebLaunchToken.token_hash == token_hash)
            .where(WebLaunchToken.used_at.is_(None))
            .values(used_at=now_iso)
        )
        await session.commit()
        if result.rowcount != 1:
            return None
        row = await session.execute(
            select(WebLaunchToken).where(WebLaunchToken.token_hash == token_hash)
        )
        return row.scalar_one_or_none()


async def create_web_session(
    session_hash: str, user_id: int, now_iso: str, expires_iso: str
) -> WebSession:
    """Persist an opaque web session (hash only)."""
    async with AsyncSessionLocal() as db_session:
        row = WebSession(
            session_hash=session_hash,
            user_id=user_id,
            created_at=now_iso,
            expires_at=expires_iso,
            last_seen_at=now_iso,
        )
        db_session.add(row)
        await db_session.commit()
        await db_session.refresh(row)
        return row


async def get_web_session(session_hash: str) -> Optional[WebSession]:
    """The session row for this hash (expiry is checked by the caller)."""
    async with AsyncSessionLocal() as db_session:
        result = await db_session.execute(
            select(WebSession).where(WebSession.session_hash == session_hash)
        )
        return result.scalar_one_or_none()


async def touch_web_session(session_hash: str, now_iso: str) -> None:
    """Update a session's last-seen stamp (best effort)."""
    async with AsyncSessionLocal() as db_session:
        await db_session.execute(
            update(WebSession)
            .where(WebSession.session_hash == session_hash)
            .values(last_seen_at=now_iso)
        )
        await db_session.commit()


async def delete_web_session(session_hash: str) -> None:
    """Invalidate a session (logout)."""
    async with AsyncSessionLocal() as db_session:
        await db_session.execute(
            delete(WebSession).where(WebSession.session_hash == session_hash)
        )
        await db_session.commit()


async def refresh_web_session(
    session_hash: str, last_seen_iso: str, expires_iso: str
) -> None:
    """Slide a session forward: bump last-seen and extend expiry (best effort)."""
    async with AsyncSessionLocal() as db_session:
        await db_session.execute(
            update(WebSession)
            .where(WebSession.session_hash == session_hash)
            .values(last_seen_at=last_seen_iso, expires_at=expires_iso)
        )
        await db_session.commit()


async def delete_web_sessions_for_user(user_id: int) -> int:
    """Invalidate every session of a user ("log out everywhere"). Returns count."""
    async with AsyncSessionLocal() as db_session:
        result = await db_session.execute(
            delete(WebSession).where(WebSession.user_id == user_id)
        )
        await db_session.commit()
        return int(result.rowcount or 0)

async def cleanup_expired_web_auth(before_iso: str) -> int:
    """Delete stale Mini App auth rows so they don't accumulate.

    Removes web sessions whose ``expires_at`` is before ``before_iso`` and
    launch tokens that are either already used or expired. Timestamps are a
    fixed ISO-8601 UTC format, so the string comparison is chronological.
    Returns the total number of rows removed. Never used for anything a
    request depends on — it is best-effort hygiene called from the nightly
    housekeeping.
    """
    async with AsyncSessionLocal() as session:
        sessions = await session.execute(
            delete(WebSession).where(WebSession.expires_at < before_iso)
        )
        tokens = await session.execute(
            delete(WebLaunchToken).where(
                or_(
                    WebLaunchToken.used_at.is_not(None),
                    WebLaunchToken.expires_at < before_iso,
                )
            )
        )
        await session.commit()
        return int(sessions.rowcount or 0) + int(tokens.rowcount or 0)

import datetime
from collections import defaultdict
from typing import List, Dict, Optional, Tuple
from sqlalchemy import select, update, delete, event, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from database.models import Base, Chat, LessonSlot, Schedule, Homework, ReminderJob
from config import DATABASE_URL

engine = create_async_engine(DATABASE_URL, echo=False)

@event.listens_for(engine.sync_engine, "connect")
def set_sqlite_pragma(dbapi_connection, connection_record):
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
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

async def get_schedule(chat_id: int, day_of_week: Optional[int] = None) -> List[Schedule]:
    async with AsyncSessionLocal() as session:
        query = select(Schedule).where(Schedule.chat_id == chat_id)
        if day_of_week is not None:
            query = query.where(Schedule.day_of_week == day_of_week)
        query = query.order_by(Schedule.lesson_number)
        result = await session.execute(query)
        return list(result.scalars().all())

async def save_schedule_day(chat_id: int, day_of_week: int, lessons: List[Tuple[int, str]]):
    """
    lessons: List of tuples (lesson_number, subject_name)
    """
    async with AsyncSessionLocal() as session:
        # Clear existing schedule for this day
        await session.execute(
            delete(Schedule)
            .where(Schedule.chat_id == chat_id)
            .where(Schedule.day_of_week == day_of_week)
        )
        for num, subject in lessons:
            # We don't save empty/skipped lessons to schedule
            if subject and subject.strip().lower() != "skip":
                sch = Schedule(chat_id=chat_id, day_of_week=day_of_week, lesson_number=num, subject_name=subject.strip())
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

        await session.execute(delete(Schedule).where(Schedule.chat_id == chat_id))
        for day_of_week, lessons in schedule_by_day.items():
            for num, subject in lessons:
                if subject and subject.strip().lower() != "skip" and num <= max_lesson_number:
                    session.add(Schedule(
                        chat_id=chat_id, day_of_week=day_of_week,
                        lesson_number=num, subject_name=subject.strip(),
                    ))

        await session.execute(
            update(Chat).where(Chat.chat_id == chat_id).values(is_onboarded=True)
        )
        await session.commit()

async def update_schedule_slot(chat_id: int, day_of_week: int, lesson_number: int, subject_name: str):
    async with AsyncSessionLocal() as session:
        # Delete first to overwrite
        await session.execute(
            delete(Schedule)
            .where(Schedule.chat_id == chat_id)
            .where(Schedule.day_of_week == day_of_week)
            .where(Schedule.lesson_number == lesson_number)
        )
        if subject_name and subject_name.strip() != "":
            sch = Schedule(chat_id=chat_id, day_of_week=day_of_week, lesson_number=lesson_number, subject_name=subject_name.strip())
            session.add(sch)
        await session.commit()

async def add_homework(chat_id: int, subject_name: str, due_date: datetime.date, description: str) -> Homework:
    async with AsyncSessionLocal() as session:
        hw = Homework(
            chat_id=chat_id,
            subject_name=subject_name.strip(),
            due_date=due_date,
            description=description.strip(),
            is_completed=False
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

async def mark_homework_completed(chat_id: int, homework_id: int, is_completed: bool = True) -> bool:
    """Returns False (no-op) if the homework doesn't exist for this chat."""
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            update(Homework)
            .where(Homework.chat_id == chat_id)
            .where(Homework.id == homework_id)
            .values(is_completed=is_completed)
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
) -> bool:
    """
    Updates one or more fields of a homework entry, always scoped to both
    chat_id and homework_id. Returns False (no-op) if the homework does not
    belong to this chat, e.g. a stale button or an already-deleted entry.
    """
    values = {}
    if subject_name is not None:
        values["subject_name"] = subject_name.strip()
    if description is not None:
        values["description"] = description.strip()
    if due_date is not None:
        values["due_date"] = due_date
    if not values:
        return False

    async with AsyncSessionLocal() as session:
        result = await session.execute(
            update(Homework)
            .where(Homework.chat_id == chat_id)
            .where(Homework.id == homework_id)
            .values(**values)
        )
        await session.commit()
        return result.rowcount > 0

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
    """Same batching idea as :func:`get_incomplete_homework_for_chats`, for a single day-of-week across many chats."""
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
        )
        session.add(new_chat)
        await session.flush()

        for model in (LessonSlot, Schedule, Homework):
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

async def claim_reminder_job(chat_id: int, kind: str, job_date: datetime.date, chunks: List[str], now_iso: str) -> Optional[ReminderJob]:
    """
    Creates (if needed) and atomically claims the ReminderJob for
    ``(chat_id, kind, job_date)``. Returns the claimed row, or ``None`` if a
    job for this key is already ``done`` or actively ``in_progress`` (claimed
    by this or another running instance) — in which case the caller must skip
    sending entirely to avoid duplicate delivery.
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
                # Another instance/tick inserted it first; fall through to re-fetch below.
                await session.rollback()
                result = await session.execute(
                    select(ReminderJob)
                    .where(ReminderJob.chat_id == chat_id)
                    .where(ReminderJob.kind == kind)
                    .where(ReminderJob.job_date == job_date)
                )
                job = result.scalar_one_or_none()
                if job is None:
                    return None
            else:
                await session.refresh(job)

        if job.status == "done":
            return None
        if job.status == "in_progress":
            # Simple staleness guard: a job claimed >10 min ago most likely
            # belongs to a crashed run, so it's safe to reclaim and resume.
            try:
                claimed_at = datetime.datetime.fromisoformat(job.updated_at)
            except ValueError:
                claimed_at = None
            if claimed_at is not None and (datetime.datetime.now(datetime.timezone.utc) - claimed_at.astimezone(datetime.timezone.utc)).total_seconds() < 600:
                return None

        result = await session.execute(
            update(ReminderJob)
            .where(ReminderJob.id == job.id)
            .where(ReminderJob.status != "done")
            .values(status="in_progress", updated_at=now_iso)
        )
        await session.commit()
        if result.rowcount == 0:
            return None

        await session.refresh(job)
        return job

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

import datetime
from typing import List, Dict, Optional, Tuple
from sqlalchemy import select, update, delete, event, text
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from database.models import Base, Chat, LessonSlot, Schedule, Homework
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
    it doesn't already exist. Any failure other than "already exists" (bad
    syntax, locked DB, etc.) propagates instead of being silently swallowed.
    """
    result = await conn.execute(text(f"PRAGMA table_info({table})"))
    existing_columns = {row[1] for row in result.fetchall()}
    if column not in existing_columns:
        await conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}"))

async def init_db():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        # Migrate existing DB (add columns introduced after the initial release).
        await _ensure_column(conn, "chats", "last_hw_reminder_date", "DATE")
        await _ensure_column(conn, "chats", "last_sch_reminder_date", "DATE")
        await _ensure_column(conn, "chats", "hw_reminder_enabled", "BOOLEAN NOT NULL DEFAULT 1")
        await _ensure_column(conn, "chats", "schedule_reminder_enabled", "BOOLEAN NOT NULL DEFAULT 1")

async def get_or_create_chat(chat_id: int, chat_type: str) -> Chat:
    async with AsyncSessionLocal() as session:
        result = await session.execute(select(Chat).where(Chat.chat_id == chat_id))
        chat = result.scalar_one_or_none()
        if not chat:
            chat = Chat(chat_id=chat_id, chat_type=chat_type)
            session.add(chat)
            await session.commit()
            await session.refresh(chat)
        return chat

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
    """
    async with AsyncSessionLocal() as session:
        # Clear existing slots
        await session.execute(delete(LessonSlot).where(LessonSlot.chat_id == chat_id))
        for num, start, end in slots:
            slot = LessonSlot(chat_id=chat_id, lesson_number=num, start_time=start, end_time=end)
            session.add(slot)
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

async def mark_homework_completed(chat_id: int, homework_id: int, is_completed: bool = True):
    async with AsyncSessionLocal() as session:
        await session.execute(
            update(Homework)
            .where(Homework.chat_id == chat_id)
            .where(Homework.id == homework_id)
            .values(is_completed=is_completed)
        )
        await session.commit()

async def delete_homework(chat_id: int, homework_id: int):
    async with AsyncSessionLocal() as session:
        await session.execute(
            delete(Homework)
            .where(Homework.chat_id == chat_id)
            .where(Homework.id == homework_id)
        )
        await session.commit()

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

async def get_all_chats() -> List[Chat]:
    async with AsyncSessionLocal() as session:
        result = await session.execute(select(Chat))
        return list(result.scalars().all())

async def get_homework_due_on(chat_id: int, due_date: datetime.date) -> List[Homework]:
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(Homework)
            .where(Homework.chat_id == chat_id)
            .where(Homework.due_date == due_date)
            .where(Homework.is_completed == False)
        )
        return list(result.scalars().all())

async def get_overdue_homework(chat_id: int, today: datetime.date) -> List[Homework]:
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(Homework)
            .where(Homework.chat_id == chat_id)
            .where(Homework.due_date < today)
            .where(Homework.is_completed == False)
            .order_by(Homework.due_date)
        )
        return list(result.scalars().all())

async def delete_chat(chat_id: int):
    async with AsyncSessionLocal() as session:
        await session.execute(delete(Chat).where(Chat.chat_id == chat_id))
        await session.commit()

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

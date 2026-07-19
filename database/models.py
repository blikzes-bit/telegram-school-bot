from sqlalchemy import (
    BigInteger, Boolean, CheckConstraint, Column, Date, ForeignKey, Index,
    Integer, String, Text, UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, relationship

class Base(DeclarativeBase):
    pass

class Chat(Base):
    __tablename__ = "chats"

    chat_id = Column(BigInteger, primary_key=True)
    chat_type = Column(String, nullable=False)
    hw_reminder_time = Column(String, default="18:00", nullable=False)  # HH:MM format
    schedule_reminder_time = Column(String, default="20:00", nullable=False)  # HH:MM format
    is_onboarded = Column(Boolean, default=False, nullable=False)
    last_hw_reminder_date = Column(Date, nullable=True)
    last_sch_reminder_date = Column(Date, nullable=True)
    hw_reminder_enabled = Column(Boolean, default=True, nullable=False)
    schedule_reminder_enabled = Column(Boolean, default=True, nullable=False)
    # Set when the bot is blocked/kicked; suppresses further reminder polling
    # for this chat until the user interacts with the bot again.
    is_blocked = Column(Boolean, default=False, nullable=False)

    # Relationships
    lesson_slots = relationship("LessonSlot", back_populates="chat", cascade="all, delete-orphan")
    schedules = relationship("Schedule", back_populates="chat", cascade="all, delete-orphan")
    homeworks = relationship("Homework", back_populates="chat", cascade="all, delete-orphan")

class LessonSlot(Base):
    __tablename__ = "lesson_slots"
    __table_args__ = (
        UniqueConstraint("chat_id", "lesson_number", name="uq_lesson_slots_chat_lesson"),
        CheckConstraint("lesson_number > 0", name="ck_lesson_slots_lesson_number_positive"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    chat_id = Column(BigInteger, ForeignKey("chats.chat_id", ondelete="CASCADE"), nullable=False)
    lesson_number = Column(Integer, nullable=False)
    start_time = Column(String, nullable=False)  # HH:MM format
    end_time = Column(String, nullable=False)    # HH:MM format

    # Relationship
    chat = relationship("Chat", back_populates="lesson_slots")

class Schedule(Base):
    __tablename__ = "schedule"
    __table_args__ = (
        UniqueConstraint("chat_id", "day_of_week", "lesson_number", name="uq_schedule_chat_day_lesson"),
        CheckConstraint("day_of_week BETWEEN 0 AND 6", name="ck_schedule_day_of_week_range"),
        CheckConstraint("lesson_number > 0", name="ck_schedule_lesson_number_positive"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    chat_id = Column(BigInteger, ForeignKey("chats.chat_id", ondelete="CASCADE"), nullable=False)
    day_of_week = Column(Integer, nullable=False)  # 0 = Monday, 6 = Sunday
    lesson_number = Column(Integer, nullable=False)
    subject_name = Column(String, nullable=False)

    # Relationship
    chat = relationship("Chat", back_populates="schedules")

class Homework(Base):
    __tablename__ = "homework"
    __table_args__ = (
        Index("ix_homework_chat_completed_due", "chat_id", "is_completed", "due_date"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    chat_id = Column(BigInteger, ForeignKey("chats.chat_id", ondelete="CASCADE"), nullable=False)
    subject_name = Column(String, nullable=False)
    due_date = Column(Date, nullable=False)
    description = Column(Text, nullable=False)
    is_completed = Column(Boolean, default=False, nullable=False)

    # Relationship
    chat = relationship("Chat", back_populates="homeworks")

class ReminderJob(Base):
    """
    Outbox row for one reminder "send attempt" (one chat, one reminder kind,
    one calendar day). Provides idempotent, resumable, multi-instance-safe
    delivery of long/multi-chunk reminders — see services/scheduler.py.
    """
    __tablename__ = "reminder_jobs"
    __table_args__ = (
        UniqueConstraint("chat_id", "kind", "job_date", name="uq_reminder_job_chat_kind_date"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    chat_id = Column(BigInteger, ForeignKey("chats.chat_id", ondelete="CASCADE"), nullable=False)
    kind = Column(String, nullable=False)  # "hw" | "sched"
    job_date = Column(Date, nullable=False)
    chunks_json = Column(Text, nullable=False)  # JSON list[str] of rendered message chunks
    chunks_total = Column(Integer, nullable=False)
    chunks_sent = Column(Integer, default=0, nullable=False)
    status = Column(String, default="pending", nullable=False)  # pending|in_progress|done
    updated_at = Column(String, nullable=False)  # ISO timestamp string (informational/staleness only)

class FSMStateRow(Base):
    """Persistent backing store for aiogram FSM state (see database/fsm_storage.py)."""
    __tablename__ = "fsm_state"

    key = Column(String, primary_key=True)
    state = Column(String, nullable=True)
    data = Column(Text, nullable=False, default="{}")

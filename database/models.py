from sqlalchemy import BigInteger, Boolean, Column, Date, ForeignKey, Integer, String, Text
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

    # Relationships
    lesson_slots = relationship("LessonSlot", back_populates="chat", cascade="all, delete-orphan")
    schedules = relationship("Schedule", back_populates="chat", cascade="all, delete-orphan")
    homeworks = relationship("Homework", back_populates="chat", cascade="all, delete-orphan")

class LessonSlot(Base):
    __tablename__ = "lesson_slots"

    id = Column(Integer, primary_key=True, autoincrement=True)
    chat_id = Column(BigInteger, ForeignKey("chats.chat_id", ondelete="CASCADE"), nullable=False)
    lesson_number = Column(Integer, nullable=False)
    start_time = Column(String, nullable=False)  # HH:MM format
    end_time = Column(String, nullable=False)    # HH:MM format

    # Relationship
    chat = relationship("Chat", back_populates="lesson_slots")

class Schedule(Base):
    __tablename__ = "schedule"

    id = Column(Integer, primary_key=True, autoincrement=True)
    chat_id = Column(BigInteger, ForeignKey("chats.chat_id", ondelete="CASCADE"), nullable=False)
    day_of_week = Column(Integer, nullable=False)  # 0 = Monday, 6 = Sunday
    lesson_number = Column(Integer, nullable=False)
    subject_name = Column(String, nullable=False)

    # Relationship
    chat = relationship("Chat", back_populates="schedules")

class Homework(Base):
    __tablename__ = "homework"

    id = Column(Integer, primary_key=True, autoincrement=True)
    chat_id = Column(BigInteger, ForeignKey("chats.chat_id", ondelete="CASCADE"), nullable=False)
    subject_name = Column(String, nullable=False)
    due_date = Column(Date, nullable=False)
    description = Column(Text, nullable=False)
    is_completed = Column(Boolean, default=False, nullable=False)

    # Relationship
    chat = relationship("Chat", back_populates="homeworks")

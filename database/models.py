from sqlalchemy import (
    BigInteger, Boolean, CheckConstraint, Column, Date, ForeignKey, Index,
    Integer, String, Text, UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, relationship

from config import TIMEZONE as DEFAULT_TIMEZONE

class Base(DeclarativeBase):
    pass


class AuthorshipMixin:
    """
    Who created a record and who last changed it.

    All six columns are nullable on purpose: every row that existed before
    authorship was introduced keeps ``NULL`` here, and the app must treat
    "unknown author" as a normal, supported case rather than an error (see
    services/permissions.py for how the homework-edit policy handles it).

    Only the acting user's Telegram id and *display name* are stored — never a
    username, phone number, token or raw Update payload. ``created_at`` /
    ``updated_at`` are ISO-8601 UTC strings (same convention as
    ``ReminderJob.updated_at``), so they are instance- and timezone-agnostic
    and sort lexicographically.
    """
    created_by_user_id = Column(BigInteger, nullable=True)
    created_by_name = Column(String, nullable=True)
    updated_by_user_id = Column(BigInteger, nullable=True)
    updated_by_name = Column(String, nullable=True)
    created_at = Column(String, nullable=True)   # ISO-8601 UTC
    updated_at = Column(String, nullable=True)   # ISO-8601 UTC

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
    # Alternating (A/B, "чётная/нечётная") week support. When ``week_mode`` is
    # off (the default, and how every pre-existing chat behaves) the single
    # ``all`` weekly template is always used. When on, ``week_anchor_monday`` is
    # the Monday that starts week A; the week alternates A→B→A… from there.
    week_mode = Column(Boolean, default=False, nullable=False)
    week_anchor_monday = Column(Date, nullable=True)

    # --- Reminder categories (each independently toggleable) ---
    # hw_reminder_* (evening "homework due tomorrow") and schedule_reminder_*
    # ("pack your bag") already exist above. Three more categories:
    #   * due-today homework (a morning nudge on the day homework is due);
    #   * next-day schedule changes/cancellations (a dedicated heads-up);
    #   * per-activity reminders for extra activities (config lives on each
    #     ExtraActivity; this is the chat-wide master switch).
    hw_duetoday_enabled = Column(Boolean, default=True, nullable=False)
    hw_duetoday_time = Column(String, default="07:30", nullable=False)  # HH:MM
    changes_reminder_enabled = Column(Boolean, default=True, nullable=False)
    extra_reminder_enabled = Column(Boolean, default=True, nullable=False)
    last_duetoday_reminder_date = Column(Date, nullable=True)
    last_changes_reminder_date = Column(Date, nullable=True)

    # Quiet hours (HH:MM, may wrap past midnight). During quiet hours non-urgent
    # reminders are deferred; both NULL means "no quiet hours".
    quiet_start = Column(String, nullable=True)
    quiet_end = Column(String, nullable=True)

    # Who may edit/complete/delete a homework entry in this chat:
    #   * ``collaborative``    — anybody in the chat (the default, and exactly
    #     how every pre-existing chat already behaved);
    #   * ``creator_or_admin`` — the entry's author or a chat admin;
    #   * ``admin_only``       — chat admins only.
    # Private chats have a single user, so the policy never restricts anything
    # there. See services/permissions.py — the policy is enforced server-side,
    # not merely by hiding buttons.
    hw_edit_policy = Column(String, default="collaborative", nullable=False)

    # IANA timezone name (e.g. "Europe/Kyiv") used for *every* user-visible date
    # and time of this chat: "Сегодня", homework due dates, even/odd weeks, date
    # overrides, extra activities and all reminders. NOT NULL with the process
    # default from ``config.TIMEZONE``, so existing chats keep behaving exactly
    # as they did while new chats can diverge. An unknown/retired zone name is
    # tolerated at read time (services/timeservice.chat_tz falls back to the
    # global default) so one bad value can never stop the scheduler.
    timezone = Column(String, default=DEFAULT_TIMEZONE, nullable=False)

    # Human-readable class name shown in the Mini App class picker.
    # Nullable so every pre-existing chat keeps working with no name set.
    title = Column(String, nullable=True)

    # Relationships
    lesson_slots = relationship("LessonSlot", back_populates="chat", cascade="all, delete-orphan")
    schedules = relationship("Schedule", back_populates="chat", cascade="all, delete-orphan")
    homeworks = relationship("Homework", back_populates="chat", cascade="all, delete-orphan")
    extra_activities = relationship("ExtraActivity", back_populates="chat", cascade="all, delete-orphan")
    day_overrides = relationship("DayOverride", back_populates="chat", cascade="all, delete-orphan")
    lesson_overrides = relationship("LessonOverride", back_populates="chat", cascade="all, delete-orphan")
    audit_entries = relationship("AuditLog", back_populates="chat", cascade="all, delete-orphan")

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
        # ``week_type`` is part of the identity of a template row so the same
        # (day, lesson) can carry different subjects on week A vs week B.
        # ``all`` is the single-template default used when a chat does not use
        # alternating weeks — which is every pre-existing chat.
        UniqueConstraint(
            "chat_id", "week_type", "day_of_week", "lesson_number",
            name="uq_schedule_chat_week_day_lesson",
        ),
        CheckConstraint("week_type IN ('all', 'A', 'B')", name="ck_schedule_week_type"),
        CheckConstraint("day_of_week BETWEEN 0 AND 6", name="ck_schedule_day_of_week_range"),
        CheckConstraint("lesson_number > 0", name="ck_schedule_lesson_number_positive"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    chat_id = Column(BigInteger, ForeignKey("chats.chat_id", ondelete="CASCADE"), nullable=False)
    week_type = Column(String, nullable=False, default="all")  # all|A|B
    day_of_week = Column(Integer, nullable=False)  # 0 = Monday, 6 = Sunday
    lesson_number = Column(Integer, nullable=False)
    subject_name = Column(String, nullable=False)

    # Relationship
    chat = relationship("Chat", back_populates="schedules")

class Homework(Base, AuthorshipMixin):
    __tablename__ = "homework"
    __table_args__ = (
        Index("ix_homework_chat_completed_due", "chat_id", "is_completed", "due_date"),
        Index("ix_homework_chat_creator", "chat_id", "created_by_user_id"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    chat_id = Column(BigInteger, ForeignKey("chats.chat_id", ondelete="CASCADE"), nullable=False)
    subject_name = Column(String, nullable=False)
    due_date = Column(Date, nullable=False)
    description = Column(Text, nullable=False)
    is_completed = Column(Boolean, default=False, nullable=False)

    # Relationships
    chat = relationship("Chat", back_populates="homeworks")
    attachments = relationship(
        "HomeworkAttachment", back_populates="homework",
        cascade="all, delete-orphan", passive_deletes=True,
    )

class HomeworkAttachment(Base):
    """
    One photo / document attached to a homework entry.

    **No binary ever touches this bot.** Only Telegram's own references are
    stored — ``file_id`` (what we pass back to ``sendPhoto``/``sendDocument``)
    and ``file_unique_id`` (stable across bots, used to spot duplicates) —
    plus the metadata needed to render and validate a card: kind, the
    *sanitised* original file name, size in bytes and an optional caption.
    Nothing is downloaded, nothing is unpacked, nothing is executed.

    ``file_name`` comes from the client and is untrusted: it is sanitised on the
    way in (see utils.safe_file_name) and HTML-escaped on the way out. It is
    display metadata only and is never used as a filesystem path.

    Deleting the homework deletes its attachments (FK ``ON DELETE CASCADE`` plus
    an ORM cascade), so no orphan rows can outlive their parent.
    """
    __tablename__ = "homework_attachments"
    __table_args__ = (
        CheckConstraint(
            "file_type IN ('photo', 'document')",
            name="ck_homework_attachments_file_type",
        ),
        CheckConstraint(
            "file_size IS NULL OR file_size >= 0",
            name="ck_homework_attachments_file_size",
        ),
        # The same file attached twice to the same homework is pointless noise.
        UniqueConstraint(
            "homework_id", "file_unique_id",
            name="uq_homework_attachments_homework_file",
        ),
        Index("ix_homework_attachments_homework", "homework_id"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    homework_id = Column(
        Integer, ForeignKey("homework.id", ondelete="CASCADE"), nullable=False
    )
    file_id = Column(String, nullable=False)         # Telegram file_id (bot-specific)
    file_unique_id = Column(String, nullable=False)  # stable id, for dedupe
    file_type = Column(String, nullable=False)       # "photo" | "document"
    file_name = Column(String, nullable=True)        # sanitised, display only
    file_size = Column(Integer, nullable=True)       # bytes, as reported by Telegram
    caption = Column(Text, nullable=True)            # optional, length-capped
    created_at = Column(String, nullable=True)       # ISO-8601 UTC
    created_by_user_id = Column(BigInteger, nullable=True)
    created_by_name = Column(String, nullable=True)

    homework = relationship("Homework", back_populates="attachments")


class ExtraActivity(Base, AuthorshipMixin):
    """
    A supplementary activity that is *not* a regular school lesson: a club,
    tutor, sports section or an extra class (e.g. English at 18:00). Kept in a
    dedicated table so it never mixes with LessonSlot/Schedule — re-running the
    school-schedule onboarding must never touch these.

    Two recurrence kinds:
      * ``weekly`` — repeats every week on ``day_of_week`` (0=Mon..6=Sun);
        ``activity_date`` is NULL.
      * ``once``   — a single dated event on ``activity_date``;
        ``day_of_week`` is NULL.

    ``start_time`` is required (``HH:MM``); ``end_time`` is optional so a plain
    "18:00" is accepted alongside "18:00 - 19:00". ``location`` and ``note``
    are optional free text.
    """
    __tablename__ = "extra_activities"
    __table_args__ = (
        CheckConstraint("kind IN ('weekly', 'once')", name="ck_extra_activities_kind"),
        CheckConstraint("day_of_week IS NULL OR (day_of_week BETWEEN 0 AND 6)", name="ck_extra_activities_day_range"),
        CheckConstraint(
            "(kind = 'weekly' AND day_of_week IS NOT NULL AND activity_date IS NULL) "
            "OR (kind = 'once' AND activity_date IS NOT NULL AND day_of_week IS NULL)",
            name="ck_extra_activities_recurrence",
        ),
        CheckConstraint(
            "reminder_minutes >= 0 AND reminder_minutes <= 10080",
            name="ck_extra_activities_reminder_minutes",
        ),
        Index("ix_extra_activities_chat_day", "chat_id", "day_of_week"),
        Index("ix_extra_activities_chat_date", "chat_id", "activity_date"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    chat_id = Column(BigInteger, ForeignKey("chats.chat_id", ondelete="CASCADE"), nullable=False)
    title = Column(String, nullable=False)
    kind = Column(String, nullable=False)  # "weekly" | "once"
    day_of_week = Column(Integer, nullable=True)  # 0=Monday..6=Sunday (weekly only)
    activity_date = Column(Date, nullable=True)   # concrete date (once only)
    start_time = Column(String, nullable=False)   # HH:MM
    end_time = Column(String, nullable=True)      # HH:MM, optional
    location = Column(String, nullable=True)
    note = Column(Text, nullable=True)
    # Per-activity reminder: whether to remind, and how many minutes before the
    # start (0..10080 = up to a week). Off by default so existing activities are
    # unaffected; a chat-wide master switch lives on Chat.extra_reminder_enabled.
    reminder_enabled = Column(Boolean, default=False, nullable=False)
    reminder_minutes = Column(Integer, default=60, nullable=False)

    # Relationship
    chat = relationship("Chat", back_populates="extra_activities")

class DayOverride(Base, AuthorshipMixin):
    """
    A per-date, whole-day setting that overlays the weekly template for one
    concrete calendar date (NOT a weekday): a fully free day, a public holiday,
    a vacation day, or a remote-learning day. ``note`` is an optional
    human-readable reason shown to the user.

      * ``free``     — no lessons at all that day;
      * ``holiday``  — public holiday (no lessons);
      * ``vacation`` — school vacation (no lessons);
      * ``remote``   — remote-learning day; the regular (or overridden) lessons
        still apply, just flagged as distance learning.

    Exactly one row may exist per (chat_id, date). The weekly Schedule template
    is never mutated — this table (plus LessonOverride) is applied on top of it
    by services/effective_schedule.py.
    """
    __tablename__ = "day_overrides"
    __table_args__ = (
        UniqueConstraint("chat_id", "date", name="uq_day_overrides_chat_date"),
        CheckConstraint(
            "day_type IN ('free', 'holiday', 'vacation', 'remote')",
            name="ck_day_overrides_type",
        ),
        Index("ix_day_overrides_chat_date", "chat_id", "date"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    chat_id = Column(BigInteger, ForeignKey("chats.chat_id", ondelete="CASCADE"), nullable=False)
    date = Column(Date, nullable=False)
    day_type = Column(String, nullable=False)  # free|holiday|vacation|remote
    note = Column(Text, nullable=True)

    chat = relationship("Chat", back_populates="day_overrides")


class LessonOverride(Base, AuthorshipMixin):
    """
    A per-date, per-lesson change overlaying the weekly Schedule template for
    one concrete calendar date:

      * ``cancel`` — the lesson at ``lesson_number`` is cancelled that day
        (rendered struck-through); ``subject_name``/times may be NULL and are
        taken from the template for display.
      * ``set``    — overrides/adds a lesson: any non-NULL of
        ``subject_name`` / ``start_time`` / ``end_time`` replaces the template
        value; NULL fields fall back to the template. A ``lesson_number`` not
        present in the template makes this a one-off *added* lesson (in which
        case subject + times are provided).

    Exactly one row may exist per (chat_id, date, lesson_number). The weekly
    template is never mutated.
    """
    __tablename__ = "lesson_overrides"
    __table_args__ = (
        UniqueConstraint(
            "chat_id", "date", "lesson_number",
            name="uq_lesson_overrides_chat_date_lesson",
        ),
        CheckConstraint("action IN ('cancel', 'set')", name="ck_lesson_overrides_action"),
        CheckConstraint("lesson_number > 0", name="ck_lesson_overrides_lesson_number_positive"),
        Index("ix_lesson_overrides_chat_date", "chat_id", "date"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    chat_id = Column(BigInteger, ForeignKey("chats.chat_id", ondelete="CASCADE"), nullable=False)
    date = Column(Date, nullable=False)
    lesson_number = Column(Integer, nullable=False)
    action = Column(String, nullable=False)  # cancel|set
    subject_name = Column(String, nullable=True)
    start_time = Column(String, nullable=True)  # HH:MM
    end_time = Column(String, nullable=True)    # HH:MM
    note = Column(Text, nullable=True)

    chat = relationship("Chat", back_populates="lesson_overrides")


class AuditLog(Base):
    """
    Append-only journal of the important changes made in a chat.

    Deliberately minimal and safe to keep: the acting user's Telegram id, their
    *display name*, which kind of entity changed, its id, what happened, and a
    short human-readable summary. Never a token, never a raw Telegram Update,
    never a username/phone/message payload — the summary is built by
    services/audit.py from a small, explicit allow-list of fields and is
    truncated (see ``AUDIT_SUMMARY_MAX``).

    An entry outlives the record it describes: deleting a homework entry removes
    the homework but keeps the "deleted" audit row (``entity_id`` then points at
    an id that no longer exists — intentional, and why there is no FK on it).
    Only ``chat_id`` cascades, so a full chat reset still wipes its history.

    Rows older than the configured retention window are pruned by the nightly
    scheduler housekeeping (see services/scheduler.py).
    """
    __tablename__ = "audit_log"
    __table_args__ = (
        CheckConstraint(
            "action IN ('create', 'update', 'delete', 'complete', 'restore')",
            name="ck_audit_log_action",
        ),
        # The history screen reads "newest first for this chat", optionally
        # filtered by entity type; the third index serves retention pruning.
        Index("ix_audit_log_chat_id_desc", "chat_id", "id"),
        Index("ix_audit_log_chat_entity", "chat_id", "entity_type", "id"),
        Index("ix_audit_log_created_at", "created_at"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    chat_id = Column(BigInteger, ForeignKey("chats.chat_id", ondelete="CASCADE"), nullable=False)
    # NULL when the change wasn't made by a user (e.g. a background/system action).
    actor_user_id = Column(BigInteger, nullable=True)
    actor_name = Column(String, nullable=True)
    entity_type = Column(String, nullable=False)  # homework|extra|schedule|day_override|lesson_override|settings
    entity_id = Column(Integer, nullable=True)    # no FK on purpose — see docstring
    action = Column(String, nullable=False)       # create|update|delete|complete|restore
    summary = Column(Text, nullable=True)         # short, safe, already-truncated
    created_at = Column(String, nullable=False)   # ISO-8601 UTC

    chat = relationship("Chat", back_populates="audit_entries")


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


# ---------------------------------------------------------------------------
# Web / Telegram Mini App models
#
# These back the read-only Mini App (see web_api/). They store the bare minimum
# needed to authenticate a Telegram user and scope every request to the chats
# they actually belong to: only a Telegram id + display name are kept — never a
# username, phone number, token or raw Update payload (mirrors the audit/
# authorship privacy rule). Every timestamp is an ISO-8601 UTC string, the same
# convention used by AuthorshipMixin / ReminderJob, so values stay comparable
# across instances and timezones.
# ---------------------------------------------------------------------------


class WebUser(Base):
    """A Telegram user who has authenticated to the Mini App at least once."""

    __tablename__ = "web_users"
    __table_args__ = (
        UniqueConstraint("telegram_user_id", name="uq_web_users_tg_id"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    telegram_user_id = Column(BigInteger, nullable=False)
    display_name = Column(String, nullable=True)
    created_at = Column(String, nullable=False)  # ISO-8601 UTC
    updated_at = Column(String, nullable=False)  # ISO-8601 UTC


class ChatMembership(Base):
    """Which chats (classes) a web user may see, and with what role.

    A row exists only for a membership the bot has verified server-side (the
    user issued ``/web`` inside that group, or opened a launch link bound to it).
    The Mini App refuses any chat_id without a matching row here — an unknown or
    unverified chat_id yields 403, never an empty result.
    """

    __tablename__ = "chat_memberships"
    __table_args__ = (
        UniqueConstraint("chat_id", "user_id", name="uq_chat_memberships_chat_user"),
        CheckConstraint("role IN ('member', 'admin')", name="ck_chat_memberships_role"),
        Index("ix_chat_memberships_user", "user_id"),
        Index("ix_chat_memberships_chat", "chat_id"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    chat_id = Column(BigInteger, ForeignKey("chats.chat_id", ondelete="CASCADE"), nullable=False)
    user_id = Column(BigInteger, nullable=False)  # Telegram user id
    role = Column(String, nullable=False, default="member")  # member|admin
    last_verified_at = Column(String, nullable=False)  # ISO-8601 UTC
    created_at = Column(String, nullable=False)        # ISO-8601 UTC


class WebLaunchToken(Base):
    """A single-use, short-lived token that bootstraps a Mini App session.

    Minted by the bot's ``/web`` command after verifying the caller's group
    membership. Only the *hash* of the random token is stored (never the token
    itself); it is bound to one ``telegram_user_id`` + ``chat_id``, expires after
    a few minutes and may be consumed exactly once.
    """

    __tablename__ = "web_launch_tokens"
    __table_args__ = (
        UniqueConstraint("token_hash", name="uq_web_launch_tokens_hash"),
        Index("ix_web_launch_tokens_hash", "token_hash"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    token_hash = Column(String, nullable=False)  # sha256 hex of the raw token
    telegram_user_id = Column(BigInteger, nullable=False)
    chat_id = Column(BigInteger, ForeignKey("chats.chat_id", ondelete="CASCADE"), nullable=False)
    created_at = Column(String, nullable=False)  # ISO-8601 UTC
    expires_at = Column(String, nullable=False)  # ISO-8601 UTC
    used_at = Column(String, nullable=True)      # ISO-8601 UTC, set on consume


class WebSession(Base):
    """An opaque, cookie-backed web session.

    The cookie carries only a random token; this table stores its ``sha256``
    hash, the owning Telegram user id, and an expiry. Nothing about the token is
    recoverable from the database.
    """

    __tablename__ = "web_sessions"
    __table_args__ = (
        UniqueConstraint("session_hash", name="uq_web_sessions_hash"),
        Index("ix_web_sessions_hash", "session_hash"),
        Index("ix_web_sessions_user", "user_id"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    session_hash = Column(String, nullable=False)  # sha256 hex of the cookie token
    user_id = Column(BigInteger, nullable=False)   # Telegram user id
    created_at = Column(String, nullable=False)    # ISO-8601 UTC
    expires_at = Column(String, nullable=False)    # ISO-8601 UTC
    last_seen_at = Column(String, nullable=True)   # ISO-8601 UTC

"""Data-transfer objects for the web API.

These Pydantic models are the *only* things the API serialises. SQLAlchemy ORM
instances are never returned directly: every DTO is built explicitly from domain
objects (see ``application/queries.py``), so a change to a table column can never
silently leak a new field over the wire, and the API contract stays decoupled
from the storage schema.
"""
import datetime
import re
from typing import List, Optional

from pydantic import BaseModel, field_validator, model_validator

_TIME_RE = re.compile(r"^([0-1]?\d|2[0-3]):[0-5]\d$")


class MeDTO(BaseModel):
    """The authenticated web user. Only id + display name are ever exposed."""

    telegram_user_id: int
    display_name: Optional[str] = None


class ClassDTO(BaseModel):
    """One class (chat) the user belongs to, for the class picker."""

    chat_id: int
    title: Optional[str] = None
    role: str
    timezone: str


class PermissionsDTO(BaseModel):
    """What the current user may do in the current class.

    Read-only in stage 1; surfaced so the frontend can pre-render controls and
    future mutation endpoints can share the same server-computed policy.
    """

    is_admin: bool
    can_edit_homework: bool
    can_edit_schedule: bool


class LessonDTO(BaseModel):
    lesson_number: int
    start_time: Optional[str] = None
    end_time: Optional[str] = None
    subject_name: Optional[str] = None
    cancelled: bool = False
    added: bool = False
    time_changed: bool = False
    subject_changed: bool = False
    note: Optional[str] = None


class ExtraActivityDTO(BaseModel):
    id: int
    title: str
    kind: str
    day_of_week: Optional[int] = None
    activity_date: Optional[datetime.date] = None
    start_time: str
    end_time: Optional[str] = None
    location: Optional[str] = None
    note: Optional[str] = None
    can_edit: bool = True  # server-computed: may the current user add/edit/delete extras


class ExtraActivityCreateDTO(BaseModel):
    """Mirrors the bot's rule: admins only in a group, anyone in a private chat
    (enforced server-side via ``ClassContext.permissions.is_admin``, not here)."""

    title: str
    kind: str  # weekly | once
    day_of_week: Optional[int] = None
    activity_date: Optional[datetime.date] = None
    start_time: str
    end_time: Optional[str] = None
    location: Optional[str] = None
    note: Optional[str] = None

    @field_validator("title")
    @classmethod
    def _title_not_blank(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("title must not be blank")
        return v

    @field_validator("start_time", "end_time")
    @classmethod
    def _valid_time(cls, v: Optional[str]) -> Optional[str]:
        if v is not None and not _TIME_RE.match(v):
            raise ValueError("time must be HH:MM")
        return v

    @model_validator(mode="after")
    def _check_recurrence(self) -> "ExtraActivityCreateDTO":
        if self.kind not in ("weekly", "once"):
            raise ValueError("kind must be 'weekly' or 'once'")
        if self.kind == "weekly":
            if self.day_of_week is None or not (0 <= self.day_of_week <= 6):
                raise ValueError("weekly activities require day_of_week in 0..6")
            if self.activity_date is not None:
                raise ValueError("weekly activities must not set activity_date")
        else:
            if self.activity_date is None:
                raise ValueError("once activities require activity_date")
            if self.day_of_week is not None:
                raise ValueError("once activities must not set day_of_week")
        if self.end_time is not None and self.end_time <= self.start_time:
            raise ValueError("end_time must be after start_time")
        return self


class ExtraActivityUpdateDTO(BaseModel):
    """Partial update: title/time/place/note, plus the recurrence anchor that
    matches the activity's existing ``kind`` (day_of_week for weekly, date for
    a one-off) — switching kind itself is not supported from the web."""

    title: Optional[str] = None
    start_time: Optional[str] = None
    end_time: Optional[str] = None
    location: Optional[str] = None
    note: Optional[str] = None
    day_of_week: Optional[int] = None
    activity_date: Optional[datetime.date] = None

    @field_validator("title")
    @classmethod
    def _title_not_blank(cls, v: Optional[str]) -> Optional[str]:
        if v is not None and not v.strip():
            raise ValueError("title must not be blank")
        return v

    @field_validator("start_time", "end_time")
    @classmethod
    def _valid_time(cls, v: Optional[str]) -> Optional[str]:
        if v is not None and not _TIME_RE.match(v):
            raise ValueError("time must be HH:MM")
        return v

    @field_validator("day_of_week")
    @classmethod
    def _valid_day(cls, v: Optional[int]) -> Optional[int]:
        if v is not None and not (0 <= v <= 6):
            raise ValueError("day_of_week must be 0..6")
        return v

    @model_validator(mode="after")
    def _check_times(self) -> "ExtraActivityUpdateDTO":
        if self.start_time is not None and self.end_time is not None and self.end_time <= self.start_time:
            raise ValueError("end_time must be after start_time")
        return self


class HomeworkDTO(BaseModel):
    id: int
    subject_name: str
    due_date: datetime.date
    description: str
    is_completed: bool
    status: str  # active | completed | overdue
    can_edit: bool = True  # server-computed: may the current user complete/edit this entry


class HomeworkCreateDTO(BaseModel):
    """Adding homework is unrestricted for any class member (mirrors the bot)."""

    subject_name: str
    due_date: datetime.date
    description: str


class HomeworkCompleteDTO(BaseModel):
    is_completed: bool = True


class DayScheduleDTO(BaseModel):
    date: datetime.date
    weekday: int
    week_type: str
    day_type: Optional[str] = None
    day_note: Optional[str] = None
    lessons: List[LessonDTO] = []
    extra: List[ExtraActivityDTO] = []


class ScheduleRangeDTO(BaseModel):
    from_date: datetime.date
    to_date: datetime.date
    timezone: str
    days: List[DayScheduleDTO] = []


class TodayDTO(BaseModel):
    date: datetime.date
    timezone: str
    weekday: int
    week_type: str
    day_type: Optional[str] = None
    day_note: Optional[str] = None
    lessons: List[LessonDTO] = []
    extra: List[ExtraActivityDTO] = []
    homework_today: List[HomeworkDTO] = []
    overdue: List[HomeworkDTO] = []
    upcoming: List[HomeworkDTO] = []
    permissions: PermissionsDTO


class HealthDTO(BaseModel):
    status: str
    app_version: str


class ReminderSettingsDTO(BaseModel):
    """Mirrors the bot's "⏰ Напоминания" screen. Viewing is unrestricted for
    any class member; ``can_edit`` tells the frontend whether *this* user may
    change anything (admin-only in a group, unrestricted in a private chat) —
    the mutation endpoint re-checks the same thing server-side."""

    hw_reminder_enabled: bool
    hw_reminder_time: str
    schedule_reminder_enabled: bool
    schedule_reminder_time: str
    hw_duetoday_enabled: bool
    hw_duetoday_time: str
    changes_reminder_enabled: bool
    extra_reminder_enabled: bool
    quiet_start: Optional[str] = None
    quiet_end: Optional[str] = None
    can_edit: bool = True


class ReminderSettingsUpdateDTO(BaseModel):
    """Every field is optional so the caller can send just what changed;
    ``clear_quiet_hours`` is the explicit "turn quiet hours off" action (a pair
    of nulls, as opposed to two omitted fields, which mean "leave unchanged")."""

    hw_reminder_enabled: Optional[bool] = None
    hw_reminder_time: Optional[str] = None
    schedule_reminder_enabled: Optional[bool] = None
    schedule_reminder_time: Optional[str] = None
    hw_duetoday_enabled: Optional[bool] = None
    hw_duetoday_time: Optional[str] = None
    changes_reminder_enabled: Optional[bool] = None
    extra_reminder_enabled: Optional[bool] = None
    quiet_start: Optional[str] = None
    quiet_end: Optional[str] = None
    clear_quiet_hours: bool = False

    @field_validator("hw_reminder_time", "schedule_reminder_time", "hw_duetoday_time", "quiet_start", "quiet_end")
    @classmethod
    def _valid_time(cls, v: Optional[str]) -> Optional[str]:
        if v is not None and not _TIME_RE.match(v):
            raise ValueError("time must be HH:MM")
        return v


class AuditEntryDTO(BaseModel):
    id: int
    created_at: str
    actor_name: str  # already resolved to a display label — see services.audit.actor_label
    entity_type: str
    entity_id: Optional[int] = None
    action: str
    summary: Optional[str] = None


class AuditPageDTO(BaseModel):
    items: List[AuditEntryDTO] = []
    total: int
    page: int
    page_size: int

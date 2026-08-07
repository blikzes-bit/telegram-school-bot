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

from pydantic import BaseModel, Field, field_validator, model_validator

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

    Computed server-side by ``services.permissions.capabilities`` — the same
    function the bot uses — and surfaced so the frontend can render only the
    controls that will actually work. Every mutation endpoint re-checks these
    rather than trusting the client to have hidden a button.
    """

    role: str
    is_owner: bool
    is_admin: bool
    can_edit_homework: bool
    can_edit_schedule: bool
    can_add_homework: bool
    can_complete_homework: bool
    can_edit_extra: bool
    can_edit_payments: bool
    can_manage_members: bool


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
    # Both server-computed. Separate because a student may tick homework off
    # without being allowed to rewrite or delete it.
    can_edit: bool = True
    can_complete: bool = True
    # True when this chat gives everybody their own mark; then ``is_completed``
    # means "I have done it" and ``completed_count`` says how many people are
    # done (None where marks are shared, so the client cannot mistake 0 for
    # "nobody has done it yet").
    per_student: bool = False
    completed_count: Optional[int] = None


class HomeworkCreateDTO(BaseModel):
    """Adding homework is unrestricted for any class member (mirrors the bot)."""

    subject_name: str
    due_date: datetime.date
    description: str


class HomeworkCompleteDTO(BaseModel):
    is_completed: bool = True


class HomeworkUpdateDTO(BaseModel):
    """Partial edit of an existing entry: any subset of subject / due date /
    description. Who may edit is *not* decided here — it is the chat's
    ``hw_edit_policy``, enforced server-side in ``application.queries``."""

    subject_name: Optional[str] = None
    due_date: Optional[datetime.date] = None
    description: Optional[str] = None

    @field_validator("subject_name", "description")
    @classmethod
    def _not_blank(cls, v: Optional[str]) -> Optional[str]:
        if v is not None and not v.strip():
            raise ValueError("must not be blank")
        return v


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


class LabeledOptionDTO(BaseModel):
    """A pickable option with a human label and a one-line explanation.

    Shared by every picker the API serves (roles, access modes, day types) so
    the frontend renders them all with one component and never hardcodes a list.
    """

    name: str
    label: str
    description: str


# Historical name, kept because the members payload documents it.
RoleOptionDTO = LabeledOptionDTO


class LessonSlotDTO(BaseModel):
    """One bell-time row: lesson N runs from ``start_time`` to ``end_time``."""

    lesson_number: int
    start_time: str
    end_time: str

    @field_validator("start_time", "end_time")
    @classmethod
    def _valid_time(cls, v: str) -> str:
        if not _TIME_RE.match(v):
            raise ValueError("time must be HH:MM")
        return v


class LessonSlotsUpdateDTO(BaseModel):
    """The full set of bell times, replacing whatever is stored.

    Sent whole rather than patched: a timetable is only coherent as a set, and
    replacing it is how the bot's own editor behaves. Lesson numbers must be
    1..N with no gaps, and each lesson must start after the previous one ends —
    the same rules the onboarding dialogue enforces.
    """

    slots: List[LessonSlotDTO]

    @model_validator(mode="after")
    def _check_sequence(self) -> "LessonSlotsUpdateDTO":
        if not self.slots:
            raise ValueError("at least one lesson slot is required")
        if len(self.slots) > 10:
            raise ValueError("at most 10 lessons per day")
        numbers = [slot.lesson_number for slot in self.slots]
        if numbers != list(range(1, len(numbers) + 1)):
            raise ValueError("lesson numbers must be 1..N without gaps")
        for slot in self.slots:
            if slot.end_time <= slot.start_time:
                raise ValueError(
                    f"lesson {slot.lesson_number}: end time must be after start time"
                )
        for previous, current in zip(self.slots, self.slots[1:]):
            if current.start_time < previous.end_time:
                raise ValueError(
                    f"lesson {current.lesson_number} starts before lesson "
                    f"{previous.lesson_number} ends"
                )
        return self


class ScheduleDayLessonDTO(BaseModel):
    lesson_number: int
    subject_name: Optional[str] = None  # blank/None = no lesson in this slot


class ScheduleDayUpdateDTO(BaseModel):
    """Subjects for one weekday of one weekly template."""

    lessons: List[ScheduleDayLessonDTO] = []


class ScheduleTemplateDayDTO(BaseModel):
    weekday: int
    lessons: List[ScheduleDayLessonDTO] = []


class ScheduleTemplateDTO(BaseModel):
    """The editable weekly template: bell times + subjects per weekday.

    Distinct from ``ScheduleRangeDTO``, which is the *effective* schedule for
    real dates (template + A/B week + per-date overrides applied). This one is
    what you edit; that one is what you get.
    """

    week_type: str
    week_mode: bool
    week_types: List[str] = []
    slots: List[LessonSlotDTO] = []
    days: List[ScheduleTemplateDayDTO] = []
    can_edit: bool = False


class DayOverrideDTO(BaseModel):
    day_type: Optional[str] = None  # free | holiday | vacation | remote
    day_type_label: Optional[str] = None
    note: Optional[str] = None


class LessonOverrideDTO(BaseModel):
    lesson_number: int
    action: str  # cancel | set
    subject_name: Optional[str] = None
    start_time: Optional[str] = None
    end_time: Optional[str] = None
    note: Optional[str] = None


class DateOverridesDTO(BaseModel):
    """Everything that has been changed for one specific date."""

    date: datetime.date
    day: Optional[DayOverrideDTO] = None
    lessons: List[LessonOverrideDTO] = []
    day_type_options: List[RoleOptionDTO] = []
    can_edit: bool = False


class DayOverrideUpdateDTO(BaseModel):
    """``day_type=None`` clears the whole-day setting (back to a normal day)."""

    day_type: Optional[str] = None
    note: Optional[str] = Field(default=None, max_length=300)


class LessonOverrideUpdateDTO(BaseModel):
    """One per-date lesson change.

    ``cancel`` needs nothing else; ``set`` replaces the subject and/or the time,
    so at least one of those must be present — an empty ``set`` would silently do
    nothing and leave the user wondering.
    """

    action: str
    subject_name: Optional[str] = None
    start_time: Optional[str] = None
    end_time: Optional[str] = None
    note: Optional[str] = Field(default=None, max_length=300)

    @field_validator("start_time", "end_time")
    @classmethod
    def _valid_time(cls, v: Optional[str]) -> Optional[str]:
        if v is not None and not _TIME_RE.match(v):
            raise ValueError("time must be HH:MM")
        return v

    @model_validator(mode="after")
    def _check_action(self) -> "LessonOverrideUpdateDTO":
        if self.action not in ("cancel", "set"):
            raise ValueError("action must be 'cancel' or 'set'")
        if self.action == "set" and not any(
            (self.subject_name and self.subject_name.strip(), self.start_time)
        ):
            raise ValueError("a 'set' change needs a subject and/or a start time")
        if self.start_time and self.end_time and self.end_time <= self.start_time:
            raise ValueError("end time must be after start time")
        return self


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
    # Payments exist only in the tutor profile; the frontend hides this row
    # elsewhere (``ClassSettingsDTO.features.payments``).
    payment_reminder_enabled: bool = True
    payment_reminder_time: str = "10:00"
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
    payment_reminder_enabled: Optional[bool] = None
    payment_reminder_time: Optional[str] = None
    quiet_start: Optional[str] = None
    quiet_end: Optional[str] = None
    clear_quiet_hours: bool = False

    @field_validator(
        "hw_reminder_time", "schedule_reminder_time", "hw_duetoday_time",
        "payment_reminder_time", "quiet_start", "quiet_end",
    )
    @classmethod
    def _valid_time(cls, v: Optional[str]) -> Optional[str]:
        if v is not None and not _TIME_RE.match(v):
            raise ValueError("time must be HH:MM")
        return v


class ProfileOptionDTO(BaseModel):
    """One choice of "what is this chat for" (see ``services.profiles``)."""

    name: str
    label: str
    description: str


class ProfileFeaturesDTO(BaseModel):
    """Which parts of the app this chat's profile uses.

    The frontend hides what is off rather than disabling it — an empty screen
    nobody can use is worse than no screen. These are *presentation* flags: they
    never grant or remove a permission (that is ``PermissionsDTO``).
    """

    school_schedule: bool
    homework: bool
    extra_activities: bool
    homework_policy: bool


class TimezoneOptionDTO(BaseModel):
    """One entry of the friendly timezone picker (``name`` is the IANA id)."""

    name: str
    label: str


class ClassSettingsDTO(BaseModel):
    """Chat-wide settings that are not reminders: the class display name, the
    timezone every user-visible date is computed in, and who may edit homework.

    ``can_edit`` is server-computed (admin-only in a group, unrestricted in a
    private chat); the mutation endpoint re-checks it rather than trusting it.
    ``local_time`` / ``timezone_label`` are rendered here so the frontend never
    has to reimplement timezone maths — it shows what the server computed.
    """

    chat_id: int
    chat_type: str
    title: Optional[str] = None
    # The profile in effect. Never null: a chat that was never asked resolves to
    # the one its chat type implies (``services.profiles.resolve``).
    profile: str
    profile_label: str
    profile_options: List[ProfileOptionDTO] = []
    features: ProfileFeaturesDTO
    timezone: str
    timezone_label: str
    local_time: str
    hw_edit_policy: str
    per_student_homework: bool = False
    can_edit: bool = False
    # Shipped with the settings so the frontend never hardcodes zone names — the
    # picker stays identical to the bot's (services.timeservice.POPULAR_TIMEZONES).
    # Any other IANA name may still be typed in; the server validates it.
    timezone_options: List[TimezoneOptionDTO] = []


class ClassSettingsUpdateDTO(BaseModel):
    """Partial update. An empty/blank ``title`` clears the class name (an
    explicit action), while omitting the field leaves it untouched."""

    # Cap mirrors ``utils.MAX_CHAT_TITLE_LEN`` — kept as a literal so this module
    # stays free of bot-side imports.
    title: Optional[str] = Field(default=None, max_length=100)
    timezone: Optional[str] = None
    hw_edit_policy: Optional[str] = None
    profile: Optional[str] = None
    per_student_homework: Optional[bool] = None

    @field_validator("timezone")
    @classmethod
    def _tz_not_blank(cls, v: Optional[str]) -> Optional[str]:
        # Validity against the IANA database is checked in the query layer
        # (``db.set_chat_timezone`` rejects unknown zones) — this only rejects
        # obvious junk early.
        if v is not None and not v.strip():
            raise ValueError("timezone must not be blank")
        return v


_PAYMENT_PERIODS = ("one_time", "monthly", "per_lesson")


class PaymentDTO(BaseModel):
    """One thing to be paid for.

    ``amount_minor`` is an integer in minor units (kopecks/cents) — money never
    travels as a float. ``amount_text`` is the same value already formatted for
    display, so every surface shows it identically and the client does no money
    maths of its own.
    """

    id: int
    title: str
    amount_minor: int
    currency: str
    amount_text: str
    due_date: datetime.date
    period: str
    period_label: str
    is_paid: bool
    paid_at: Optional[str] = None
    note: Optional[str] = None
    remind_days_before: int
    status: str  # paid | due_soon | overdue | upcoming
    can_edit: bool = False


class PaymentCreateDTO(BaseModel):
    title: str
    amount_minor: int = Field(ge=0)
    due_date: datetime.date
    currency: str = Field(default="UAH", max_length=16)
    period: str = "one_time"
    note: Optional[str] = Field(default=None, max_length=300)
    remind_days_before: int = Field(default=1, ge=0, le=30)

    @field_validator("title")
    @classmethod
    def _title_not_blank(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("title must not be blank")
        return v

    @field_validator("period")
    @classmethod
    def _known_period(cls, v: str) -> str:
        if v not in _PAYMENT_PERIODS:
            raise ValueError("period must be one_time, monthly or per_lesson")
        return v


class PaymentUpdateDTO(BaseModel):
    """Partial update — send only what changed."""

    title: Optional[str] = None
    amount_minor: Optional[int] = Field(default=None, ge=0)
    due_date: Optional[datetime.date] = None
    currency: Optional[str] = Field(default=None, max_length=16)
    period: Optional[str] = None
    note: Optional[str] = Field(default=None, max_length=300)
    remind_days_before: Optional[int] = Field(default=None, ge=0, le=30)

    @field_validator("title")
    @classmethod
    def _title_not_blank(cls, v: Optional[str]) -> Optional[str]:
        if v is not None and not v.strip():
            raise ValueError("title must not be blank")
        return v

    @field_validator("period")
    @classmethod
    def _known_period(cls, v: Optional[str]) -> Optional[str]:
        if v is not None and v not in _PAYMENT_PERIODS:
            raise ValueError("period must be one_time, monthly or per_lesson")
        return v


class PaymentPaidDTO(BaseModel):
    is_paid: bool = True


class MemberDTO(BaseModel):
    """One person with access to a class.

    Minimal PII by design: a Telegram id and a display name, the same two fields
    the app already stores. ``role`` is the effective role — including the
    reported Telegram-derived ones when the chat has not switched to app roles —
    while ``app_role`` is only what was explicitly assigned.
    """

    user_id: int
    display_name: Optional[str] = None
    role: str
    role_label: str
    app_role: Optional[str] = None
    is_owner: bool = False
    is_self: bool = False


class MemberRoleUpdateDTO(BaseModel):
    """``None`` clears the assigned role (back to "no role"; a viewer in role mode)."""

    app_role: Optional[str] = None


class MembersPageDTO(BaseModel):
    members: List[MemberDTO] = []
    access_mode: str
    access_mode_label: str
    access_mode_options: List[RoleOptionDTO] = []
    assignable_roles: List[RoleOptionDTO] = []
    can_manage: bool = False


class InviteCreateDTO(BaseModel):
    app_role: str
    # Kept short by default: an invite is a credential, not a permanent link.
    ttl_hours: int = Field(default=24, ge=1, le=720)


class InviteDTO(BaseModel):
    """An invitation. ``url`` and ``token`` are present **only** in the response
    that created it — the raw token is never stored, so it can never be shown
    again. Listing existing invites returns everything except those two."""

    id: int
    app_role: str
    role_label: str
    created_at: str
    expires_at: str
    created_by_name: Optional[str] = None
    token: Optional[str] = None
    url: Optional[str] = None


class InviteAcceptDTO(BaseModel):
    token: str


class InviteAcceptedDTO(BaseModel):
    chat_id: int
    title: Optional[str] = None
    app_role: str
    role_label: str


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

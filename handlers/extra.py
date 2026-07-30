"""
"🎯 Доп. занятия" — supplementary activities (clubs, tutors, sections, extra
classes such as English at 18:00). These are deliberately kept separate from
the regular school schedule (LessonSlot/Schedule):

  * viewing is open to everyone;
  * in a group/supergroup, only admins may add/edit/delete — private chats have
    no such restriction (see middleware.access);
  * both recurring (weekly, by weekday) and one-off (dated) activities;
  * fields: title, weekday-or-date, start (and optional end) time, optional
    location and optional note.

The pure formatting/filter helpers (``format_extra_activities_block``,
``activities_on_date``, ``activities_for_weekday``) are imported by the Today
screen, the day-schedule view and the scheduler so those places can show a
dedicated "Доп. занятия" block. They never touch the DB or network.
"""
import datetime
from typing import List, Optional

from aiogram import Router, F
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State
from aiogram.types import CallbackQuery, Message

from database.db import (
    add_extra_activity, get_extra_activities, get_extra_activity_by_id,
    update_extra_activity, delete_extra_activity, set_extra_activity_reminder,
)
from database.models import ExtraActivity
from keyboards.inline import (
    DAYS_RU, get_extra_list_keyboard, get_extra_kind_keyboard,
    get_extra_day_keyboard, get_extra_action_keyboard,
    get_extra_edit_menu_keyboard, get_extra_delete_confirm_keyboard,
    get_extra_reminder_keyboard, get_cancel_keyboard,
)
from keyboards.reply import get_main_menu
from middleware.access import require_admin, is_chat_admin
import services.audit as audit
import services.timeservice as ts
from utils import (
    html_escape, safe_edit_text, safe_callback_ints, next_occurrence,
    parse_activity_time, MAX_TITLE_LEN, MAX_LOCATION_LEN, MAX_NOTE_LEN,
)

router = Router()

NON_TEXT_HINT = "🤔 Мне нужен текст. Пожалуйста, отправь сообщение текстом (или нажми «❌ Отмена»)."
STALE_BUTTON_TEXT = "⚠️ Эта кнопка устарела, открой список заново."
SKIP_WORDS = {"skip", "пропустить", "-", "нет", "нету", "—"}


class ExtraActivityStates(StatesGroup):
    waiting_for_kind = State()
    waiting_for_day = State()
    waiting_for_date = State()
    waiting_for_title = State()
    waiting_for_time = State()
    waiting_for_location = State()
    waiting_for_note = State()
    waiting_for_edit_value = State()
    waiting_for_reminder_minutes = State()


# --- Pure rendering / filtering helpers (reused by today/schedule/scheduler) --

def format_extra_activity_line(a: ExtraActivity, with_date: bool = False) -> str:
    """One activity as a compact HTML line. All user text is escaped."""
    time_part = a.start_time
    if a.end_time:
        time_part += f" - {a.end_time}"
    line = f"🎯 <b>{html_escape(a.title)}</b> — <code>{time_part}</code>"
    if with_date and a.kind == "once" and a.activity_date is not None:
        line += f" ({a.activity_date.strftime('%d.%m')})"
    if a.location:
        line += f"\n   📍 {html_escape(a.location)}"
    if a.note:
        line += f"\n   📝 {html_escape(a.note)}"
    return line


def format_extra_activities_block(activities: List[ExtraActivity], with_date: bool = False) -> str:
    """A titled block for the given activities, or "" when there are none."""
    if not activities:
        return ""
    lines = [format_extra_activity_line(a, with_date) for a in activities]
    return "🎯 <b>Доп. занятия:</b>\n" + "\n".join(lines)


def activities_on_date(activities: List[ExtraActivity], date: datetime.date) -> List[ExtraActivity]:
    """Activities that apply on a concrete ``date`` (weekly by weekday + once by date)."""
    weekday = date.weekday()
    matched = [
        a for a in activities
        if (a.kind == "weekly" and a.day_of_week == weekday)
        or (a.kind == "once" and a.activity_date == date)
    ]
    return sorted(matched, key=lambda a: a.start_time)


def activities_for_weekday(
    activities: List[ExtraActivity], day_of_week: int, today: datetime.date
) -> List[ExtraActivity]:
    """
    Activities for a weekday view: all weekly ones on ``day_of_week`` plus any
    upcoming (today-or-later) one-off activities whose date falls on that
    weekday, so a dated activity still surfaces on the right day tab.
    """
    matched = []
    for a in activities:
        if a.kind == "weekly" and a.day_of_week == day_of_week:
            matched.append(a)
        elif (
            a.kind == "once"
            and a.activity_date is not None
            and a.activity_date >= today
            and a.activity_date.weekday() == day_of_week
        ):
            matched.append(a)
    return sorted(matched, key=lambda a: a.start_time)


def _sort_key(a: ExtraActivity):
    # weekly first (grouped by weekday), then dated one-offs by date; ties by time.
    return (
        0 if a.kind == "weekly" else 1,
        a.day_of_week if a.day_of_week is not None else 99,
        a.activity_date or datetime.date.max,
        a.start_time,
    )


def _when_label(a: ExtraActivity) -> str:
    if a.kind == "weekly" and a.day_of_week is not None:
        return DAYS_RU[a.day_of_week]
    if a.kind == "once" and a.activity_date is not None:
        return f"разово {a.activity_date.strftime('%d.%m')}"
    return "—"


def _reminder_label(a: ExtraActivity) -> str:
    if not a.reminder_enabled:
        return "выкл"
    if a.reminder_minutes == 0:
        return "в начале занятия"
    return f"за {a.reminder_minutes} мин до начала"


def _author_line(a: ExtraActivity, tz=None) -> str:
    """
    Who added the activity (and who last changed it, when that differs).
    Activities created before authorship existed have no author and say so
    rather than being attributed to whoever edits them next. ``tz`` is the chat's
    timezone, so the stored UTC timestamps are shown in the chat's own time.
    """
    if a.created_by_user_id is None and not a.created_by_name:
        return "👤 Автор неизвестен"
    created = audit.actor_label(a.created_by_user_id, a.created_by_name)
    line = f"👤 Добавил(а): <b>{html_escape(created)}</b> · {audit.format_ts(a.created_at, tz)}"
    if a.updated_at and a.updated_at != a.created_at:
        updated = audit.actor_label(a.updated_by_user_id, a.updated_by_name)
        line += f"\n✏️ Изменил(а): <b>{html_escape(updated)}</b> · {audit.format_ts(a.updated_at, tz)}"
    return line


def _detail_text(a: ExtraActivity, tz=None) -> str:
    time_part = a.start_time + (f" - {a.end_time}" if a.end_time else "")
    lines = [
        f"🎯 <b>{html_escape(a.title)}</b>",
        f"📅 {_when_label(a)}",
        f"🕒 {time_part}",
    ]
    if a.location:
        lines.append(f"📍 {html_escape(a.location)}")
    if a.note:
        lines.append(f"📝 {html_escape(a.note)}")
    lines.append(f"🔔 Напоминание: {_reminder_label(a)}")
    lines.append(_author_line(a, tz))
    return "\n".join(lines)


async def _can_manage(event, bot) -> bool:
    """True if the acting user may add/edit/delete in this chat."""
    # Duck-typed so it works for both Message and CallbackQuery: a callback
    # carries the chat under ``.message.chat``, a message under ``.chat``.
    message = getattr(event, "message", None)
    chat = message.chat if message is not None else event.chat
    return await is_chat_admin(bot, chat.id, event.from_user.id, chat.type)


async def format_extra_list(chat_id: int) -> str:
    activities = sorted(await get_extra_activities(chat_id), key=_sort_key)
    if not activities:
        return (
            "🎯 <b>Доп. занятия</b>\n\n"
            "Пока нет ни одного дополнительного занятия (кружки, репетиторы, секции).\n"
            "Здесь можно добавить, например, английский по вторникам в 18:00."
        )
    text = "🎯 <b>Доп. занятия</b>\n\n"
    for a in activities:
        time_part = a.start_time + (f" - {a.end_time}" if a.end_time else "")
        text += f"🎯 <b>{html_escape(a.title)}</b> — {_when_label(a)}, <code>{time_part}</code>"
        if a.location:
            text += f"\n   📍 {html_escape(a.location)}"
        if a.note:
            text += f"\n   📝 {html_escape(a.note)}"
        text += "\n\n"
    return text.rstrip("\n")


async def _get_sorted_ids(chat_id: int) -> List[int]:
    return [a.id for a in sorted(await get_extra_activities(chat_id), key=_sort_key)]


async def _show_list_via_edit(callback: CallbackQuery, can_manage: bool):
    chat_id = callback.message.chat.id
    activities = sorted(await get_extra_activities(chat_id), key=_sort_key)
    text = await format_extra_list(chat_id)
    kb = get_extra_list_keyboard(activities, can_manage=can_manage)
    await safe_edit_text(callback.message, text, reply_markup=kb, parse_mode="HTML")


# --- View (everyone) --------------------------------------------------------

@router.message(Command("extra"))
@router.message(F.text == "🎯 Доп. занятия")
async def show_extra(message: Message, state: FSMContext):
    await state.clear()
    can_manage = await _can_manage(message, message.bot)
    activities = sorted(await get_extra_activities(message.chat.id), key=_sort_key)
    text = await format_extra_list(message.chat.id)
    await message.answer(
        text,
        reply_markup=get_extra_list_keyboard(activities, can_manage=can_manage),
        parse_mode="HTML",
    )


@router.callback_query(F.data == "ea_list")
async def process_extra_list(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    can_manage = await _can_manage(callback, callback.bot)
    await _show_list_via_edit(callback, can_manage)
    await callback.answer()


@router.callback_query(F.data == "ea_cancel")
async def process_extra_cancel(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    can_manage = await _can_manage(callback, callback.bot)
    await _show_list_via_edit(callback, can_manage)
    await callback.answer("Отменено.")


@router.callback_query(F.data.startswith("ea_view:"))
async def process_extra_view(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    ints = safe_callback_ints(callback.data, 1)
    if ints is None:
        await callback.answer(STALE_BUTTON_TEXT, show_alert=True)
        return
    activity_id = ints[0]
    activity = await get_extra_activity_by_id(callback.message.chat.id, activity_id)
    if activity is None:
        await _reject_missing(callback)
        return
    can_manage = await _can_manage(callback, callback.bot)
    await safe_edit_text(
        callback.message,
        _detail_text(activity, await ts.tz_for_chat_id(callback.message.chat.id)),
        reply_markup=get_extra_action_keyboard(activity_id, can_manage=can_manage),
        parse_mode="HTML",
    )
    await callback.answer()


async def _reject_missing(callback: CallbackQuery):
    await callback.answer("⚠️ Это занятие не найдено (возможно, уже удалено).", show_alert=True)
    can_manage = await _can_manage(callback, callback.bot)
    await _show_list_via_edit(callback, can_manage)


# --- Add flow (admin) -------------------------------------------------------

@router.callback_query(F.data == "ea_add")
async def process_extra_add(callback: CallbackQuery, state: FSMContext):
    if not await require_admin(callback, callback.bot):
        return
    await state.clear()
    await state.set_state(ExtraActivityStates.waiting_for_kind)
    await safe_edit_text(
        callback.message,
        "➕ <b>Новое доп. занятие</b>\n\nКак часто оно проходит?",
        reply_markup=get_extra_kind_keyboard(),
        parse_mode="HTML",
    )
    await callback.answer()


@router.callback_query(ExtraActivityStates.waiting_for_kind, F.data.startswith("ea_kind:"))
async def process_extra_kind(callback: CallbackQuery, state: FSMContext):
    kind = callback.data.split(":", 1)[1]
    if kind not in ("weekly", "once"):
        await callback.answer(STALE_BUTTON_TEXT, show_alert=True)
        return
    await state.update_data(kind=kind)
    if kind == "weekly":
        await state.set_state(ExtraActivityStates.waiting_for_day)
        await safe_edit_text(
            callback.message,
            "🔁 <b>Еженедельное занятие</b>\n\nВыбери день недели:",
            reply_markup=get_extra_day_keyboard("ea_day"),
            parse_mode="HTML",
        )
    else:
        await state.set_state(ExtraActivityStates.waiting_for_date)
        await safe_edit_text(
            callback.message,
            "📆 <b>Разовое занятие</b>\n\n"
            "Введи дату в формате <code>ДД.ММ</code> (например, <code>14.10</code>):",
            reply_markup=get_cancel_keyboard(callback_data="ea_cancel"),
            parse_mode="HTML",
        )
    await callback.answer()


@router.callback_query(ExtraActivityStates.waiting_for_day, F.data.startswith("ea_day:"))
async def process_extra_day(callback: CallbackQuery, state: FSMContext):
    ints = safe_callback_ints(callback.data, 1)
    if ints is None or not (0 <= ints[0] <= 6):
        await callback.answer(STALE_BUTTON_TEXT, show_alert=True)
        return
    await state.update_data(day_of_week=ints[0])
    await state.set_state(ExtraActivityStates.waiting_for_title)
    await safe_edit_text(
        callback.message,
        f"📅 День: <b>{DAYS_RU[ints[0]]}</b>\n\nВведи название занятия (например, «Английский»):",
        reply_markup=get_cancel_keyboard(callback_data="ea_cancel"),
        parse_mode="HTML",
    )
    await callback.answer()


@router.message(ExtraActivityStates.waiting_for_date, F.text)
async def process_extra_date(message: Message, state: FSMContext):
    text = message.text.strip()
    try:
        day, month = map(int, text.split("."))
        today = await ts.today_for_chat_id(message.chat.id)
        activity_date = next_occurrence(month, day, today)
    except ValueError:
        await message.answer(
            "Неверный формат даты! Укажи дату в формате <code>ДД.ММ</code> (например, <code>14.10</code>):",
            parse_mode="HTML",
        )
        return
    await state.update_data(activity_date=activity_date.isoformat())
    await state.set_state(ExtraActivityStates.waiting_for_title)
    await message.answer(
        f"📆 Дата: <b>{activity_date.strftime('%d.%m.%Y')}</b>\n\n"
        "Введи название занятия (например, «Репетитор по математике»):",
        parse_mode="HTML",
    )


@router.message(ExtraActivityStates.waiting_for_title, F.text)
async def process_extra_title(message: Message, state: FSMContext):
    title = message.text.strip()
    if not title:
        await message.answer("Название не может быть пустым. Введи название занятия:")
        return
    if len(title) > MAX_TITLE_LEN:
        await message.answer(f"Слишком длинное название (макс. {MAX_TITLE_LEN} символов). Введи короче:")
        return
    await state.update_data(title=title)
    await state.set_state(ExtraActivityStates.waiting_for_time)
    await message.answer(
        f"🏷 Название: <b>{html_escape(title)}</b>\n\n"
        "Введи время. Можно только начало (<code>18:00</code>) "
        "или интервал (<code>18:00 - 19:00</code>):",
        parse_mode="HTML",
    )


@router.message(ExtraActivityStates.waiting_for_time, F.text)
async def process_extra_time(message: Message, state: FSMContext):
    try:
        start, end = parse_activity_time(message.text.strip())
    except ValueError as e:
        await message.answer(f"⚠️ {html_escape(str(e))}", parse_mode="HTML")
        return
    await state.update_data(start_time=start, end_time=end)
    await state.set_state(ExtraActivityStates.waiting_for_location)
    await message.answer(
        "📍 Введи место проведения (или напиши <code>-</code>, чтобы пропустить):",
        parse_mode="HTML",
    )


@router.message(ExtraActivityStates.waiting_for_location, F.text)
async def process_extra_location(message: Message, state: FSMContext):
    text = message.text.strip()
    location: Optional[str] = None if text.lower() in SKIP_WORDS else text
    if location is not None and len(location) > MAX_LOCATION_LEN:
        await message.answer(f"Слишком длинное место (макс. {MAX_LOCATION_LEN} символов). Введи короче:")
        return
    await state.update_data(location=location)
    await state.set_state(ExtraActivityStates.waiting_for_note)
    await message.answer(
        "📝 Введи примечание (или напиши <code>-</code>, чтобы пропустить):",
        parse_mode="HTML",
    )


@router.message(ExtraActivityStates.waiting_for_note, F.text)
async def process_extra_note(message: Message, state: FSMContext):
    text = message.text.strip()
    note: Optional[str] = None if text.lower() in SKIP_WORDS else text
    if note is not None and len(note) > MAX_NOTE_LEN:
        await message.answer(f"Слишком длинное примечание (макс. {MAX_NOTE_LEN} символов). Введи короче:")
        return

    data = await state.get_data()
    kind = data["kind"]
    activity_date = (
        datetime.date.fromisoformat(data["activity_date"])
        if data.get("activity_date") else None
    )
    actor_user_id, actor_name = audit.actor_from(message)
    activity = await add_extra_activity(
        message.chat.id,
        title=data["title"],
        kind=kind,
        start_time=data["start_time"],
        day_of_week=data.get("day_of_week"),
        activity_date=activity_date,
        end_time=data.get("end_time"),
        location=data.get("location"),
        note=note,
        actor_user_id=actor_user_id,
        actor_name=actor_name,
    )
    await audit.record_event(
        message, message.chat.id, audit.ENTITY_EXTRA, audit.ACTION_CREATE,
        entity_id=activity.id,
        summary=audit.summarize(data["title"], _when_label(activity), data["start_time"]),
    )
    await state.clear()

    await message.answer("✅ Доп. занятие сохранено!", reply_markup=get_main_menu())
    activities = sorted(await get_extra_activities(message.chat.id), key=_sort_key)
    await message.answer(
        await format_extra_list(message.chat.id),
        reply_markup=get_extra_list_keyboard(activities, can_manage=True),
        parse_mode="HTML",
    )


# --- Delete flow (admin) ----------------------------------------------------

@router.callback_query(F.data.startswith("ea_delete_ask:"))
async def process_extra_delete_ask(callback: CallbackQuery):
    if not await require_admin(callback, callback.bot):
        return
    ints = safe_callback_ints(callback.data, 1)
    if ints is None:
        await callback.answer(STALE_BUTTON_TEXT, show_alert=True)
        return
    activity = await get_extra_activity_by_id(callback.message.chat.id, ints[0])
    if activity is None:
        await _reject_missing(callback)
        return
    await safe_edit_text(
        callback.message,
        f"❗ Удалить занятие «{html_escape(activity.title)}» безвозвратно?",
        reply_markup=get_extra_delete_confirm_keyboard(ints[0]),
        parse_mode="HTML",
    )
    await callback.answer()


@router.callback_query(F.data.startswith("ea_delete_confirm:"))
async def process_extra_delete_confirm(callback: CallbackQuery):
    if not await require_admin(callback, callback.bot):
        return
    ints = safe_callback_ints(callback.data, 1)
    if ints is None:
        await callback.answer(STALE_BUTTON_TEXT, show_alert=True)
        return
    # Read the title before deleting so the (surviving) audit entry can name it.
    activity = await get_extra_activity_by_id(callback.message.chat.id, ints[0])
    ok = await delete_extra_activity(callback.message.chat.id, ints[0])
    if ok:
        await audit.record_event(
            callback, callback.message.chat.id, audit.ENTITY_EXTRA, audit.ACTION_DELETE,
            entity_id=ints[0],
            summary=audit.summarize(activity.title if activity else None),
        )
    await callback.answer("Занятие удалено." if ok else "⚠️ Это занятие уже не существует.", show_alert=not ok)
    can_manage = await _can_manage(callback, callback.bot)
    await _show_list_via_edit(callback, can_manage)


# --- Edit flow (admin) ------------------------------------------------------

@router.callback_query(F.data.startswith("ea_edit_menu:"))
async def process_extra_edit_menu(callback: CallbackQuery, state: FSMContext):
    if not await require_admin(callback, callback.bot):
        return
    await state.clear()
    ints = safe_callback_ints(callback.data, 1)
    if ints is None:
        await callback.answer(STALE_BUTTON_TEXT, show_alert=True)
        return
    activity = await get_extra_activity_by_id(callback.message.chat.id, ints[0])
    if activity is None:
        await _reject_missing(callback)
        return
    await safe_edit_text(
        callback.message,
        f"✏️ <b>Редактирование</b>\n\n"
        f"{_detail_text(activity, await ts.tz_for_chat_id(callback.message.chat.id))}\n\n"
        "Что изменить?",
        reply_markup=get_extra_edit_menu_keyboard(ints[0], activity.kind),
        parse_mode="HTML",
    )
    await callback.answer()


# Field labels for audit summaries — the journal records which field changed,
# never the new value.
EDIT_FIELD_AUDIT_LABELS = {
    "title": "название", "time": "время", "date": "дата",
    "location": "место", "note": "примечание",
}

EDIT_FIELD_PROMPTS = {
    "title": "Введи новое название занятия:",
    "time": "Введи новое время (<code>18:00</code> или <code>18:00 - 19:00</code>):",
    "location": "Введи новое место (или <code>-</code>, чтобы очистить):",
    "note": "Введи новое примечание (или <code>-</code>, чтобы очистить):",
}


@router.callback_query(F.data.startswith("ea_edit_field:"))
async def process_extra_edit_field(callback: CallbackQuery, state: FSMContext):
    if not await require_admin(callback, callback.bot):
        return
    parts = callback.data.split(":")
    ints = safe_callback_ints(callback.data, 1)
    if ints is None or len(parts) < 3 or parts[2] not in ("title", "when", "time", "location", "note"):
        await callback.answer(STALE_BUTTON_TEXT, show_alert=True)
        return
    activity_id = ints[0]
    field = parts[2]

    activity = await get_extra_activity_by_id(callback.message.chat.id, activity_id)
    if activity is None:
        await _reject_missing(callback)
        return

    # "when" edits the recurrence anchor: a weekday picker for weekly, a date
    # prompt for one-off. Everything else is a plain text prompt.
    if field == "when":
        if activity.kind == "weekly":
            await safe_edit_text(
                callback.message,
                "📅 Выбери новый день недели:",
                reply_markup=get_extra_day_keyboard(f"ea_setday:{activity_id}"),
                parse_mode="HTML",
            )
            await callback.answer()
            return
        field = "date"  # once → edit the date via text

    await state.update_data(edit_id=activity_id, edit_field=field)
    await state.set_state(ExtraActivityStates.waiting_for_edit_value)
    prompt = (
        "Введи новую дату в формате <code>ДД.ММ</code> (например, <code>14.10</code>):"
        if field == "date" else EDIT_FIELD_PROMPTS[field]
    )
    await safe_edit_text(
        callback.message,
        prompt,
        reply_markup=get_cancel_keyboard(callback_data=f"ea_edit_menu:{activity_id}"),
        parse_mode="HTML",
    )
    await callback.answer()


@router.callback_query(F.data.startswith("ea_setday:"))
async def process_extra_set_day(callback: CallbackQuery):
    if not await require_admin(callback, callback.bot):
        return
    ints = safe_callback_ints(callback.data, 1, 2)
    if ints is None or not (0 <= ints[1] <= 6):
        await callback.answer(STALE_BUTTON_TEXT, show_alert=True)
        return
    activity_id, day = ints
    actor_user_id, actor_name = audit.actor_from(callback)
    ok = await update_extra_activity(
        callback.message.chat.id, activity_id, day_of_week=day,
        actor_user_id=actor_user_id, actor_name=actor_name,
    )
    if not ok:
        await _reject_missing(callback)
        return
    activity = await get_extra_activity_by_id(callback.message.chat.id, activity_id)
    await audit.record_event(
        callback, callback.message.chat.id, audit.ENTITY_EXTRA, audit.ACTION_UPDATE,
        entity_id=activity_id,
        summary=audit.summarize(activity.title if activity else None, "поля: день недели"),
    )
    await safe_edit_text(
        callback.message,
        f"✅ День обновлён.\n\n"
        f"{_detail_text(activity, await ts.tz_for_chat_id(callback.message.chat.id))}",
        reply_markup=get_extra_action_keyboard(activity_id, can_manage=True),
        parse_mode="HTML",
    )
    await callback.answer()


@router.message(ExtraActivityStates.waiting_for_edit_value, F.text)
async def process_extra_edit_value(message: Message, state: FSMContext):
    data = await state.get_data()
    activity_id = data["edit_id"]
    field = data["edit_field"]
    text = message.text.strip()

    values = {}
    if field == "title":
        if not text:
            await message.answer("Название не может быть пустым. Введи название занятия:")
            return
        if len(text) > MAX_TITLE_LEN:
            await message.answer(f"Слишком длинное название (макс. {MAX_TITLE_LEN} символов). Введи короче:")
            return
        values["title"] = text
    elif field == "time":
        try:
            start, end = parse_activity_time(text)
        except ValueError as e:
            await message.answer(f"⚠️ {html_escape(str(e))}", parse_mode="HTML")
            return
        values["start_time"] = start
        values["end_time"] = end
    elif field == "date":
        try:
            day, month = map(int, text.split("."))
            today = await ts.today_for_chat_id(message.chat.id)
            values["activity_date"] = next_occurrence(month, day, today)
        except ValueError:
            await message.answer(
                "Неверный формат даты! Укажи дату в формате <code>ДД.ММ</code> (например, <code>14.10</code>):",
                parse_mode="HTML",
            )
            return
    elif field == "location":
        location = None if text.lower() in SKIP_WORDS else text
        if location is not None and len(location) > MAX_LOCATION_LEN:
            await message.answer(f"Слишком длинное место (макс. {MAX_LOCATION_LEN} символов). Введи короче:")
            return
        values["location"] = location
    else:  # note
        note = None if text.lower() in SKIP_WORDS else text
        if note is not None and len(note) > MAX_NOTE_LEN:
            await message.answer(f"Слишком длинное примечание (макс. {MAX_NOTE_LEN} символов). Введи короче:")
            return
        values["note"] = note

    actor_user_id, actor_name = audit.actor_from(message)
    updated = await update_extra_activity(
        message.chat.id, activity_id,
        actor_user_id=actor_user_id, actor_name=actor_name, **values,
    )
    await state.clear()
    if not updated:
        await message.answer(
            "⚠️ Это занятие уже не существует (возможно, было удалено).",
            reply_markup=get_main_menu(),
        )
    else:
        activity = await get_extra_activity_by_id(message.chat.id, activity_id)
        await audit.record_event(
            message, message.chat.id, audit.ENTITY_EXTRA, audit.ACTION_UPDATE,
            entity_id=activity_id,
            summary=audit.summarize(
                activity.title if activity else None,
                audit.fields_summary([EDIT_FIELD_AUDIT_LABELS.get(field, field)]),
            ),
        )
        await message.answer("✅ Занятие обновлено!", reply_markup=get_main_menu())

    activities = sorted(await get_extra_activities(message.chat.id), key=_sort_key)
    await message.answer(
        await format_extra_list(message.chat.id),
        reply_markup=get_extra_list_keyboard(activities, can_manage=True),
        parse_mode="HTML",
    )


# --- Per-activity reminder config (admin) -----------------------------------

async def _show_reminder_menu(callback: CallbackQuery, activity: ExtraActivity, note: str = None):
    text = (
        f"🔔 <b>Напоминание — {html_escape(activity.title)}</b>\n\n"
        f"Текущее: <b>{_reminder_label(activity)}</b>.\n\n"
        "Бот пришлёт напоминание перед началом занятия. Можно выбрать, за сколько "
        "минут (0 — в момент начала). Общий выключатель напоминаний о доп. занятиях — "
        "в разделе «⚙️ Настройки»."
    )
    await safe_edit_text(
        callback.message, text,
        reply_markup=get_extra_reminder_keyboard(activity), parse_mode="HTML",
    )
    await callback.answer(note) if note else await callback.answer()


@router.callback_query(F.data.startswith("ea_rem_menu:"))
async def process_extra_reminder_menu(callback: CallbackQuery, state: FSMContext):
    if not await require_admin(callback, callback.bot):
        return
    await state.clear()
    ints = safe_callback_ints(callback.data, 1)
    if ints is None:
        await callback.answer(STALE_BUTTON_TEXT, show_alert=True)
        return
    activity = await get_extra_activity_by_id(callback.message.chat.id, ints[0])
    if activity is None:
        await _reject_missing(callback)
        return
    await _show_reminder_menu(callback, activity)


@router.callback_query(F.data.startswith("ea_rem_toggle:"))
async def process_extra_reminder_toggle(callback: CallbackQuery):
    if not await require_admin(callback, callback.bot):
        return
    ints = safe_callback_ints(callback.data, 1)
    if ints is None:
        await callback.answer(STALE_BUTTON_TEXT, show_alert=True)
        return
    activity = await get_extra_activity_by_id(callback.message.chat.id, ints[0])
    if activity is None:
        await _reject_missing(callback)
        return
    actor_user_id, actor_name = audit.actor_from(callback)
    await set_extra_activity_reminder(
        callback.message.chat.id, ints[0], enabled=not activity.reminder_enabled,
        actor_user_id=actor_user_id, actor_name=actor_name,
    )
    activity = await get_extra_activity_by_id(callback.message.chat.id, ints[0])
    await audit.record_event(
        callback, callback.message.chat.id, audit.ENTITY_EXTRA, audit.ACTION_UPDATE,
        entity_id=ints[0],
        summary=audit.summarize(activity.title if activity else None, "поля: напоминание"),
    )
    await _show_reminder_menu(callback, activity)


@router.callback_query(F.data.startswith("ea_rem_min:"))
async def process_extra_reminder_minutes(callback: CallbackQuery):
    if not await require_admin(callback, callback.bot):
        return
    ints = safe_callback_ints(callback.data, 1, 2)
    if ints is None or not (0 <= ints[1] <= 10080):
        await callback.answer(STALE_BUTTON_TEXT, show_alert=True)
        return
    activity_id, minutes = ints
    actor_user_id, actor_name = audit.actor_from(callback)
    ok = await set_extra_activity_reminder(
        callback.message.chat.id, activity_id, enabled=True, minutes=minutes,
        actor_user_id=actor_user_id, actor_name=actor_name,
    )
    if not ok:
        await _reject_missing(callback)
        return
    activity = await get_extra_activity_by_id(callback.message.chat.id, activity_id)
    await audit.record_event(
        callback, callback.message.chat.id, audit.ENTITY_EXTRA, audit.ACTION_UPDATE,
        entity_id=activity_id,
        summary=audit.summarize(activity.title if activity else None, "поля: напоминание"),
    )
    await _show_reminder_menu(callback, activity, note="Сохранено.")


@router.callback_query(F.data.startswith("ea_rem_custom:"))
async def process_extra_reminder_custom(callback: CallbackQuery, state: FSMContext):
    if not await require_admin(callback, callback.bot):
        return
    ints = safe_callback_ints(callback.data, 1)
    if ints is None:
        await callback.answer(STALE_BUTTON_TEXT, show_alert=True)
        return
    await state.update_data(reminder_activity_id=ints[0])
    await state.set_state(ExtraActivityStates.waiting_for_reminder_minutes)
    await safe_edit_text(
        callback.message,
        "Введи, за сколько минут до начала напоминать (число от 0 до 10080, "
        "где 10080 — это неделя):",
        reply_markup=get_cancel_keyboard(callback_data=f"ea_rem_menu:{ints[0]}"),
        parse_mode="HTML",
    )
    await callback.answer()


@router.message(ExtraActivityStates.waiting_for_reminder_minutes, F.text)
async def process_extra_reminder_minutes_value(message: Message, state: FSMContext):
    text = message.text.strip()
    try:
        minutes = int(text)
    except ValueError:
        await message.answer("Нужно число от 0 до 10080. Попробуй ещё раз:")
        return
    if not (0 <= minutes <= 10080):
        await message.answer("Число должно быть от 0 до 10080 (0 — в начале, 10080 — за неделю). Введи ещё раз:")
        return
    data = await state.get_data()
    activity_id = data.get("reminder_activity_id")
    await state.clear()
    actor_user_id, actor_name = audit.actor_from(message)
    ok = await set_extra_activity_reminder(
        message.chat.id, activity_id, enabled=True, minutes=minutes,
        actor_user_id=actor_user_id, actor_name=actor_name,
    )
    if not ok:
        await message.answer("⚠️ Это занятие уже не существует.", reply_markup=get_main_menu())
        return
    await audit.record_event(
        message, message.chat.id, audit.ENTITY_EXTRA, audit.ACTION_UPDATE,
        entity_id=activity_id, summary=audit.summarize("поля: напоминание"),
    )
    label = "в начале занятия" if minutes == 0 else f"за {minutes} мин до начала"
    await message.answer(f"✅ Напоминание установлено: {label}.", reply_markup=get_main_menu())
    activities = sorted(await get_extra_activities(message.chat.id), key=_sort_key)
    await message.answer(
        await format_extra_list(message.chat.id),
        reply_markup=get_extra_list_keyboard(activities, can_manage=True),
        parse_mode="HTML",
    )


# --- Fallback: non-text content while a step expects text -------------------

async def extra_non_text(message: Message):
    await message.answer(NON_TEXT_HINT)


router.message.register(
    extra_non_text,
    StateFilter(
        ExtraActivityStates.waiting_for_date,
        ExtraActivityStates.waiting_for_title,
        ExtraActivityStates.waiting_for_time,
        ExtraActivityStates.waiting_for_location,
        ExtraActivityStates.waiting_for_note,
        ExtraActivityStates.waiting_for_edit_value,
        ExtraActivityStates.waiting_for_reminder_minutes,
    ),
)

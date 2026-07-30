"""
"🗓 Изменения по датам" — per-date changes that overlay the weekly schedule
template for one concrete calendar date, without ever mutating the template:

  * отмена отдельного урока (cancel a single lesson);
  * замена предмета (replace a subject);
  * изменение времени урока (change a lesson's time);
  * добавление разового урока (add a one-off lesson);
  * полностью свободный день / праздник / каникулы / дистанционный день;
  * необязательное примечание (optional note / reason).

Access: the whole section is admin-only in a group/supergroup (private chats
have no restriction) — every entry point calls ``require_admin``. Every change
is shown as a "сейчас → станет" preview before it is persisted, and clearing /
removing changes asks for confirmation. Extra activities are never touched and
are shown as their own block.

The effective (template + overrides) schedule itself is computed by the single
shared service in services/effective_schedule.py, which the "Сегодня" screen
and the reminders use too — this handler only edits the overrides and previews
the result.
"""
import datetime
from typing import Optional, Tuple

from aiogram import Router, F
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State
from aiogram.types import CallbackQuery, Message

from database.db import (
    get_lesson_slots, get_schedule, get_day_override, get_lesson_overrides,
    set_lesson_override, set_day_override, clear_day_override,
    clear_date_overrides, get_override_dates, get_extra_activities,
)
from database.models import DayOverride, LessonOverride
from handlers.extra import activities_on_date, format_extra_activities_block
from services.effective_schedule import (
    EffectiveDay, compute_effective_day, format_effective_schedule_body,
    resolve_week_type_for_chat,
)
from keyboards.inline import (
    DAYS_RU, get_date_grid_keyboard, get_date_editor_keyboard,
    get_lesson_pick_keyboard, get_day_type_keyboard,
    get_override_preview_keyboard, get_confirm_keyboard,
)
from middleware.access import require_admin
import services.audit as audit
import services.timeservice as ts
from utils import (
    html_escape, safe_edit_text, safe_callback_ints, parse_time_interval,
    MAX_SUBJECT_LEN, MAX_NOTE_LEN,
)

router = Router()

NON_TEXT_HINT = "🤔 Мне нужен текст. Пожалуйста, отправь сообщение текстом (или вернись в меню)."
STALE_BUTTON_TEXT = "⚠️ Эта кнопка устарела, открой раздел заново."
SKIP_WORDS = {"skip", "пропустить", "-", "нет", "нету", "—"}
NO_LESSONS_PLACEHOLDER = "— нет уроков —"

# Short labels used in audit summaries (what kind of change was saved).
LESSON_ACTION_LABELS = {"cancel": "отмена урока", "set": "замена/изменение"}
DAY_TYPE_AUDIT_LABELS = {
    "free": "свободный день", "holiday": "праздник",
    "vacation": "каникулы", "remote": "дистанционный день",
}


class DateOverrideStates(StatesGroup):
    waiting_for_subject = State()    # replace subject / add-lesson subject
    waiting_for_time = State()       # change an existing lesson's time
    waiting_for_add_time = State()   # add-lesson time (after its subject)
    waiting_for_day_note = State()   # optional reason for a day type


# --- Small helpers ----------------------------------------------------------

def _parse_iso(value: Optional[str]) -> Optional[datetime.date]:
    try:
        return datetime.date.fromisoformat(value) if value else None
    except (ValueError, TypeError):
        return None


async def _today(chat_id: int) -> datetime.date:
    """Today in *this chat's* timezone — the date picker must start on the
    user's own today, not the server's."""
    return await ts.today_for_chat_id(chat_id)


async def _load(chat_id: int, date: datetime.date):
    """Fetch the four effective-schedule inputs for one chat+date (scoped),
    selecting the correct weekly template (all / A / B) for this date."""
    week_type = await resolve_week_type_for_chat(chat_id, date)
    slots = await get_lesson_slots(chat_id)
    schedule_items = await get_schedule(chat_id, date.weekday(), week_type=week_type)
    day_override = await get_day_override(chat_id, date)
    lesson_overrides = await get_lesson_overrides(chat_id, date)
    return slots, schedule_items, day_override, lesson_overrides


def _sim(lesson_overrides, day_override, pending, chat_id, date):
    """Return (lesson_overrides, day_override) with the pending change merged in-memory."""
    lesson_overrides = list(lesson_overrides)
    if pending["kind"] == "lesson":
        num = pending["lesson_number"]
        lesson_overrides = [o for o in lesson_overrides if o.lesson_number != num]
        lesson_overrides.append(LessonOverride(
            chat_id=chat_id, date=date, lesson_number=num,
            action=pending["action"], subject_name=pending.get("subject"),
            start_time=pending.get("start"), end_time=pending.get("end"),
            note=pending.get("note"),
        ))
        return lesson_overrides, day_override
    # kind == "day"
    sim_day = DayOverride(
        chat_id=chat_id, date=date, day_type=pending["day_type"], note=pending.get("note")
    )
    return lesson_overrides, sim_day


def _body(eff: EffectiveDay) -> str:
    return format_effective_schedule_body(
        eff, per_subject_emoji=True, show_free=True, no_lessons_text=NO_LESSONS_PLACEHOLDER
    )


async def _extra_block(chat_id: int, date: datetime.date) -> str:
    extra = activities_on_date(await get_extra_activities(chat_id), date)
    return format_extra_activities_block(extra, with_date=False)


async def _render_editor(chat_id: int, date: datetime.date) -> Tuple[str, object]:
    slots, sched, day_ovr, lesson_ovr = await _load(chat_id, date)
    eff = compute_effective_day(date, slots, sched, day_ovr, lesson_ovr)

    day_name = DAYS_RU[date.weekday()]
    text = f"🗓 <b>Изменения — {day_name}, {date.strftime('%d.%m.%Y')}</b>\n\n"
    text += "📋 <b>Итоговое расписание:</b>\n" + _body(eff)
    if eff.has_changes:
        text += "\n\n✏️ <i>Есть изменения относительно обычного расписания.</i>"
    extra_block = await _extra_block(chat_id, date)
    if extra_block:
        text += "\n\n" + extra_block

    has_lessons = any(lesson.subject_name for lesson in eff.lessons)
    kb = get_date_editor_keyboard(date.isoformat(), has_lessons=has_lessons, has_changes=eff.has_changes)
    return text, kb


async def _show_editor(callback: CallbackQuery, date: datetime.date, note: Optional[str] = None):
    text, kb = await _render_editor(callback.message.chat.id, date)
    await safe_edit_text(callback.message, text, reply_markup=kb, parse_mode="HTML")
    await callback.answer(note) if note else await callback.answer()


async def _preview_text(chat_id: int, date: datetime.date, pending: dict) -> str:
    slots, sched, day_ovr, lesson_ovr = await _load(chat_id, date)
    before = compute_effective_day(date, slots, sched, day_ovr, lesson_ovr)
    sim_lesson, sim_day = _sim(lesson_ovr, day_ovr, pending, chat_id, date)
    after = compute_effective_day(date, slots, sched, sim_day, sim_lesson)

    day_name = DAYS_RU[date.weekday()]
    return (
        f"🔎 <b>Проверь изменение — {day_name}, {date.strftime('%d.%m.%Y')}</b>\n\n"
        f"📋 <b>Сейчас:</b>\n{_body(before)}\n\n"
        f"➡️ <b>Станет:</b>\n{_body(after)}"
    )


async def _preview_via_message(message: Message, state: FSMContext, date: datetime.date, pending: dict):
    await state.update_data(pending=pending)
    await state.set_state(None)  # leave the text step; keep data for do_save
    text = await _preview_text(message.chat.id, date, pending)
    await message.answer(text, reply_markup=get_override_preview_keyboard(date.isoformat()), parse_mode="HTML")


async def _preview_via_callback(callback: CallbackQuery, state: FSMContext, date: datetime.date, pending: dict):
    await state.update_data(pending=pending)
    await state.set_state(None)
    text = await _preview_text(callback.message.chat.id, date, pending)
    await safe_edit_text(
        callback.message, text,
        reply_markup=get_override_preview_keyboard(date.isoformat()), parse_mode="HTML",
    )
    await callback.answer()


# --- Menu & date picker -----------------------------------------------------

async def _show_menu(callback: CallbackQuery, start_offset: int):
    chat_id = callback.message.chat.id
    today = await _today(chat_id)
    change_dates = set(await get_override_dates(chat_id, since=today))
    text = (
        "🗓 <b>Изменения по датам</b>\n\n"
        "Выбери дату, чтобы отменить урок, заменить предмет, изменить время, "
        "добавить разовый урок или отметить свободный день / праздник / каникулы.\n\n"
        "Обычное недельное расписание при этом не меняется.\n"
        "<i>● — на эту дату уже есть изменения.</i>"
    )
    kb = get_date_grid_keyboard(start_offset, today, change_dates)
    await safe_edit_text(callback.message, text, reply_markup=kb, parse_mode="HTML")


@router.callback_query(F.data == "do_menu")
async def do_menu(callback: CallbackQuery, state: FSMContext):
    if not await require_admin(callback, callback.bot):
        return
    await state.clear()
    await _show_menu(callback, 0)
    await callback.answer()


@router.callback_query(F.data.startswith("do_days:"))
async def do_days(callback: CallbackQuery, state: FSMContext):
    if not await require_admin(callback, callback.bot):
        return
    await state.clear()
    ints = safe_callback_ints(callback.data, 1)
    if ints is None or not (0 <= ints[0] <= 730):
        await callback.answer(STALE_BUTTON_TEXT, show_alert=True)
        return
    await _show_menu(callback, ints[0])
    await callback.answer()


@router.callback_query(F.data.startswith("do_date:"))
async def do_date(callback: CallbackQuery, state: FSMContext):
    if not await require_admin(callback, callback.bot):
        return
    await state.clear()
    date = _parse_iso(callback.data.split(":", 1)[1])
    if date is None:
        await callback.answer(STALE_BUTTON_TEXT, show_alert=True)
        return
    await _show_editor(callback, date)


# --- Cancel / replace / retime: pick a lesson -------------------------------

@router.callback_query(F.data.startswith("do_pick:"))
async def do_pick(callback: CallbackQuery, state: FSMContext):
    if not await require_admin(callback, callback.bot):
        return
    await state.clear()
    parts = callback.data.split(":")
    if len(parts) < 3 or parts[2] not in ("cancel", "replace", "retime"):
        await callback.answer(STALE_BUTTON_TEXT, show_alert=True)
        return
    date = _parse_iso(parts[1])
    if date is None:
        await callback.answer(STALE_BUTTON_TEXT, show_alert=True)
        return
    op = parts[2]

    slots, sched, day_ovr, lesson_ovr = await _load(callback.message.chat.id, date)
    eff = compute_effective_day(date, slots, sched, day_ovr, lesson_ovr)
    candidates = []
    for lesson in eff.lessons:
        if lesson.cancelled or not lesson.subject_name:
            continue
        label = f"Урок {lesson.lesson_number}: {lesson.subject_name} ({lesson.start_time})"
        candidates.append((lesson.lesson_number, label[:64]))

    if not candidates:
        await callback.answer("На эту дату нет уроков для изменения.", show_alert=True)
        return

    prompts = {
        "cancel": "🚫 Какой урок отменить?",
        "replace": "🔄 У какого урока заменить предмет?",
        "retime": "🕒 У какого урока изменить время?",
    }
    await safe_edit_text(
        callback.message, prompts[op],
        reply_markup=get_lesson_pick_keyboard(date.isoformat(), op, candidates), parse_mode="HTML",
    )
    await callback.answer()


@router.callback_query(F.data.startswith("do_lesson:"))
async def do_lesson(callback: CallbackQuery, state: FSMContext):
    if not await require_admin(callback, callback.bot):
        return
    parts = callback.data.split(":")
    ints = safe_callback_ints(callback.data, 3)
    if len(parts) < 4 or ints is None or parts[2] not in ("cancel", "replace", "retime"):
        await callback.answer(STALE_BUTTON_TEXT, show_alert=True)
        return
    date = _parse_iso(parts[1])
    if date is None:
        await callback.answer(STALE_BUTTON_TEXT, show_alert=True)
        return
    op, num = parts[2], ints[0]

    if op == "cancel":
        pending = {"kind": "lesson", "lesson_number": num, "action": "cancel"}
        await _preview_via_callback(callback, state, date, pending)
        return

    # replace / retime need a text value → start an FSM step.
    await state.set_state(
        DateOverrideStates.waiting_for_subject if op == "replace" else DateOverrideStates.waiting_for_time
    )
    await state.update_data(iso=date.isoformat(), op=op, lesson_number=num)
    prompt = (
        "Введи новое название предмета:" if op == "replace"
        else "Введи новое время в формате <code>ЧЧ:ММ - ЧЧ:ММ</code> (например, <code>09:00 - 09:45</code>):"
    )
    await safe_edit_text(callback.message, prompt, parse_mode="HTML")
    await callback.answer()


# --- Add a one-off lesson ---------------------------------------------------

@router.callback_query(F.data.startswith("do_add:"))
async def do_add(callback: CallbackQuery, state: FSMContext):
    if not await require_admin(callback, callback.bot):
        return
    date = _parse_iso(callback.data.split(":", 1)[1])
    if date is None:
        await callback.answer(STALE_BUTTON_TEXT, show_alert=True)
        return
    await state.set_state(DateOverrideStates.waiting_for_subject)
    await state.update_data(iso=date.isoformat(), op="add")
    await safe_edit_text(
        callback.message,
        "➕ <b>Разовый урок</b>\n\nВведи название предмета:",
        parse_mode="HTML",
    )
    await callback.answer()


async def _next_lesson_number(chat_id: int, date: datetime.date) -> int:
    slots, _, _, lesson_ovr = await _load(chat_id, date)
    nums = {s.lesson_number for s in slots} | {o.lesson_number for o in lesson_ovr}
    return (max(nums) + 1) if nums else 1


@router.message(DateOverrideStates.waiting_for_subject, F.text)
async def process_subject(message: Message, state: FSMContext):
    subject = message.text.strip()
    if not subject:
        await message.answer("Название не может быть пустым. Введи название предмета:")
        return
    if len(subject) > MAX_SUBJECT_LEN:
        await message.answer(f"Слишком длинное название (макс. {MAX_SUBJECT_LEN} символов). Введи короче:")
        return

    data = await state.get_data()
    date = _parse_iso(data.get("iso"))
    if date is None:
        await state.clear()
        await message.answer(STALE_BUTTON_TEXT)
        return

    if data.get("op") == "add":
        await state.update_data(subject=subject)
        await state.set_state(DateOverrideStates.waiting_for_add_time)
        await message.answer(
            f"🏷 Предмет: <b>{html_escape(subject)}</b>\n\n"
            "Теперь введи время в формате <code>ЧЧ:ММ - ЧЧ:ММ</code> (например, <code>15:00 - 15:45</code>):",
            parse_mode="HTML",
        )
        return

    # replace
    pending = {
        "kind": "lesson", "lesson_number": data["lesson_number"],
        "action": "set", "subject": subject,
    }
    await _preview_via_message(message, state, date, pending)


@router.message(DateOverrideStates.waiting_for_time, F.text)
async def process_time(message: Message, state: FSMContext):
    try:
        start, end = parse_time_interval(message.text.strip())
    except ValueError as e:
        await message.answer(f"⚠️ {html_escape(str(e))}", parse_mode="HTML")
        return
    data = await state.get_data()
    date = _parse_iso(data.get("iso"))
    if date is None:
        await state.clear()
        await message.answer(STALE_BUTTON_TEXT)
        return
    pending = {
        "kind": "lesson", "lesson_number": data["lesson_number"],
        "action": "set", "start": start, "end": end,
    }
    await _preview_via_message(message, state, date, pending)


@router.message(DateOverrideStates.waiting_for_add_time, F.text)
async def process_add_time(message: Message, state: FSMContext):
    try:
        start, end = parse_time_interval(message.text.strip())
    except ValueError as e:
        await message.answer(f"⚠️ {html_escape(str(e))}", parse_mode="HTML")
        return
    data = await state.get_data()
    date = _parse_iso(data.get("iso"))
    if date is None:
        await state.clear()
        await message.answer(STALE_BUTTON_TEXT)
        return
    num = await _next_lesson_number(message.chat.id, date)
    pending = {
        "kind": "lesson", "lesson_number": num, "action": "set",
        "subject": data["subject"], "start": start, "end": end,
    }
    await _preview_via_message(message, state, date, pending)


# --- Day type (free / holiday / vacation / remote) --------------------------

@router.callback_query(F.data.startswith("do_dtype:"))
async def do_dtype(callback: CallbackQuery, state: FSMContext):
    if not await require_admin(callback, callback.bot):
        return
    await state.clear()
    date = _parse_iso(callback.data.split(":", 1)[1])
    if date is None:
        await callback.answer(STALE_BUTTON_TEXT, show_alert=True)
        return
    day_ovr = await get_day_override(callback.message.chat.id, date)
    await safe_edit_text(
        callback.message,
        "📅 <b>Тип дня</b>\n\nВыбери, что это за день (уроки на этот день будут заменены пометкой):",
        reply_markup=get_day_type_keyboard(date.isoformat(), has_day_type=day_ovr is not None),
        parse_mode="HTML",
    )
    await callback.answer()


@router.callback_query(F.data.startswith("do_setdtype:"))
async def do_setdtype(callback: CallbackQuery, state: FSMContext):
    if not await require_admin(callback, callback.bot):
        return
    parts = callback.data.split(":")
    if len(parts) < 3 or parts[2] not in ("free", "holiday", "vacation", "remote"):
        await callback.answer(STALE_BUTTON_TEXT, show_alert=True)
        return
    date = _parse_iso(parts[1])
    if date is None:
        await callback.answer(STALE_BUTTON_TEXT, show_alert=True)
        return
    await state.set_state(DateOverrideStates.waiting_for_day_note)
    await state.update_data(iso=date.isoformat(), day_type=parts[2])
    await safe_edit_text(
        callback.message,
        "📝 Добавь причину/примечание к этому дню "
        "(например, «Государственный праздник») или напиши <code>-</code>, чтобы пропустить:",
        parse_mode="HTML",
    )
    await callback.answer()


@router.message(DateOverrideStates.waiting_for_day_note, F.text)
async def process_day_note(message: Message, state: FSMContext):
    text = message.text.strip()
    note = None if text.lower() in SKIP_WORDS else text
    if note is not None and len(note) > MAX_NOTE_LEN:
        await message.answer(f"Слишком длинное примечание (макс. {MAX_NOTE_LEN} символов). Введи короче:")
        return
    data = await state.get_data()
    date = _parse_iso(data.get("iso"))
    if date is None:
        await state.clear()
        await message.answer(STALE_BUTTON_TEXT)
        return
    pending = {"kind": "day", "day_type": data["day_type"], "note": note}
    await _preview_via_message(message, state, date, pending)


# --- Save a pending change --------------------------------------------------

@router.callback_query(F.data.startswith("do_save:"))
async def do_save(callback: CallbackQuery, state: FSMContext):
    if not await require_admin(callback, callback.bot):
        return
    date = _parse_iso(callback.data.split(":", 1)[1])
    data = await state.get_data()
    pending = data.get("pending")
    if date is None or not pending:
        await callback.answer(STALE_BUTTON_TEXT, show_alert=True)
        return

    chat_id = callback.message.chat.id
    actor_user_id, actor_name = audit.actor_from(callback)
    date_label = date.strftime("%d.%m.%Y")
    if pending["kind"] == "lesson":
        row = await set_lesson_override(
            chat_id, date, pending["lesson_number"], pending["action"],
            subject_name=pending.get("subject"),
            start_time=pending.get("start"), end_time=pending.get("end"),
            note=pending.get("note"),
            actor_user_id=actor_user_id, actor_name=actor_name,
        )
        await audit.record_event(
            callback, chat_id, audit.ENTITY_LESSON_OVERRIDE, audit.ACTION_UPDATE,
            entity_id=row.id,
            summary=audit.summarize(
                date_label,
                f"урок {pending['lesson_number']}",
                LESSON_ACTION_LABELS.get(pending["action"], pending["action"]),
                pending.get("subject"),
            ),
        )
    else:  # day
        row = await set_day_override(
            chat_id, date, pending["day_type"], note=pending.get("note"),
            actor_user_id=actor_user_id, actor_name=actor_name,
        )
        await audit.record_event(
            callback, chat_id, audit.ENTITY_DAY_OVERRIDE, audit.ACTION_UPDATE,
            entity_id=row.id,
            summary=audit.summarize(date_label, DAY_TYPE_AUDIT_LABELS.get(
                pending["day_type"], pending["day_type"]
            )),
        )

    await state.clear()
    await _show_editor(callback, date, note="✅ Сохранено!")


# --- Clear all changes for a date (confirm) ---------------------------------

@router.callback_query(F.data.startswith("do_clear_ask:"))
async def do_clear_ask(callback: CallbackQuery, state: FSMContext):
    if not await require_admin(callback, callback.bot):
        return
    await state.clear()
    date = _parse_iso(callback.data.split(":", 1)[1])
    if date is None:
        await callback.answer(STALE_BUTTON_TEXT, show_alert=True)
        return
    iso = date.isoformat()
    await safe_edit_text(
        callback.message,
        f"❗ Очистить <b>все</b> изменения на {date.strftime('%d.%m.%Y')}?\n"
        "Дополнительные занятия при этом затронуты не будут.",
        reply_markup=get_confirm_keyboard(f"do_clear_yes:{iso}", f"do_date:{iso}", "⚠️ Да, очистить"),
        parse_mode="HTML",
    )
    await callback.answer()


@router.callback_query(F.data.startswith("do_clear_yes:"))
async def do_clear_yes(callback: CallbackQuery, state: FSMContext):
    if not await require_admin(callback, callback.bot):
        return
    await state.clear()
    date = _parse_iso(callback.data.split(":", 1)[1])
    if date is None:
        await callback.answer(STALE_BUTTON_TEXT, show_alert=True)
        return
    removed = await clear_date_overrides(callback.message.chat.id, date)
    if removed:
        # The overridden rows are gone; the journal keeps the fact they existed.
        await audit.record_event(
            callback, callback.message.chat.id, audit.ENTITY_LESSON_OVERRIDE,
            audit.ACTION_DELETE,
            summary=audit.summarize(date.strftime("%d.%m.%Y"), "очищены все изменения на дату"),
        )
    await _show_editor(callback, date, note="Изменения очищены.")


# --- Remove just the day type (confirm) -------------------------------------

@router.callback_query(F.data.startswith("do_rmday_ask:"))
async def do_rmday_ask(callback: CallbackQuery, state: FSMContext):
    if not await require_admin(callback, callback.bot):
        return
    await state.clear()
    date = _parse_iso(callback.data.split(":", 1)[1])
    if date is None:
        await callback.answer(STALE_BUTTON_TEXT, show_alert=True)
        return
    iso = date.isoformat()
    await safe_edit_text(
        callback.message,
        f"❗ Убрать пометку типа дня на {date.strftime('%d.%m.%Y')}?",
        reply_markup=get_confirm_keyboard(f"do_rmday_yes:{iso}", f"do_dtype:{iso}", "⚠️ Да, убрать"),
        parse_mode="HTML",
    )
    await callback.answer()


@router.callback_query(F.data.startswith("do_rmday_yes:"))
async def do_rmday_yes(callback: CallbackQuery, state: FSMContext):
    if not await require_admin(callback, callback.bot):
        return
    await state.clear()
    date = _parse_iso(callback.data.split(":", 1)[1])
    if date is None:
        await callback.answer(STALE_BUTTON_TEXT, show_alert=True)
        return
    removed = await clear_day_override(callback.message.chat.id, date)
    if removed:
        await audit.record_event(
            callback, callback.message.chat.id, audit.ENTITY_DAY_OVERRIDE,
            audit.ACTION_DELETE,
            summary=audit.summarize(date.strftime("%d.%m.%Y"), "убран тип дня"),
        )
    await _show_editor(callback, date, note="Тип дня убран.")


# --- Fallback: non-text content while a step expects text -------------------

async def date_override_non_text(message: Message):
    await message.answer(NON_TEXT_HINT)


router.message.register(
    date_override_non_text,
    StateFilter(
        DateOverrideStates.waiting_for_subject,
        DateOverrideStates.waiting_for_time,
        DateOverrideStates.waiting_for_add_time,
        DateOverrideStates.waiting_for_day_note,
    ),
)

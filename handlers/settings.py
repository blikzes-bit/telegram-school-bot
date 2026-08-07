import re

from aiogram import Router, F
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardRemove
from database.db import (
    get_or_create_chat, update_chat_reminder_times,
    delete_chat, set_reminder_category_enabled, update_duetoday_time, set_quiet_hours,
    set_hw_edit_policy, set_access_mode, set_chat_owner, set_chat_profile,
    set_chat_timezone,
)
import services.timeservice as ts
from services.timeservice import has_quiet_hours
from services import profiles
from services.permissions import (
    ACCESS_MODE_DESCRIPTIONS, ACCESS_MODE_LABELS, ACCESS_MODES,
    POLICY_DESCRIPTIONS, POLICY_LABELS, normalize_access_mode, normalize_policy,
)
import services.audit as audit
from keyboards.inline import (
    get_settings_keyboard, get_general_settings_keyboard, get_access_mode_keyboard,
    get_cancel_keyboard, get_hw_policy_keyboard, get_profile_keyboard,
    get_timezone_keyboard, get_timezone_confirm_keyboard,
)
from keyboards.reply import main_menu_for
from middleware.access import require_admin
from utils import html_escape, safe_edit_text

router = Router()

NON_TEXT_HINT = "🤔 Мне нужен текст. Пожалуйста, отправь время текстом в формате <code>ЧЧ:ММ</code>."

# Reminder-category labels for audit summaries.
CATEGORY_AUDIT_LABELS = {
    "hw": "ДЗ на завтра",
    "sched": "портфель на завтра",
    "duetoday": "ДЗ в день сдачи",
    "changes": "изменения в расписании",
    "extra": "напоминания о доп. занятиях",
}


async def _audit_settings(event, summary: str):
    """Journal one settings change (chat-wide config, no entity id)."""
    chat_id = event.message.chat.id if hasattr(event, "data") else event.chat.id
    await audit.record_event(
        event, chat_id, audit.ENTITY_SETTINGS, audit.ACTION_UPDATE,
        summary=audit.summarize(summary),
    )


class SettingStates(StatesGroup):
    waiting_for_hw_time = State()
    waiting_for_sch_time = State()
    waiting_for_duetoday_time = State()
    waiting_for_quiet = State()
    waiting_for_timezone = State()
    waiting_for_reset_confirm = State()


def _status_label(enabled: bool) -> str:
    return "🟢 включено" if enabled else "🔴 отключено"


async def format_settings_message(chat_id: int, chat_type: str = "private") -> str:
    """Reminder-only screen ("⏰ Напоминания"): notification categories + quiet
    hours. General options (timezone, policy, data, reset) live behind the
    "⚙️ Настройки" button and :func:`format_general_settings_message`."""
    chat = await get_or_create_chat(chat_id, chat_type)
    quiet = (
        f"с <b>{chat.quiet_start}</b> до <b>{chat.quiet_end}</b>"
        if has_quiet_hours(chat.quiet_start, chat.quiet_end) else "отключены"
    )
    return (
        "⏰ <b>Напоминания</b>\n\n"
        f"🔔 <b>ДЗ на завтра</b> ({_status_label(chat.hw_reminder_enabled)}) — в <b>{chat.hw_reminder_time}</b>.\n"
        f"🎒 <b>Портфель на завтра</b> ({_status_label(chat.schedule_reminder_enabled)}) — в <b>{chat.schedule_reminder_time}</b>.\n"
        f"⏰ <b>ДЗ в день сдачи</b> ({_status_label(chat.hw_duetoday_enabled)}) — в <b>{chat.hw_duetoday_time}</b>.\n"
        f"⚠️ <b>Изменения в расписании на завтра</b> ({_status_label(chat.changes_reminder_enabled)}).\n"
        f"🎯 <b>Напоминания о доп. занятиях</b> ({_status_label(chat.extra_reminder_enabled)}) — время задаётся у каждого занятия.\n\n"
        f"🌙 <b>Тихие часы</b>: {quiet}. В тихие часы несрочные уведомления откладываются.\n\n"
        f"Настройте категории и время кнопками ниже:"
    )


async def format_general_settings_message(chat_id: int, chat_type: str = "private") -> str:
    """General-settings screen ("⚙️ Настройки"): timezone, homework-edit policy
    and pointers to history / data / reset. Notification categories live on the
    "⏰ Напоминания" screen (see :func:`format_settings_message`)."""
    chat = await get_or_create_chat(chat_id, chat_type)
    policy = normalize_policy(chat.hw_edit_policy)
    profile = profiles.resolve(chat)
    lines = [
        "⚙️ <b>Настройки</b>",
        "",
        f"🧩 <b>Режим</b>: {profiles.PROFILE_LABELS[profile]} — "
        f"{profiles.PROFILE_DESCRIPTIONS[profile]}.",
        f"🌍 <b>Часовой пояс</b>: {html_escape(ts.tz_label(ts.chat_tz(chat)))} — "
        f"местное время <b>{ts.local_time_label(ts.chat_tz(chat))}</b>.",
    ]
    if profiles.features(profile).homework_policy:
        mode = normalize_access_mode(chat.access_mode)
        lines.append(
            f"🔐 <b>Кто вносит данные</b>: {ACCESS_MODE_LABELS[mode]} — "
            f"{ACCESS_MODE_DESCRIPTIONS[mode]}."
        )
        lines.append(
            f"✍️ <b>Кто может изменять ДЗ</b>: {POLICY_LABELS[policy]} — "
            f"{POLICY_DESCRIPTIONS[policy]}."
        )
    lines += [
        "",
        "📜 <b>История изменений</b> — журнал важных действий.",
        "💾 <b>Данные и резервная копия</b> — экспорт, импорт, восстановление.",
        "⚙️ <b>Сброс</b> — полностью очистить настройки чата.",
        "",
        "Выберите раздел кнопками ниже:",
    ]
    return "\n".join(lines)


async def get_settings_keyboard_for_chat(chat_id: int, chat_type: str = "private"):
    chat = await get_or_create_chat(chat_id, chat_type)
    return get_settings_keyboard(chat)


async def get_general_settings_keyboard_for_chat(chat_id: int, chat_type: str = "private"):
    chat = await get_or_create_chat(chat_id, chat_type)
    return get_general_settings_keyboard(chat)


async def _refresh(callback: CallbackQuery):
    """Redraw the reminders screen in place (after a toggle or a cancel)."""
    text = await format_settings_message(callback.message.chat.id, callback.message.chat.type)
    kb = await get_settings_keyboard_for_chat(callback.message.chat.id, callback.message.chat.type)
    await safe_edit_text(callback.message, text, reply_markup=kb, parse_mode="HTML")


async def _refresh_general(callback: CallbackQuery):
    """Redraw the general-settings screen in place (after a policy/timezone change)."""
    text = await format_general_settings_message(callback.message.chat.id, callback.message.chat.type)
    kb = await get_general_settings_keyboard_for_chat(callback.message.chat.id, callback.message.chat.type)
    await safe_edit_text(callback.message, text, reply_markup=kb, parse_mode="HTML")


@router.message(F.text == "⏰ Напоминания")
async def show_reminders(message: Message, state: FSMContext):
    await state.clear()
    text = await format_settings_message(message.chat.id, message.chat.type)
    kb = await get_settings_keyboard_for_chat(message.chat.id, message.chat.type)
    await message.answer(text, reply_markup=kb, parse_mode="HTML")


@router.message(Command("settings"))
@router.message(F.text == "⚙️ Настройки")
async def show_settings(message: Message, state: FSMContext):
    await state.clear()
    text = await format_general_settings_message(message.chat.id, message.chat.type)
    kb = await get_general_settings_keyboard_for_chat(message.chat.id, message.chat.type)
    await message.answer(text, reply_markup=kb, parse_mode="HTML")


@router.callback_query(F.data == "set_hw_rem")
async def edit_hw_reminder(callback: CallbackQuery, state: FSMContext):
    if not await require_admin(callback, callback.bot):
        return
    await state.set_state(SettingStates.waiting_for_hw_time)
    await safe_edit_text(
        callback.message,
        "🔔 <b>Настройка напоминания о ДЗ</b>\n\n"
        "Введите время в 24-часовом формате <code>ЧЧ:ММ</code> (например, <code>17:30</code> или <code>20:00</code>), "
        "когда вы хотите получать напоминание о домашнем задании на завтра:",
        reply_markup=get_cancel_keyboard(callback_data="set_cancel"),
        parse_mode="HTML"
    )
    await callback.answer()


@router.callback_query(F.data == "set_sch_rem")
async def edit_sch_reminder(callback: CallbackQuery, state: FSMContext):
    if not await require_admin(callback, callback.bot):
        return
    await state.set_state(SettingStates.waiting_for_sch_time)
    await safe_edit_text(
        callback.message,
        "🎒 <b>Настройка напоминания о портфеле</b>\n\n"
        "Введите время в 24-часовом формате <code>ЧЧ:ММ</code> (например, <code>19:30</code> или <code>21:15</code>), "
        "когда вы хотите получать расписание уроков на завтра:",
        reply_markup=get_cancel_keyboard(callback_data="set_cancel"),
        parse_mode="HTML"
    )
    await callback.answer()


TIME_FORMAT = re.compile(r"^([0-1]?[0-9]|2[0-3]):[0-5][0-9]$")


@router.message(SettingStates.waiting_for_hw_time, F.text)
async def process_hw_time(message: Message, state: FSMContext):
    text = message.text.strip()
    if not TIME_FORMAT.match(text):
        await message.answer("Неверный формат времени! Напишите в формате <code>ЧЧ:ММ</code> (например, <code>18:00</code>):", parse_mode="HTML")
        return

    # Standardize time string (e.g. 8:00 -> 08:00)
    hour, minute = map(int, text.split(":"))
    std_time = f"{hour:02d}:{minute:02d}"

    await update_chat_reminder_times(message.chat.id, hw_time=std_time)
    await _audit_settings(message, f"время напоминания о ДЗ: {std_time}")
    await state.clear()

    await message.answer(f"✅ Время напоминания о ДЗ успешно изменено на <b>{std_time}</b>!", reply_markup=await main_menu_for(message.chat.id, message.chat.type), parse_mode="HTML")

    settings_text = await format_settings_message(message.chat.id, message.chat.type)
    kb = await get_settings_keyboard_for_chat(message.chat.id, message.chat.type)
    await message.answer(settings_text, reply_markup=kb, parse_mode="HTML")


@router.message(SettingStates.waiting_for_sch_time, F.text)
async def process_sch_time(message: Message, state: FSMContext):
    text = message.text.strip()
    if not TIME_FORMAT.match(text):
        await message.answer("Неверный формат времени! Напишите в формате <code>ЧЧ:ММ</code> (например, <code>20:30</code>):", parse_mode="HTML")
        return

    hour, minute = map(int, text.split(":"))
    std_time = f"{hour:02d}:{minute:02d}"

    await update_chat_reminder_times(message.chat.id, schedule_time=std_time)
    await _audit_settings(message, f"время напоминания о портфеле: {std_time}")
    await state.clear()

    await message.answer(f"✅ Время напоминания о портфеле успешно изменено на <b>{std_time}</b>!", reply_markup=await main_menu_for(message.chat.id, message.chat.type), parse_mode="HTML")

    settings_text = await format_settings_message(message.chat.id, message.chat.type)
    kb = await get_settings_keyboard_for_chat(message.chat.id, message.chat.type)
    await message.answer(settings_text, reply_markup=kb, parse_mode="HTML")


@router.message(SettingStates.waiting_for_duetoday_time, F.text)
async def process_duetoday_time(message: Message, state: FSMContext):
    text = message.text.strip()
    if not TIME_FORMAT.match(text):
        await message.answer("Неверный формат времени! Напишите в формате <code>ЧЧ:ММ</code> (например, <code>07:30</code>):", parse_mode="HTML")
        return
    hour, minute = map(int, text.split(":"))
    std_time = f"{hour:02d}:{minute:02d}"
    await update_duetoday_time(message.chat.id, std_time)
    await _audit_settings(message, f"время напоминания о ДЗ в день сдачи: {std_time}")
    await state.clear()
    await message.answer(f"✅ Время напоминания о ДЗ в день сдачи изменено на <b>{std_time}</b>!", reply_markup=await main_menu_for(message.chat.id, message.chat.type), parse_mode="HTML")
    settings_text = await format_settings_message(message.chat.id, message.chat.type)
    kb = await get_settings_keyboard_for_chat(message.chat.id, message.chat.type)
    await message.answer(settings_text, reply_markup=kb, parse_mode="HTML")


QUIET_OFF_WORDS = {"выкл", "off", "нет", "-", "отключить"}
QUIET_FORMAT = re.compile(r"^([0-1]?[0-9]|2[0-3]):[0-5][0-9]\s*-\s*([0-1]?[0-9]|2[0-3]):[0-5][0-9]$")


@router.message(SettingStates.waiting_for_quiet, F.text)
async def process_quiet_hours(message: Message, state: FSMContext):
    text = message.text.strip().lower()
    if text in QUIET_OFF_WORDS:
        await set_quiet_hours(message.chat.id, None, None)
        await _audit_settings(message, "тихие часы отключены")
        await state.clear()
        await message.answer("✅ Тихие часы отключены.", reply_markup=await main_menu_for(message.chat.id, message.chat.type))
    elif QUIET_FORMAT.match(text):
        start_raw, end_raw = text.split("-")
        sh, sm = map(int, start_raw.strip().split(":"))
        eh, em = map(int, end_raw.strip().split(":"))
        start, end = f"{sh:02d}:{sm:02d}", f"{eh:02d}:{em:02d}"
        if start == end:
            await message.answer("Начало и конец тихих часов не могут совпадать. Введите другой интервал:")
            return
        await set_quiet_hours(message.chat.id, start, end)
        await _audit_settings(message, f"тихие часы: {start}-{end}")
        await state.clear()
        await message.answer(f"✅ Тихие часы установлены: <b>{start}–{end}</b>.", reply_markup=await main_menu_for(message.chat.id, message.chat.type), parse_mode="HTML")
    else:
        await message.answer(
            "Неверный формат! Введите интервал <code>ЧЧ:ММ-ЧЧ:ММ</code> (например, <code>22:00-07:00</code>) "
            "или <code>выкл</code>:",
            parse_mode="HTML",
        )
        return

    settings_text = await format_settings_message(message.chat.id, message.chat.type)
    kb = await get_settings_keyboard_for_chat(message.chat.id, message.chat.type)
    await message.answer(settings_text, reply_markup=kb, parse_mode="HTML")


@router.callback_query(F.data == "set_cancel")
@router.callback_query(F.data == "set_reminders")
async def cancel_settings_edit(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await _refresh(callback)
    await callback.answer()


@router.callback_query(F.data == "set_general")
async def open_general_settings(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await _refresh_general(callback)
    await callback.answer()


_CATEGORY_ATTR = {
    "hw": "hw_reminder_enabled",
    "sched": "schedule_reminder_enabled",
    "duetoday": "hw_duetoday_enabled",
    "changes": "changes_reminder_enabled",
    "extra": "extra_reminder_enabled",
}


@router.callback_query(F.data.startswith("set_toggle:"))
async def toggle_category(callback: CallbackQuery, state: FSMContext):
    if not await require_admin(callback, callback.bot):
        return
    category = callback.data.split(":", 1)[1]
    attr = _CATEGORY_ATTR.get(category)
    if attr is None:
        await callback.answer("⚠️ Неизвестная категория.", show_alert=True)
        return
    chat = await get_or_create_chat(callback.message.chat.id, callback.message.chat.type)
    enabled = not getattr(chat, attr)
    await set_reminder_category_enabled(chat.chat_id, category, enabled)
    await _audit_settings(
        callback,
        f"категория «{CATEGORY_AUDIT_LABELS.get(category, category)}»: "
        f"{'включена' if enabled else 'отключена'}",
    )
    await _refresh(callback)
    await callback.answer()


# --- Timezone (admin in a group; the single user in a private chat) ----------

TZ_HELP_TEXT = (
    "🌍 <b>Часовой пояс чата</b>\n\n"
    "От него зависят все даты и время этого чата: «Сегодня», сроки сдачи ДЗ, "
    "чётные/нечётные недели, изменения по датам, доп. занятия и все напоминания.\n\n"
    "Выбери пояс из списка или введи название вручную "
    "(формат IANA, например <code>Europe/Kyiv</code>)."
)


async def _show_timezone_menu(callback: CallbackQuery):
    chat = await get_or_create_chat(callback.message.chat.id, callback.message.chat.type)
    tz = ts.chat_tz(chat)
    await safe_edit_text(
        callback.message,
        f"{TZ_HELP_TEXT}\n\n"
        f"Сейчас: <b>{html_escape(ts.tz_label(tz))}</b>\n"
        f"🕒 Местное время: <b>{ts.local_time_label(tz)}</b>",
        reply_markup=get_timezone_keyboard(chat.timezone),
        parse_mode="HTML",
    )


@router.callback_query(F.data == "set_tz")
async def show_timezone(callback: CallbackQuery, state: FSMContext):
    if not await require_admin(callback, callback.bot):
        return
    await state.clear()
    await _show_timezone_menu(callback)
    await callback.answer()


async def _preview_timezone(callback: CallbackQuery, tz_name: str):
    """
    Show the chosen zone's *current local time* before committing, so a wrong
    pick is obvious ("это точно не наше время") rather than discovered later by
    a reminder arriving at 4am.
    """
    canonical = ts.normalize_timezone(tz_name)
    if canonical is None:
        await callback.answer("⚠️ Неизвестный часовой пояс.", show_alert=True)
        return False
    tz = ts.tz_from_name(canonical)
    await safe_edit_text(
        callback.message,
        "🌍 <b>Проверь часовой пояс</b>\n\n"
        f"Пояс: <b>{html_escape(ts.tz_label(tz))}</b>\n"
        f"🕒 Местное время сейчас: <b>{ts.local_time_label(tz)}</b>\n\n"
        "Если время совпадает с твоим — сохраняй.",
        reply_markup=get_timezone_confirm_keyboard(canonical),
        parse_mode="HTML",
    )
    return True


@router.callback_query(F.data.startswith("set_tz_pick:"))
async def pick_timezone(callback: CallbackQuery, state: FSMContext):
    if not await require_admin(callback, callback.bot):
        return
    await state.clear()
    if await _preview_timezone(callback, callback.data.split(":", 1)[1]):
        await callback.answer()


@router.callback_query(F.data == "set_tz_manual")
async def ask_timezone_manually(callback: CallbackQuery, state: FSMContext):
    if not await require_admin(callback, callback.bot):
        return
    await state.set_state(SettingStates.waiting_for_timezone)
    await safe_edit_text(
        callback.message,
        "✏️ Введи название часового пояса в формате IANA — например "
        "<code>Europe/Kyiv</code>, <code>America/New_York</code> или <code>UTC</code>:",
        reply_markup=get_cancel_keyboard(callback_data="set_tz"),
        parse_mode="HTML",
    )
    await callback.answer()


@router.message(SettingStates.waiting_for_timezone, F.text)
async def process_timezone_input(message: Message, state: FSMContext):
    canonical = ts.normalize_timezone(message.text)
    if canonical is None:
        await message.answer(
            "⚠️ Не знаю такого часового пояса. Нужно название в формате IANA, "
            "например <code>Europe/Kyiv</code> или <code>America/New_York</code>. "
            "Попробуй ещё раз:",
            parse_mode="HTML",
        )
        return
    tz = ts.tz_from_name(canonical)
    await state.clear()
    await message.answer(
        "🌍 <b>Проверь часовой пояс</b>\n\n"
        f"Пояс: <b>{html_escape(ts.tz_label(tz))}</b>\n"
        f"🕒 Местное время сейчас: <b>{ts.local_time_label(tz)}</b>\n\n"
        "Если время совпадает с твоим — сохраняй.",
        reply_markup=get_timezone_confirm_keyboard(canonical),
        parse_mode="HTML",
    )


@router.callback_query(F.data.startswith("set_tz_save:"))
async def save_timezone(callback: CallbackQuery, state: FSMContext):
    if not await require_admin(callback, callback.bot):
        return
    await state.clear()
    tz_name = callback.data.split(":", 1)[1]
    # set_chat_timezone validates again: a stale/forged callback must not be able
    # to store a zone the scheduler would then have to fall back from.
    if not await set_chat_timezone(callback.message.chat.id, tz_name):
        await callback.answer("⚠️ Неизвестный часовой пояс.", show_alert=True)
        return
    await _audit_settings(callback, f"часовой пояс: {tz_name}")
    await _refresh_general(callback)
    await callback.answer(f"Часовой пояс сохранён: {tz_name}")


# --- Homework edit policy (admin) -------------------------------------------

@router.callback_query(F.data == "set_hw_policy")
async def show_hw_policy(callback: CallbackQuery, state: FSMContext):
    if not await require_admin(callback, callback.bot):
        return
    await state.clear()
    chat = await get_or_create_chat(callback.message.chat.id, callback.message.chat.type)
    current = normalize_policy(chat.hw_edit_policy)
    lines = "\n".join(
        f"• <b>{POLICY_LABELS[policy]}</b> — {POLICY_DESCRIPTIONS[policy]}"
        for policy in POLICY_LABELS
    )
    await safe_edit_text(
        callback.message,
        "✍️ <b>Кто может изменять домашние задания</b>\n\n"
        f"{lines}\n\n"
        f"Сейчас выбрано: <b>{POLICY_LABELS[current]}</b>.\n\n"
        "<i>Добавлять ДЗ может любой участник при любой настройке — правило "
        "касается изменения, выполнения и удаления уже существующих записей. "
        "В личном чате оно ничего не ограничивает.</i>",
        reply_markup=get_hw_policy_keyboard(current),
        parse_mode="HTML",
    )
    await callback.answer()


@router.callback_query(F.data.startswith("set_hw_policy_set:"))
async def set_hw_policy(callback: CallbackQuery, state: FSMContext):
    if not await require_admin(callback, callback.bot):
        return
    await state.clear()
    policy = callback.data.split(":", 1)[1]
    # set_hw_edit_policy rejects anything that isn't a known policy, so a stale
    # or hand-crafted callback can't put the chat into an unknown state.
    if not await set_hw_edit_policy(callback.message.chat.id, policy):
        await callback.answer("⚠️ Неизвестный режим прав.", show_alert=True)
        return
    await _audit_settings(callback, f"права на изменение ДЗ: {POLICY_LABELS[policy]}")
    await _refresh_general(callback)
    await callback.answer(f"Готово: {POLICY_LABELS[policy]}")


@router.callback_query(F.data == "set_profile")
async def show_profile(callback: CallbackQuery, state: FSMContext):
    if not await require_admin(callback, callback.bot):
        return
    await state.clear()
    chat = await get_or_create_chat(callback.message.chat.id, callback.message.chat.type)
    current = profiles.resolve(chat)
    lines = "\n".join(
        f"• <b>{profiles.PROFILE_LABELS[name]}</b> — {profiles.PROFILE_DESCRIPTIONS[name]}"
        for name in profiles.PROFILES
    )
    await safe_edit_text(
        callback.message,
        "🧩 <b>Как используется этот чат</b>\n\n"
        f"{lines}\n\n"
        f"Сейчас: <b>{profiles.PROFILE_LABELS[current]}</b>.\n\n"
        "<i>Режим влияет только на то, какие разделы показываются. Ничего не "
        "удаляется: если переключиться обратно, расписание и все записи снова "
        "будут на месте. Права участников режим не меняет.</i>",
        reply_markup=get_profile_keyboard(current),
        parse_mode="HTML",
    )
    await callback.answer()


@router.callback_query(F.data.startswith("set_profile_set:"))
async def set_profile(callback: CallbackQuery, state: FSMContext):
    if not await require_admin(callback, callback.bot):
        return
    await state.clear()
    profile = callback.data.split(":", 1)[1]
    # set_chat_profile rejects anything unknown, so a stale or hand-crafted
    # callback can't put the chat into a profile the app doesn't understand.
    if not await set_chat_profile(callback.message.chat.id, profile):
        await callback.answer("⚠️ Неизвестный режим.", show_alert=True)
        return
    await _audit_settings(callback, f"режим чата: {profiles.PROFILE_LABELS[profile]}")
    await _refresh_general(callback)
    await callback.answer(f"Готово: {profiles.PROFILE_LABELS[profile]}")


@router.callback_query(F.data == "set_access")
async def show_access_mode(callback: CallbackQuery, state: FSMContext):
    if not await require_admin(callback, callback.bot):
        return
    await state.clear()
    chat = await get_or_create_chat(callback.message.chat.id, callback.message.chat.type)
    current = normalize_access_mode(chat.access_mode)
    lines = "\n".join(
        f"• <b>{ACCESS_MODE_LABELS[mode]}</b> — {ACCESS_MODE_DESCRIPTIONS[mode]}"
        for mode in ACCESS_MODES
    )
    await safe_edit_text(
        callback.message,
        "🔐 <b>Кто вносит данные</b>\n\n"
        f"{lines}\n\n"
        f"Сейчас: <b>{ACCESS_MODE_LABELS[current]}</b>.\n\n"
        "<i>Во втором варианте роли выдаёт владелец чата в приложении "
        "(раздел «Участники»). Тот, кому роль не выдали, может только смотреть. "
        "Владелец чата остаётся владельцем всегда — потерять доступ к своему "
        "чату нельзя.</i>",
        reply_markup=get_access_mode_keyboard(current),
        parse_mode="HTML",
    )
    await callback.answer()


@router.callback_query(F.data.startswith("set_access_set:"))
async def pick_access_mode(callback: CallbackQuery, state: FSMContext):
    if not await require_admin(callback, callback.bot):
        return
    await state.clear()
    mode = callback.data.split(":", 1)[1]
    # Turning on role mode is what makes "only chosen people write" real, so the
    # switcher must also make sure this chat *has* an owner — otherwise nobody
    # would be able to hand out roles afterwards.
    if not await set_access_mode(callback.message.chat.id, mode):
        await callback.answer("⚠️ Неизвестный режим.", show_alert=True)
        return
    await set_chat_owner(callback.message.chat.id, callback.from_user.id, only_if_empty=True)
    await _audit_settings(callback, f"кто вносит данные: {ACCESS_MODE_LABELS[mode]}")
    await _refresh_general(callback)
    await callback.answer(f"Готово: {ACCESS_MODE_LABELS[mode]}")


@router.callback_query(F.data == "set_duetoday_time")
async def edit_duetoday_time(callback: CallbackQuery, state: FSMContext):
    if not await require_admin(callback, callback.bot):
        return
    await state.set_state(SettingStates.waiting_for_duetoday_time)
    await safe_edit_text(
        callback.message,
        "⏰ <b>Напоминание о ДЗ в день сдачи</b>\n\n"
        "Во сколько напоминать утром о домашке, которую нужно сдать сегодня? "
        "Введите время в формате <code>ЧЧ:ММ</code> (например, <code>07:30</code>):",
        reply_markup=get_cancel_keyboard(callback_data="set_cancel"),
        parse_mode="HTML",
    )
    await callback.answer()


@router.callback_query(F.data == "set_quiet")
async def edit_quiet_hours(callback: CallbackQuery, state: FSMContext):
    if not await require_admin(callback, callback.bot):
        return
    await state.set_state(SettingStates.waiting_for_quiet)
    await safe_edit_text(
        callback.message,
        "🌙 <b>Тихие часы</b>\n\n"
        "В тихие часы несрочные уведомления откладываются (напоминание о занятии "
        "при этом не придёт после его начала).\n\n"
        "Введите интервал в формате <code>ЧЧ:ММ-ЧЧ:ММ</code> (например, <code>22:00-07:00</code>) "
        "или напишите <code>выкл</code>, чтобы отключить:",
        reply_markup=get_cancel_keyboard(callback_data="set_cancel"),
        parse_mode="HTML",
    )
    await callback.answer()


# RESET ALL SETTINGS
@router.callback_query(F.data == "set_reset_all")
async def confirm_reset(callback: CallbackQuery, state: FSMContext):
    if not await require_admin(callback, callback.bot):
        return
    await state.set_state(SettingStates.waiting_for_reset_confirm)

    confirm_kb = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="⚠️ Да, удалить всё", callback_data="set_reset_confirm"),
            InlineKeyboardButton(text="❌ Нет, отмена", callback_data="set_general")
        ]
    ])

    await safe_edit_text(
        callback.message,
        "⚠️ <b>ВНИМАНИЕ!</b> ⚠️\n\n"
        "Вы действительно хотите сбросить все настройки?\n"
        "Это безвозвратно удалит ваше расписание, время уроков и все записанные домашние задания!",
        reply_markup=confirm_kb,
        parse_mode="HTML"
    )
    await callback.answer()


@router.callback_query(SettingStates.waiting_for_reset_confirm, F.data == "set_reset_confirm")
async def execute_reset(callback: CallbackQuery, state: FSMContext):
    if not await require_admin(callback, callback.bot):
        return
    chat_id = callback.message.chat.id

    # Delete chat entry from database (cascade deletes everything else)
    await delete_chat(chat_id)

    await state.clear()
    await callback.answer("Все данные удалены.")

    # Re-trigger start flow (will show onboarding start keyboard)
    await safe_edit_text(
        callback.message,
        "👋 Все данные этого чата были успешно удалены.\n"
        "Бот сброшен к первоначальному состоянию. Нажмите кнопку ниже, чтобы начать новую настройку:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🚀 Начать настройку", callback_data="ob_start")]
        ])
    )
    # The persistent reply keyboard (main menu) is separate from the inline
    # keyboard above and would otherwise keep showing post-reset — remove it.
    removal_notice = await callback.message.answer(
        "Клавиатура сброшена.", reply_markup=ReplyKeyboardRemove()
    )
    try:
        await removal_notice.delete()
    except Exception:
        pass


# --- Fallback: non-text content while awaiting a reminder time ---
async def settings_non_text(message: Message):
    await message.answer(NON_TEXT_HINT, parse_mode="HTML")


router.message.register(
    settings_non_text,
    StateFilter(
        SettingStates.waiting_for_hw_time,
        SettingStates.waiting_for_sch_time,
        SettingStates.waiting_for_duetoday_time,
        SettingStates.waiting_for_quiet,
        SettingStates.waiting_for_timezone,
    ),
)

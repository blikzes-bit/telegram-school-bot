import datetime
from typing import Optional

from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

from services.permissions import HW_EDIT_POLICIES, POLICY_LABELS, normalize_policy
from services.timeservice import POPULAR_TIMEZONES

DAYS_RU = ["Понедельник", "Вторник", "Среда", "Четверг", "Пятница", "Суббота", "Воскресенье"]
DAYS_SHORT_RU = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]

def get_schedule_days_keyboard(
    current_day: int, week_mode: bool = False, week_type: str = "all"
) -> InlineKeyboardMarkup:
    """
    Inline keyboard with the days of the week (current day highlighted).

    When ``week_mode`` is on, an A/B week switcher is shown and every day/edit
    button carries the currently-viewed ``week_type`` so navigation stays in
    the same week; a control row lets the admin turn alternation off. When off,
    a single control row offers to enable alternating (even/odd) weeks.
    """
    wt = week_type if week_mode else "all"
    buttons = []

    if week_mode:
        buttons.append([
            InlineKeyboardButton(
                text=("• 🅰 A (нечёт.) •" if wt == "A" else "🅰 A (нечёт.)"),
                callback_data=f"sch_day:{current_day}:A",
            ),
            InlineKeyboardButton(
                text=("• 🅱 B (чёт.) •" if wt == "B" else "🅱 B (чёт.)"),
                callback_data=f"sch_day:{current_day}:B",
            ),
        ])

    day_row = []
    for i, short_name in enumerate(DAYS_SHORT_RU):
        text = f"• {short_name} •" if i == current_day else short_name
        day_row.append(InlineKeyboardButton(text=text, callback_data=f"sch_day:{i}:{wt}"))
    buttons.append(day_row)

    buttons.append([
        InlineKeyboardButton(text="✏️ Изменить уроки на этот день", callback_data=f"sch_edit:{current_day}:{wt}"),
        InlineKeyboardButton(text="🕒 Настройка звонков", callback_data="sch_edit_times")
    ])
    # Per-date changes (cancellations, substitutions, holidays, one-off lessons)
    buttons.append([
        InlineKeyboardButton(text="🗓 Изменения по датам", callback_data="do_menu")
    ])

    if week_mode:
        buttons.append([
            InlineKeyboardButton(text="📋 Скопировать обычное расписание в A и B", callback_data="sch_copy_ab"),
        ])
        buttons.append([
            InlineKeyboardButton(text="🔀 Выключить чередование недель", callback_data="sch_wk_off"),
        ])
    else:
        buttons.append([
            InlineKeyboardButton(text="🔀 Включить чётную/нечётную неделю", callback_data="sch_wk_on"),
        ])

    return InlineKeyboardMarkup(inline_keyboard=buttons)

def get_homework_menu_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="➕ Добавить ДЗ", callback_data="hw_add"),
                InlineKeyboardButton(text="🗄️ Архив (Выполненные)", callback_data="hw_archive")
            ],
            [
                InlineKeyboardButton(text="🔄 Обновить список", callback_data="hw_list_active")
            ]
        ]
    )

def get_homework_action_keyboard(
    hw_id: int, is_archive: bool = False, page: int = 0, attachment_count: int = 0
) -> InlineKeyboardMarkup:
    archive_flag = 1 if is_archive else 0
    top_row = []
    if not is_archive:
        top_row.append(InlineKeyboardButton(text="✅ Выполнено", callback_data=f"hw_complete:{hw_id}:{page}"))
    else:
        top_row.append(InlineKeyboardButton(text="🔄 Вернуть в список", callback_data=f"hw_restore:{hw_id}:{page}"))
    top_row.append(InlineKeyboardButton(text="✏️ Редактировать", callback_data=f"hw_edit_menu:{hw_id}:{archive_flag}:{page}"))

    files_label = f"📎 Вложения ({attachment_count})" if attachment_count else "📎 Вложения"
    files_row = [InlineKeyboardButton(
        text=files_label, callback_data=f"hw_att_menu:{hw_id}:{archive_flag}:{page}"
    )]

    bottom_row = [InlineKeyboardButton(text="❌ Удалить", callback_data=f"hw_delete_ask:{hw_id}:{archive_flag}:{page}")]
    back_row = [InlineKeyboardButton(
        text="🔙 Назад к списку",
        callback_data=f"hw_page:{'arc' if is_archive else 'act'}:{page}",
    )]

    return InlineKeyboardMarkup(inline_keyboard=[top_row, files_row, bottom_row, back_row])


# --- 📎 Homework attachments -------------------------------------------------

def get_attachment_collect_keyboard(count: int) -> InlineKeyboardMarkup:
    """
    Keyboard for the "send me files" step of the add-homework flow. The finish
    button doubles as the "skip" action when nothing has been attached yet, so
    there is always exactly one obvious way forward.
    """
    done_text = "✅ Готово" if count else "⏭ Пропустить (без вложений)"
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=done_text, callback_data="hwa_files_done")],
        [InlineKeyboardButton(text="❌ Отмена", callback_data="hw_list_active")],
    ])


def get_attachment_menu_keyboard(
    attachments, hw_id: int, is_archive: bool, page: int, can_add: bool = True
) -> InlineKeyboardMarkup:
    """
    Manage one homework's attachments: a delete button per file, an "add" button
    while under the limit, and back to the card.
    """
    archive_flag = 1 if is_archive else 0
    rows = []
    for index, attachment in enumerate(attachments, 1):
        icon = "🖼" if attachment.file_type == "photo" else "📄"
        label = attachment.file_name or (f"Фото {index}" if attachment.file_type == "photo" else f"Файл {index}")
        rows.append([InlineKeyboardButton(
            text=f"🗑 {icon} {label}"[:64],
            callback_data=f"hw_att_del_ask:{attachment.id}:{hw_id}:{archive_flag}:{page}",
        )])
    if can_add:
        rows.append([InlineKeyboardButton(
            text="➕ Добавить вложение",
            callback_data=f"hw_att_add:{hw_id}:{archive_flag}:{page}",
        )])
    rows.append([InlineKeyboardButton(
        text="🔙 Назад к заданию",
        callback_data=f"hw_view_actions:{hw_id}:{archive_flag}:{page}",
    )])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def get_attachment_delete_confirm_keyboard(
    attachment_id: int, hw_id: int, is_archive: bool, page: int
) -> InlineKeyboardMarkup:
    archive_flag = 1 if is_archive else 0
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(
            text="⚠️ Да, удалить",
            callback_data=f"hw_att_del:{attachment_id}:{hw_id}:{archive_flag}:{page}",
        ),
        InlineKeyboardButton(
            text="❌ Отмена",
            callback_data=f"hw_att_menu:{hw_id}:{archive_flag}:{page}",
        ),
    ]])

def get_homework_delete_confirm_keyboard(hw_id: int, is_archive: bool, page: int) -> InlineKeyboardMarkup:
    archive_flag = 1 if is_archive else 0
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="⚠️ Да, удалить", callback_data=f"hw_delete_confirm:{hw_id}:{archive_flag}:{page}"),
            InlineKeyboardButton(text="❌ Отмена", callback_data=f"hw_view_actions:{hw_id}:{archive_flag}:{page}"),
        ]
    ])

def get_homework_edit_menu_keyboard(hw_id: int, is_archive: bool = False, page: int = 0) -> InlineKeyboardMarkup:
    archive_flag = 1 if is_archive else 0
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📚 Предмет", callback_data=f"hw_edit_field:{hw_id}:subject:{archive_flag}:{page}")],
        [InlineKeyboardButton(text="📝 Описание", callback_data=f"hw_edit_field:{hw_id}:desc:{archive_flag}:{page}")],
        [InlineKeyboardButton(text="📅 Дата сдачи", callback_data=f"hw_edit_field:{hw_id}:date:{archive_flag}:{page}")],
        [InlineKeyboardButton(text="🔙 Назад", callback_data=f"hw_view_actions:{hw_id}:{archive_flag}:{page}")],
    ])

def get_settings_keyboard(chat) -> InlineKeyboardMarkup:
    """
    Reminder-only keyboard ("⏰ Напоминания"), built from the ``Chat`` row. Each
    of the five categories has an on/off toggle; the timed ones also have a "set
    time" button, plus a quiet-hours button. General settings (timezone, edit
    policy, history, data, reset) live behind the "⚙️ Настройки" button so this
    screen stays focused on notifications only.
    """
    def mark(enabled: bool) -> str:
        return "🟢" if enabled else "🔴"

    quiet_label = (
        f"{chat.quiet_start}–{chat.quiet_end}"
        if chat.quiet_start and chat.quiet_end else "выкл"
    )
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text=f"{mark(chat.hw_reminder_enabled)} ДЗ на завтра", callback_data="set_toggle:hw"),
            InlineKeyboardButton(text=f"🕒 {chat.hw_reminder_time}", callback_data="set_hw_rem"),
        ],
        [
            InlineKeyboardButton(text=f"{mark(chat.schedule_reminder_enabled)} Портфель на завтра", callback_data="set_toggle:sched"),
            InlineKeyboardButton(text=f"🕒 {chat.schedule_reminder_time}", callback_data="set_sch_rem"),
        ],
        [
            InlineKeyboardButton(text=f"{mark(chat.hw_duetoday_enabled)} ДЗ в день сдачи", callback_data="set_toggle:duetoday"),
            InlineKeyboardButton(text=f"🕒 {chat.hw_duetoday_time}", callback_data="set_duetoday_time"),
        ],
        [
            InlineKeyboardButton(text=f"{mark(chat.changes_reminder_enabled)} Изменения в расписании", callback_data="set_toggle:changes"),
        ],
        [
            InlineKeyboardButton(text=f"{mark(chat.extra_reminder_enabled)} Напоминания о доп. занятиях", callback_data="set_toggle:extra"),
        ],
        [
            InlineKeyboardButton(text=f"🌙 Тихие часы: {quiet_label}", callback_data="set_quiet"),
        ],
        [
            InlineKeyboardButton(text="🔄 Обновить", callback_data="set_reminders"),
            InlineKeyboardButton(text="⚙️ Настройки", callback_data="set_general"),
        ],
    ])


def get_general_settings_keyboard(chat) -> InlineKeyboardMarkup:
    """
    General-settings keyboard ("⚙️ Настройки"): timezone, homework-edit policy,
    change history, data/backup and the full reset. Notification categories live
    behind the "⏰ Напоминания" button so the two screens don't duplicate.
    """
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="⏰ Напоминания", callback_data="set_reminders"),
        ],
        [
            InlineKeyboardButton(
                text=f"🌍 Часовой пояс: {chat.timezone}", callback_data="set_tz"
            ),
        ],
        [
            InlineKeyboardButton(
                text=f"✍️ Права на ДЗ: {POLICY_LABELS[normalize_policy(chat.hw_edit_policy)]}",
                callback_data="set_hw_policy",
            ),
        ],
        [
            InlineKeyboardButton(text="📜 История изменений", callback_data="au_open"),
        ],
        [
            InlineKeyboardButton(text="💾 Данные и резервная копия", callback_data="bk_menu"),
        ],
        [
            InlineKeyboardButton(text="🔄 Обновить", callback_data="set_general"),
            InlineKeyboardButton(text="⚙️ Сбросить всё", callback_data="set_reset_all"),
        ],
    ])


# --- 💾 Данные и резервная копия --------------------------------------------

def get_backup_menu_keyboard() -> InlineKeyboardMarkup:
    """Export/import entry points. Every tap is admin-gated server-side."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📦 Резервная копия (JSON)", callback_data="bk_json")],
        [
            InlineKeyboardButton(text="📊 Расписание CSV", callback_data="bk_csv"),
            InlineKeyboardButton(text="📅 Календарь ICS", callback_data="bk_ics"),
        ],
        [InlineKeyboardButton(text="📜 Выгрузить историю", callback_data="bk_audit")],
        [InlineKeyboardButton(text="⬆️ Восстановить из файла", callback_data="bk_import")],
        [InlineKeyboardButton(text="🔙 Назад к настройкам", callback_data="set_general")],
    ])


def get_backup_mode_keyboard() -> InlineKeyboardMarkup:
    """Merge vs replace, after a file has been read and validated."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="➕ Дополнить", callback_data="bk_mode:merge")],
        [InlineKeyboardButton(text="♻️ Заменить всё", callback_data="bk_mode:replace")],
        [InlineKeyboardButton(text="❌ Отмена", callback_data="bk_menu")],
    ])


def get_backup_import_confirm_keyboard(mode: str, first_step: bool = True) -> InlineKeyboardMarkup:
    """
    Confirmation under the preview report.

    ``merge`` is confirmed once; ``replace`` deletes everything first, so its
    first button only leads to a second, explicit confirmation.
    """
    if mode == "replace" and first_step:
        yes = InlineKeyboardButton(text="♻️ Далее", callback_data="bk_replace_ask2")
    elif mode == "replace":
        yes = InlineKeyboardButton(text="⚠️ Да, заменить всё", callback_data="bk_apply:replace")
    else:
        yes = InlineKeyboardButton(text="✅ Да, дополнить", callback_data="bk_apply:merge")
    return InlineKeyboardMarkup(inline_keyboard=[[
        yes, InlineKeyboardButton(text="❌ Отмена", callback_data="bk_menu"),
    ]])


def get_hw_policy_keyboard(current: str) -> InlineKeyboardMarkup:
    """
    Picker for the homework-edit policy. The active one is marked; the choice is
    still validated server-side when the button is tapped.
    """
    current = normalize_policy(current)
    rows = []
    for policy in HW_EDIT_POLICIES:
        mark = "• " if policy == current else ""
        rows.append([InlineKeyboardButton(
            text=f"{mark}{POLICY_LABELS[policy]}", callback_data=f"set_hw_policy_set:{policy}"
        )])
    rows.append([InlineKeyboardButton(text="🔙 Назад к настройкам", callback_data="set_general")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


# --- 🌍 Часовой пояс ---------------------------------------------------------

def get_timezone_keyboard(current: Optional[str] = None) -> InlineKeyboardMarkup:
    """
    Picker of common zones (the active one marked), plus "type it by hand" for
    anything else. Whatever arrives is still validated server-side.
    """
    rows = []
    row = []
    for name, label in POPULAR_TIMEZONES:
        mark = "• " if name == current else ""
        # The zone name is passed as an index-free literal: IANA names are short
        # enough to stay well inside Telegram's 64-byte callback_data limit.
        row.append(InlineKeyboardButton(text=f"{mark}{label}", callback_data=f"set_tz_pick:{name}"))
        if len(row) == 2:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append([InlineKeyboardButton(text="✏️ Ввести вручную", callback_data="set_tz_manual")])
    rows.append([InlineKeyboardButton(text="🔙 Назад к настройкам", callback_data="set_general")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def get_timezone_confirm_keyboard(tz_name: str) -> InlineKeyboardMarkup:
    """Confirm/cancel for a chosen zone, shown together with its local time."""
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="✅ Сохранить", callback_data=f"set_tz_save:{tz_name}"),
        InlineKeyboardButton(text="❌ Отмена", callback_data="set_tz"),
    ]])


# --- 📜 История (audit log) -------------------------------------------------

# Filter tabs of the history screen: (callback key, button label). "all" is the
# unfiltered view; the rest map 1:1 onto services.audit entity types.
AUDIT_FILTERS = (
    ("all", "🗂 Все"),
    ("homework", "📝 ДЗ"),
    ("extra", "🎯 Доп."),
    ("schedule", "📅 Расписание"),
    ("lesson_override", "🗓 По датам"),
    ("settings", "⚙️ Настройки"),
)


def get_audit_keyboard(current_filter: str, page: int, total_pages: int) -> InlineKeyboardMarkup:
    """Filter tabs + pager for the history screen."""
    rows = []
    row = []
    for key, label in AUDIT_FILTERS:
        mark = "• " if key == current_filter else ""
        row.append(InlineKeyboardButton(text=f"{mark}{label}", callback_data=f"au_page:{key}:0"))
        if len(row) == 3:
            rows.append(row)
            row = []
    if row:
        rows.append(row)

    if total_pages > 1:
        nav = []
        if page > 0:
            nav.append(InlineKeyboardButton(
                text="◀️", callback_data=f"au_page:{current_filter}:{page - 1}"
            ))
        nav.append(InlineKeyboardButton(text=f"{page + 1}/{total_pages}", callback_data="au_noop"))
        if page < total_pages - 1:
            nav.append(InlineKeyboardButton(
                text="▶️", callback_data=f"au_page:{current_filter}:{page + 1}"
            ))
        rows.append(nav)

    rows.append([InlineKeyboardButton(text="🔙 Назад к настройкам", callback_data="set_general")])
    return InlineKeyboardMarkup(inline_keyboard=rows)

# --- Extra activities (clubs / tutors / sections) --------------------------

def get_extra_list_keyboard(activities, can_manage: bool = True) -> InlineKeyboardMarkup:
    """
    ``activities`` is a list of objects exposing ``.id``/``.title``. Every user
    can open an item; the "➕ Добавить" button is only shown when ``can_manage``
    (the tap itself is still admin-gated server-side as a defence in depth).
    """
    buttons = []
    for a in activities:
        buttons.append([InlineKeyboardButton(text=f"🎯 {a.title}", callback_data=f"ea_view:{a.id}")])
    if can_manage:
        buttons.append([InlineKeyboardButton(text="➕ Добавить занятие", callback_data="ea_add")])
    buttons.append([InlineKeyboardButton(text="🔄 Обновить список", callback_data="ea_list")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def get_extra_kind_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔁 Еженедельно", callback_data="ea_kind:weekly")],
        [InlineKeyboardButton(text="📆 Разово (по дате)", callback_data="ea_kind:once")],
        [InlineKeyboardButton(text="❌ Отмена", callback_data="ea_cancel")],
    ])


def get_extra_day_keyboard(callback_prefix: str = "ea_day") -> InlineKeyboardMarkup:
    """Weekday picker. ``callback_prefix`` lets the same keyboard serve both the
    add flow (``ea_day:{i}``) and the edit flow (``ea_setday:{id}:{i}``)."""
    buttons = []
    row = []
    for i, name in enumerate(DAYS_SHORT_RU):
        row.append(InlineKeyboardButton(text=name, callback_data=f"{callback_prefix}:{i}"))
        if len(row) == 4:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)
    buttons.append([InlineKeyboardButton(text="❌ Отмена", callback_data="ea_cancel")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def get_extra_action_keyboard(activity_id: int, can_manage: bool = True) -> InlineKeyboardMarkup:
    buttons = []
    if can_manage:
        buttons.append([
            InlineKeyboardButton(text="✏️ Редактировать", callback_data=f"ea_edit_menu:{activity_id}"),
            InlineKeyboardButton(text="❌ Удалить", callback_data=f"ea_delete_ask:{activity_id}"),
        ])
    buttons.append([InlineKeyboardButton(text="🔙 Назад к списку", callback_data="ea_list")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def get_extra_edit_menu_keyboard(activity_id: int, kind: str) -> InlineKeyboardMarkup:
    when_label = "📅 День недели" if kind == "weekly" else "📆 Дата"
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🏷 Название", callback_data=f"ea_edit_field:{activity_id}:title")],
        [InlineKeyboardButton(text=when_label, callback_data=f"ea_edit_field:{activity_id}:when")],
        [InlineKeyboardButton(text="🕒 Время", callback_data=f"ea_edit_field:{activity_id}:time")],
        [InlineKeyboardButton(text="📍 Место", callback_data=f"ea_edit_field:{activity_id}:location")],
        [InlineKeyboardButton(text="📝 Примечание", callback_data=f"ea_edit_field:{activity_id}:note")],
        [InlineKeyboardButton(text="🔔 Напоминание", callback_data=f"ea_rem_menu:{activity_id}")],
        [InlineKeyboardButton(text="🔙 Назад", callback_data=f"ea_view:{activity_id}")],
    ])


# Preset "minutes before" choices for an extra-activity reminder.
EXTRA_REMINDER_PRESETS = [(0, "в начале"), (15, "за 15 мин"), (30, "за 30 мин"),
                          (60, "за 1 ч"), (120, "за 2 ч"), (1440, "за сутки")]


def get_extra_reminder_keyboard(activity) -> InlineKeyboardMarkup:
    aid = activity.id
    toggle = "🔕 Выключить напоминание" if activity.reminder_enabled else "🔔 Включить напоминание"
    rows = [[InlineKeyboardButton(text=toggle, callback_data=f"ea_rem_toggle:{aid}")]]
    if activity.reminder_enabled:
        row = []
        for minutes, label in EXTRA_REMINDER_PRESETS:
            mark = "• " if activity.reminder_minutes == minutes else ""
            row.append(InlineKeyboardButton(text=f"{mark}{label}", callback_data=f"ea_rem_min:{aid}:{minutes}"))
            if len(row) == 2:
                rows.append(row)
                row = []
        if row:
            rows.append(row)
        rows.append([InlineKeyboardButton(text="✏️ Другое кол-во минут", callback_data=f"ea_rem_custom:{aid}")])
    rows.append([InlineKeyboardButton(text="🔙 Назад", callback_data=f"ea_edit_menu:{aid}")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def get_extra_delete_confirm_keyboard(activity_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="⚠️ Да, удалить", callback_data=f"ea_delete_confirm:{activity_id}"),
            InlineKeyboardButton(text="❌ Отмена", callback_data=f"ea_view:{activity_id}"),
        ]
    ])


# --- Date overrides (🗓 Изменения по датам) --------------------------------


def get_date_grid_keyboard(start_offset: int, today: datetime.date, change_dates=None) -> InlineKeyboardMarkup:
    """
    A 14-day date picker starting at ``today + start_offset`` days, 3 buttons
    per row, labelled like "Пн 28.07". Dates that already carry a change are
    marked with "●". Includes a prev/next pager and a back button.
    """
    change_dates = change_dates or set()
    buttons = []
    row = []
    for i in range(14):
        d = today + datetime.timedelta(days=start_offset + i)
        mark = "● " if d in change_dates else ""
        label = f"{mark}{DAYS_SHORT_RU[d.weekday()]} {d.strftime('%d.%m')}"
        row.append(InlineKeyboardButton(text=label, callback_data=f"do_date:{d.isoformat()}"))
        if len(row) == 3:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)

    pager = []
    if start_offset > 0:
        prev_offset = max(0, start_offset - 14)
        pager.append(InlineKeyboardButton(text="◀️ Раньше", callback_data=f"do_days:{prev_offset}"))
    pager.append(InlineKeyboardButton(text="Позже ▶️", callback_data=f"do_days:{start_offset + 14}"))
    buttons.append(pager)

    buttons.append([InlineKeyboardButton(text="🔙 К расписанию", callback_data="sch_day:0")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def get_date_editor_keyboard(iso: str, has_lessons: bool, has_changes: bool) -> InlineKeyboardMarkup:
    """Actions for one date's overrides. ``iso`` is an ISO date string."""
    buttons = []
    if has_lessons:
        buttons.append([
            InlineKeyboardButton(text="🚫 Отменить урок", callback_data=f"do_pick:{iso}:cancel"),
            InlineKeyboardButton(text="🔄 Заменить предмет", callback_data=f"do_pick:{iso}:replace"),
        ])
        buttons.append([
            InlineKeyboardButton(text="🕒 Изменить время", callback_data=f"do_pick:{iso}:retime"),
            InlineKeyboardButton(text="➕ Добавить урок", callback_data=f"do_add:{iso}"),
        ])
    else:
        buttons.append([InlineKeyboardButton(text="➕ Добавить урок", callback_data=f"do_add:{iso}")])
    buttons.append([InlineKeyboardButton(text="📅 Тип дня (свободный / праздник…)", callback_data=f"do_dtype:{iso}")])
    if has_changes:
        buttons.append([InlineKeyboardButton(text="🗑 Очистить изменения на дату", callback_data=f"do_clear_ask:{iso}")])
    buttons.append([InlineKeyboardButton(text="🔙 К выбору даты", callback_data="do_menu")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def get_lesson_pick_keyboard(iso: str, op: str, lessons) -> InlineKeyboardMarkup:
    """
    ``lessons`` is a list of (lesson_number, label) to pick from for ``op``
    (cancel / replace / retime). Falls back to a back button when empty.
    """
    buttons = []
    for num, label in lessons:
        buttons.append([InlineKeyboardButton(text=label, callback_data=f"do_lesson:{iso}:{op}:{num}")])
    buttons.append([InlineKeyboardButton(text="🔙 Назад", callback_data=f"do_date:{iso}")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def get_day_type_keyboard(iso: str, has_day_type: bool) -> InlineKeyboardMarkup:
    buttons = [
        [InlineKeyboardButton(text="🟢 Свободный день", callback_data=f"do_setdtype:{iso}:free")],
        [InlineKeyboardButton(text="🎉 Праздник", callback_data=f"do_setdtype:{iso}:holiday")],
        [InlineKeyboardButton(text="🏖 Каникулы", callback_data=f"do_setdtype:{iso}:vacation")],
        [InlineKeyboardButton(text="💻 Дистанционный день", callback_data=f"do_setdtype:{iso}:remote")],
    ]
    if has_day_type:
        buttons.append([InlineKeyboardButton(text="🧹 Убрать тип дня", callback_data=f"do_rmday_ask:{iso}")])
    buttons.append([InlineKeyboardButton(text="🔙 Назад", callback_data=f"do_date:{iso}")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def get_override_preview_keyboard(iso: str) -> InlineKeyboardMarkup:
    """Save / cancel for a pending change, shown together with the before/after preview."""
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="✅ Сохранить", callback_data=f"do_save:{iso}"),
        InlineKeyboardButton(text="❌ Отмена", callback_data=f"do_date:{iso}"),
    ]])


def get_confirm_keyboard(yes_callback: str, no_callback: str, yes_text: str = "⚠️ Да") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text=yes_text, callback_data=yes_callback),
        InlineKeyboardButton(text="❌ Отмена", callback_data=no_callback),
    ]])


def get_cancel_keyboard(callback_data: str = "cancel_action") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="❌ Отмена", callback_data=callback_data)]
        ]
    )

def get_onboarding_start_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🚀 Начать настройку", callback_data="ob_start")]
        ]
    )

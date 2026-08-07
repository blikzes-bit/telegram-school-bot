"""``/payments`` — what has to be paid, right inside the chat.

Deliberately a small surface: a list, and one tap to mark something paid or
unpaid. Creating and editing entries lives in the Mini App, where a form with an
amount, a date and a period is far less painful than a chat dialogue — the bot's
job here is to *tell* you and to let you tick things off.

Only shown in a chat whose profile has a money side (the tutor profile); the
screen is gated server-side by ``can_edit_payments`` for every change, so a stale
button cannot spend anybody's rights.
"""
from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import (
    CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message,
)

import services.audit as audit
import services.timeservice as ts
from database.db import (
    get_or_create_chat, get_payments, get_payment_by_id, set_payment_paid,
)
from services import profiles
from services.permissions import capabilities_for_event
from utils import html_escape, safe_edit_text

router = Router()

NOT_FOR_THIS_CHAT = (
    "💳 В этом чате оплаты нет. Она включается в режиме «Занятия с репетитором» "
    "(⚙️ Настройки → 🧩 Режим)."
)
NO_RIGHTS = "🚫 Отмечать оплату может владелец чата или редактор."
ADD_HINT = "Добавить или изменить запись можно в приложении: /web"


def _status_mark(status: str) -> str:
    return {
        "paid": "✅",
        "overdue": "🔴",
        "due_soon": "🟡",
        "upcoming": "•",
    }.get(status, "•")


async def render_payments(chat_id: int, chat_type: str) -> tuple[str, InlineKeyboardMarkup]:
    """The screen: unpaid entries first (they are the point), then paid ones."""
    today = await ts.today_for_chat_id(chat_id)
    rows = await get_payments(chat_id)

    unpaid = [p for p in rows if not p.is_paid]
    paid = [p for p in rows if p.is_paid]

    if not rows:
        text = (
            "💳 <b>Оплата</b>\n\n"
            "Пока ничего не записано.\n\n" + ADD_HINT
        )
        return text, InlineKeyboardMarkup(inline_keyboard=[])

    lines = ["💳 <b>Оплата</b>", ""]
    buttons = []
    for payment in unpaid:
        status = profiles.payment_status(
            payment.due_date, today, False, payment.remind_days_before
        )
        lines.append(
            f"{_status_mark(status)} <b>{html_escape(payment.title)}</b> — "
            f"{html_escape(profiles.format_amount(payment.amount_minor, payment.currency))}"
            f", до {payment.due_date.strftime('%d.%m')}"
        )
        if payment.note:
            lines.append(f"   <i>{html_escape(payment.note)}</i>")
        buttons.append([InlineKeyboardButton(
            text=f"✅ Оплачено: {payment.title[:24]}",
            callback_data=f"pay_done:{payment.id}",
        )])

    if paid:
        lines += ["", "<b>Уже оплачено:</b>"]
        for payment in paid[-5:]:  # a short tail; the full history is in the app
            lines.append(
                f"✅ {html_escape(payment.title)} — "
                f"{html_escape(profiles.format_amount(payment.amount_minor, payment.currency))}"
            )

    lines += ["", ADD_HINT]
    return "\n".join(lines), InlineKeyboardMarkup(inline_keyboard=buttons)


@router.message(Command("payments"))
@router.message(F.text == "💳 Оплата")
async def show_payments(message: Message, state: FSMContext):
    await state.clear()
    chat = await get_or_create_chat(message.chat.id, message.chat.type)
    if not profiles.features_for(chat).payments:
        await message.answer(NOT_FOR_THIS_CHAT)
        return
    text, kb = await render_payments(message.chat.id, message.chat.type)
    await message.answer(text, reply_markup=kb, parse_mode="HTML")


@router.callback_query(F.data.startswith("pay_done:"))
async def mark_paid(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    chat_id = callback.message.chat.id
    chat = await get_or_create_chat(chat_id, callback.message.chat.type)

    caps = await capabilities_for_event(callback.bot, chat, callback.from_user.id)
    if not caps.can_edit_payments:
        await callback.answer(NO_RIGHTS, show_alert=True)
        return

    try:
        payment_id = int(callback.data.split(":", 1)[1])
    except (ValueError, IndexError):
        await callback.answer("⚠️ Кнопка устарела.", show_alert=True)
        return

    payment = await get_payment_by_id(chat_id, payment_id)
    if payment is None:
        await callback.answer("⚠️ Эта запись уже удалена.", show_alert=True)
        text, kb = await render_payments(chat_id, callback.message.chat.type)
        await safe_edit_text(callback.message, text, reply_markup=kb, parse_mode="HTML")
        return

    actor_user_id, actor_name = audit.actor_from(callback)
    await set_payment_paid(
        chat_id, payment_id, True, paid_at=ts.now_iso_utc(),
        actor_user_id=actor_user_id, actor_name=actor_name,
    )
    await audit.record_event(
        callback, chat_id, audit.ENTITY_PAYMENT, audit.ACTION_COMPLETE,
        entity_id=payment_id, summary=audit.summarize(payment.title),
    )
    text, kb = await render_payments(chat_id, callback.message.chat.type)
    await safe_edit_text(callback.message, text, reply_markup=kb, parse_mode="HTML")
    await callback.answer("Отмечено как оплаченное")

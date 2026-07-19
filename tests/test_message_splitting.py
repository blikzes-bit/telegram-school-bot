"""Fix #2: 4096-char message limit handling for lists and reminders."""
import datetime

import pytz

from utils import split_message, MAX_MESSAGE_LENGTH
from config import TIMEZONE
from database.db import get_or_create_chat, add_homework
from handlers.homework import format_homework_list

tz = pytz.timezone(TIMEZONE)
CHAT_ID = 555


def test_short_text_not_split():
    assert split_message("hello") == ["hello"]


def test_split_respects_limit():
    # 300 paragraphs of 50 chars → well over 4096.
    text = "\n\n".join(f"paragraph number {i} " + "x" * 30 for i in range(300))
    chunks = split_message(text)
    assert len(chunks) > 1
    for chunk in chunks:
        assert len(chunk) <= MAX_MESSAGE_LENGTH


def test_split_prefers_line_boundaries():
    line = "a" * 100
    text = "\n".join(line for _ in range(100))  # 100 lines, ~10100 chars
    chunks = split_message(text)
    # No chunk should end mid-line (each line is intact 'a'*100 blocks).
    for chunk in chunks:
        assert len(chunk) <= MAX_MESSAGE_LENGTH
    assert "".join(c.replace("\n", "") for c in chunks) == text.replace("\n", "")


def test_hard_cut_for_single_huge_line():
    text = "z" * (MAX_MESSAGE_LENGTH * 2 + 500)
    chunks = split_message(text)
    assert all(len(c) <= MAX_MESSAGE_LENGTH for c in chunks)
    assert "".join(chunks) == text


async def test_homework_list_paginates_over_limit(db):
    await get_or_create_chat(CHAT_ID, "private")
    today = datetime.datetime.now(tz).date()
    due = today + datetime.timedelta(days=3)

    # 40 homeworks with long descriptions => far more than one 4096 message.
    for i in range(40):
        await add_homework(CHAT_ID, f"Subject {i}", due, "D" * 400)

    # Walk all pages; each rendered page must fit within Telegram's limit,
    # and its keyboard buttons must correspond to items shown on that page.
    seen_pages = 0
    page = 0
    while True:
        text, kb = await format_homework_list(CHAT_ID, is_archive=False, page=page)
        assert len(text) <= MAX_MESSAGE_LENGTH
        # Count item-action buttons (hw_view_actions) on this page.
        item_buttons = [
            b for row in kb.inline_keyboard for b in row
            if b.callback_data and b.callback_data.startswith("hw_view_actions:")
        ]
        assert item_buttons, "each page should show at least one homework"
        # The page indicator "(стр. X/Y)" should be present with multiple pages.
        assert "стр." in text
        seen_pages += 1
        # Stop after the last page.
        has_next = any(
            b.callback_data == f"hw_page:act:{page + 1}"
            for row in kb.inline_keyboard for b in row
        )
        if not has_next:
            break
        page += 1
        if seen_pages > 50:  # safety valve
            break

    assert seen_pages > 1


async def test_reminder_over_limit_is_split(db, fake_bot):
    from services.scheduler import send_hw_reminder

    await get_or_create_chat(CHAT_ID, "private")
    today = datetime.datetime.now(tz).date()
    tomorrow = today + datetime.timedelta(days=1)

    for i in range(40):
        await add_homework(CHAT_ID, f"Subject {i}", tomorrow, "D" * 400)

    handled = await send_hw_reminder(fake_bot, CHAT_ID, tz)
    assert handled is True
    assert len(fake_bot.sent) > 1  # had to split into several messages
    for _chat_id, text, _kwargs in fake_bot.sent:
        assert len(text) <= MAX_MESSAGE_LENGTH

"""
Tests for middleware/access.py: the ChatContextMiddleware (which resolves/creates
the Chat row, clears a stale ``is_blocked`` flag and fills a missing class name
from the group's Telegram title) and the OnboardingGuard (which blocks feature
handlers until onboarding is complete).
"""
from types import SimpleNamespace

from database.db import get_or_create_chat, get_chat, set_onboarded
from middleware.access import ChatContextMiddleware, OnboardingGuardMiddleware

CHAT_ID = 880_500


def _update(message=None, callback_query=None):
    return SimpleNamespace(message=message, callback_query=callback_query)


async def test_chat_context_creates_and_stores_chat(db):
    captured = {}

    async def handler(event, data):
        captured.update(data)
        return "ok"

    msg = SimpleNamespace(chat=SimpleNamespace(id=CHAT_ID, type="private"))
    result = await ChatContextMiddleware()(handler, _update(message=msg), {})
    assert result == "ok"
    assert captured["chat"].chat_id == CHAT_ID
    assert await get_chat(CHAT_ID) is not None


async def test_chat_context_clears_blocked_flag(db):
    chat = await get_or_create_chat(CHAT_ID, "private")
    chat.is_blocked = True
    from database.db import AsyncSessionLocal
    async with AsyncSessionLocal() as session:
        db_chat = await session.get(type(chat), CHAT_ID)
        db_chat.is_blocked = True
        await session.commit()

    async def handler(event, data):
        return data["chat"]

    cb_msg = SimpleNamespace(chat=SimpleNamespace(id=CHAT_ID, type="private"))
    update = _update(callback_query=SimpleNamespace(message=cb_msg))
    resolved = await ChatContextMiddleware()(handler, update, {})
    assert resolved.is_blocked is False


async def test_chat_context_passes_through_without_chat(db):
    async def handler(event, data):
        return "no-chat"

    # An update with neither message nor callback (e.g. poll answer): no chat set.
    result = await ChatContextMiddleware()(handler, _update(), {})
    assert result == "no-chat"


async def test_chat_context_fills_missing_class_name_from_group_title(db):
    async def handler(event, data):
        return data["chat"]

    msg = SimpleNamespace(chat=SimpleNamespace(id=-880_600, type="group", title="9-А класс"))
    resolved = await ChatContextMiddleware()(handler, _update(message=msg), {})
    assert resolved.title == "9-А класс"
    assert (await get_chat(-880_600)).title == "9-А класс"


async def test_chat_context_never_overwrites_a_chosen_class_name(db):
    """A name set in the Mini App must survive later updates from Telegram."""
    from database.db import set_chat_title

    chat_id = -880_601
    await get_or_create_chat(chat_id, "group")
    await set_chat_title(chat_id, "Моё название")

    async def handler(event, data):
        return data["chat"]

    msg = SimpleNamespace(chat=SimpleNamespace(id=chat_id, type="group", title="Telegram Title"))
    resolved = await ChatContextMiddleware()(handler, _update(message=msg), {})
    assert resolved.title == "Моё название"
    assert (await get_chat(chat_id)).title == "Моё название"


async def test_onboarding_guard_blocks_until_onboarded(db):
    await get_or_create_chat(CHAT_ID, "private")
    chat = await get_chat(CHAT_ID)  # not onboarded yet

    answered = []

    class FakeMessage:
        async def answer(self, text, **kwargs):
            answered.append(text)

    async def handler(event, data):
        return "ran"

    guard = OnboardingGuardMiddleware()
    # Not onboarded -> handler must not run.
    blocked = await guard(handler, FakeMessage(), {"chat": chat})
    assert blocked is None
    assert answered

    # After onboarding -> handler runs.
    await set_onboarded(CHAT_ID, True)
    chat = await get_chat(CHAT_ID)
    ran = await guard(handler, FakeMessage(), {"chat": chat})
    assert ran == "ran"

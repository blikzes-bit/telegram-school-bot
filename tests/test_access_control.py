"""
Covers: regular-member vs admin permissions in a group chat, and that
private chats are never gated by the admin check.
"""
from types import SimpleNamespace

from middleware.access import require_admin, is_chat_admin

GROUP_CHAT_ID = -100500001
ADMIN_USER_ID = 111
MEMBER_USER_ID = 222


class FakeMessageEvent:
    def __init__(self, chat_id, user_id, chat_type):
        self.chat = SimpleNamespace(id=chat_id, type=chat_type)
        self.from_user = SimpleNamespace(id=user_id)
        self.answers = []

    async def answer(self, text, **kwargs):
        self.answers.append(text)


class FakeCallbackEvent:
    def __init__(self, chat_id, user_id, chat_type):
        self.message = SimpleNamespace(chat=SimpleNamespace(id=chat_id, type=chat_type))
        self.from_user = SimpleNamespace(id=user_id)
        self.alerts = []
        self.calls = []

    async def answer(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        if kwargs.get("show_alert"):
            self.alerts.append(args[0] if args else kwargs.get("text"))


async def test_is_chat_admin_private_chat_always_true(fake_bot):
    assert await is_chat_admin(fake_bot, 12345, MEMBER_USER_ID, "private") is True


async def test_is_chat_admin_group_member_false(fake_bot):
    fake_bot.admins = {ADMIN_USER_ID}
    assert await is_chat_admin(fake_bot, GROUP_CHAT_ID, MEMBER_USER_ID, "group") is False


async def test_is_chat_admin_group_admin_true(fake_bot):
    fake_bot.admins = {ADMIN_USER_ID}
    assert await is_chat_admin(fake_bot, GROUP_CHAT_ID, ADMIN_USER_ID, "group") is True


async def test_require_admin_rejects_regular_member_in_group(fake_bot):
    fake_bot.admins = {ADMIN_USER_ID}
    event = FakeCallbackEvent(GROUP_CHAT_ID, MEMBER_USER_ID, "group")
    allowed = await require_admin(event, fake_bot)
    assert allowed is False
    assert event.calls, "regular member must see a clear rejection message"


async def test_require_admin_allows_admin_in_group(fake_bot):
    fake_bot.admins = {ADMIN_USER_ID}
    event = FakeCallbackEvent(GROUP_CHAT_ID, ADMIN_USER_ID, "group")
    allowed = await require_admin(event, fake_bot)
    assert allowed is True
    assert not event.calls


async def test_require_admin_always_allows_private_chat(fake_bot):
    event = FakeCallbackEvent(12345, MEMBER_USER_ID, "private")
    allowed = await require_admin(event, fake_bot)
    assert allowed is True

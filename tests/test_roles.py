"""
App roles, the access mode and the chat owner (``services.permissions``).

Two properties carry the whole feature:

  * **Nothing changes for anybody who does not opt in.** With the default access
    mode a Telegram admin and a plain member get exactly the capabilities they
    had before roles existed — including the fact that extra activities were
    admin-only while the homework list was collaborative.
  * **Role mode is real, not cosmetic.** Somebody with no role assigned can read
    and nothing else, on *both* surfaces, and Telegram admin status stops
    mattering — which is the entire point for a family group where half the
    members happen to be admins.
"""
from types import SimpleNamespace

import pytest

from services import permissions as perms


def _chat(**kwargs):
    base = dict(
        chat_id=-500, chat_type="group", access_mode=None, owner_user_id=None,
        hw_edit_policy="collaborative",
    )
    base.update(kwargs)
    return SimpleNamespace(**base)


# --- Default (Telegram) access mode: byte-for-byte the old behaviour ----------

def test_private_chat_is_always_fully_capable():
    caps = perms.capabilities(
        _chat(chat_type="private"), user_id=7, is_telegram_admin=False
    )
    assert caps.is_owner and caps.is_admin and caps.can_manage_members


def test_telegram_admin_keeps_administrator_capabilities():
    caps = perms.capabilities(_chat(), user_id=7, is_telegram_admin=True)
    assert caps.role == perms.ROLE_TG_ADMIN
    assert caps.is_admin and caps.can_edit_schedule and caps.can_edit_extra


def test_plain_member_keeps_exactly_the_old_rights():
    """Homework was collaborative; extras, schedule and settings were admin-only."""
    caps = perms.capabilities(_chat(), user_id=8, is_telegram_admin=False)
    assert caps.role == perms.ROLE_TG_MEMBER
    assert caps.can_add_homework and caps.can_edit_homework and caps.can_complete_homework
    assert not caps.can_edit_extra
    assert not caps.can_edit_schedule
    assert not caps.is_admin
    assert not caps.can_manage_members


def test_an_app_role_is_ignored_until_the_chat_opts_in():
    """Assigning roles must not change anything while the mode is still telegram."""
    caps = perms.capabilities(
        _chat(), user_id=8, is_telegram_admin=False, app_role=perms.ROLE_VIEWER
    )
    assert caps.can_add_homework is True


# --- Role mode ----------------------------------------------------------------

def test_role_mode_makes_an_unassigned_member_read_only():
    caps = perms.capabilities(
        _chat(access_mode=perms.ACCESS_ROLES), user_id=8, is_telegram_admin=False
    )
    assert caps.role == perms.ROLE_VIEWER
    assert not any([
        caps.can_add_homework, caps.can_edit_homework, caps.can_complete_homework,
        caps.can_edit_extra, caps.can_edit_schedule, caps.can_manage_members,
    ])


def test_role_mode_ignores_telegram_admin_status():
    """The point of role mode: rights stop following Telegram admin rights."""
    caps = perms.capabilities(
        _chat(access_mode=perms.ACCESS_ROLES), user_id=8, is_telegram_admin=True
    )
    assert caps.role == perms.ROLE_VIEWER
    assert not caps.is_admin


@pytest.mark.parametrize(
    "role,expected",
    [
        (perms.ROLE_EDITOR, dict(
            can_add_homework=True, can_edit_homework=True, can_complete_homework=True,
            can_edit_extra=True, can_edit_schedule=False, can_manage_members=False,
        )),
        (perms.ROLE_STUDENT, dict(
            can_add_homework=False, can_edit_homework=False, can_complete_homework=True,
            can_edit_extra=False, can_edit_schedule=False, can_manage_members=False,
        )),
        (perms.ROLE_VIEWER, dict(
            can_add_homework=False, can_edit_homework=False, can_complete_homework=False,
            can_edit_extra=False, can_edit_schedule=False, can_manage_members=False,
        )),
    ],
)
def test_each_role_grants_exactly_its_capabilities(role, expected):
    caps = perms.capabilities(
        _chat(access_mode=perms.ACCESS_ROLES), user_id=8,
        is_telegram_admin=False, app_role=role,
    )
    for field, value in expected.items():
        assert getattr(caps, field) is value, field


def test_a_student_may_tick_homework_off_but_not_rewrite_it():
    caps = perms.capabilities(
        _chat(access_mode=perms.ACCESS_ROLES), user_id=8,
        is_telegram_admin=False, app_role=perms.ROLE_STUDENT,
    )
    assert caps.can_complete_homework is True
    assert caps.can_edit_homework is False


def test_the_owner_can_never_be_locked_out_of_their_own_chat():
    """Even in role mode, with no role assigned to them at all."""
    caps = perms.capabilities(
        _chat(access_mode=perms.ACCESS_ROLES, owner_user_id=9),
        user_id=9, is_telegram_admin=False, app_role=None,
    )
    assert caps.is_owner and caps.is_admin and caps.can_manage_members


def test_an_unknown_stored_role_degrades_to_read_only():
    caps = perms.capabilities(
        _chat(access_mode=perms.ACCESS_ROLES), user_id=8,
        is_telegram_admin=False, app_role="superuser",
    )
    assert caps.role == perms.ROLE_VIEWER


def test_an_unknown_access_mode_falls_back_to_telegram():
    assert perms.normalize_access_mode("something-else") == perms.ACCESS_TELEGRAM
    caps = perms.capabilities(
        _chat(access_mode="something-else"), user_id=8, is_telegram_admin=True
    )
    assert caps.is_admin is True


def test_ownership_is_not_an_assignable_role():
    """Owners follow ``chats.owner_user_id``, not a dropdown."""
    assert perms.ROLE_OWNER not in perms.ASSIGNABLE_ROLES
    assert set(perms.ASSIGNABLE_ROLES) == {
        perms.ROLE_EDITOR, perms.ROLE_STUDENT, perms.ROLE_VIEWER,
    }


def test_every_role_has_a_label_and_a_description():
    for role in perms.APP_ROLES:
        assert perms.ROLE_LABELS.get(role)
        assert perms.ROLE_DESCRIPTIONS.get(role)


# --- The bot obeys the same roles ---------------------------------------------
# Without this the whole feature would be theatre: the owner locks the class
# down in the app, and everyone keeps editing from the chat as before.

class _FakeCallback:
    def __init__(self, chat_id, user_id, bot, chat_type="group"):
        self.message = SimpleNamespace(
            chat=SimpleNamespace(id=chat_id, type=chat_type)
        )
        self.data = None
        self.bot = bot
        self.from_user = SimpleNamespace(id=user_id, full_name="Tester")
        self.alerts = []

    async def answer(self, *args, **kwargs):
        if kwargs.get("show_alert"):
            self.alerts.append(args[0] if args else kwargs.get("text"))


async def test_require_admin_in_role_mode_refuses_a_telegram_admin(db):
    from database.db import get_or_create_chat, set_access_mode, set_chat_owner
    from middleware.access import require_admin
    from tests.conftest import FakeBot

    chat_id, owner_id, other_admin = -510_001, 1, 2
    await get_or_create_chat(chat_id, "group")
    await set_chat_owner(chat_id, owner_id)
    await set_access_mode(chat_id, perms.ACCESS_ROLES)
    bot = FakeBot(admins={owner_id, other_admin})

    denied = _FakeCallback(chat_id, other_admin, bot)
    assert await require_admin(denied, bot) is False
    assert denied.alerts, "the refusal must explain itself"

    allowed = _FakeCallback(chat_id, owner_id, bot)
    assert await require_admin(allowed, bot) is True


async def test_bot_homework_edit_respects_the_viewer_role(db):
    from database.db import (
        add_homework, get_or_create_chat, set_access_mode, set_chat_owner,
        upsert_membership, set_member_app_role,
    )
    from services.audit import now_iso
    from services.permissions import can_edit_homework
    from tests.conftest import FakeBot
    import datetime

    chat_id, owner_id, viewer_id, editor_id = -510_002, 1, 2, 3
    chat = await get_or_create_chat(chat_id, "group")
    await set_chat_owner(chat_id, owner_id)
    await set_access_mode(chat_id, perms.ACCESS_ROLES)
    for uid, role in ((viewer_id, perms.ROLE_VIEWER), (editor_id, perms.ROLE_EDITOR)):
        await upsert_membership(chat_id, uid, "member", now_iso())
        await set_member_app_role(chat_id, uid, role)

    hw = await add_homework(chat_id, "Физика", datetime.date(2024, 1, 15), "опыт")
    from database.db import get_chat
    chat = await get_chat(chat_id)
    bot = FakeBot(admins={viewer_id})  # a Telegram admin, deliberately

    assert await can_edit_homework(bot, chat, hw, viewer_id) is False
    assert await can_edit_homework(bot, chat, hw, editor_id) is True
    assert await can_edit_homework(bot, chat, hw, owner_id) is True


async def test_bot_student_can_complete_but_not_edit(db):
    from database.db import (
        add_homework, get_chat, get_or_create_chat, set_access_mode,
        set_chat_owner, set_member_app_role, upsert_membership,
    )
    from services.audit import now_iso
    from services.permissions import can_edit_homework
    from tests.conftest import FakeBot
    import datetime

    chat_id, owner_id, student_id = -510_003, 1, 4
    await get_or_create_chat(chat_id, "group")
    await set_chat_owner(chat_id, owner_id)
    await set_access_mode(chat_id, perms.ACCESS_ROLES)
    await upsert_membership(chat_id, student_id, "member", now_iso())
    await set_member_app_role(chat_id, student_id, perms.ROLE_STUDENT)

    hw = await add_homework(chat_id, "Химия", datetime.date(2024, 1, 15), "опыт")
    chat = await get_chat(chat_id)
    bot = FakeBot()

    assert await can_edit_homework(bot, chat, hw, student_id, completing=True) is True
    assert await can_edit_homework(bot, chat, hw, student_id) is False

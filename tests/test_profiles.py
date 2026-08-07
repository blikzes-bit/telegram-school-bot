"""
Chat profiles (``services.profiles`` + ``chats.profile``).

The load-bearing property is **backwards compatibility**: a chat with
``profile IS NULL`` — i.e. every chat that existed before this feature — must
behave exactly as it did before, resolving to the profile its chat type implies.
The rest covers the onboarding shortcut for the tutor profile (no bell times at
all) and the rule that a profile's starting settings apply only when the profile
actually changes.
"""
from types import SimpleNamespace

from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.base import StorageKey
from aiogram.fsm.storage.memory import MemoryStorage

from database.db import (
    finalize_onboarding, get_chat, get_lesson_slots, get_or_create_chat,
    get_schedule, set_chat_profile, set_hw_edit_policy,
)
from handlers.onboarding import OnboardingStates, process_profile_choice
from services import profiles
from tests.conftest import FakeBot

CHAT_ID = 710_001
GROUP_ID = -710_002


class FakeMessage:
    def __init__(self, chat_id, text=None, chat_type="private"):
        self.text = text
        self.chat = SimpleNamespace(id=chat_id, type=chat_type)
        self.answers = []

    async def answer(self, text, **kwargs):
        self.answers.append((text, kwargs))
        return self


class FakeCallback:
    def __init__(self, message, data=None):
        self.message = message
        self.data = data
        # A group chat gates these flows on admin rights, so the fake bot must
        # answer getChatMember — see tests/conftest.FakeBot.
        self.from_user = SimpleNamespace(id=abs(message.chat.id), full_name="Tester")
        self.bot = FakeBot(admins={self.from_user.id})
        self.alerts = []
        self.acks = []

    async def answer(self, *args, **kwargs):
        if kwargs.get("show_alert"):
            self.alerts.append(args[0] if args else kwargs.get("text"))
        else:
            self.acks.append(args[0] if args else None)


def _state(chat_id=CHAT_ID):
    return FSMContext(
        storage=MemoryStorage(),
        key=StorageKey(bot_id=1, chat_id=chat_id, user_id=abs(chat_id)),
    )


# --- Resolution ---------------------------------------------------------------

def test_null_profile_resolves_from_chat_type():
    """Every pre-existing chat: private -> personal diary, group -> class."""
    assert profiles.resolve(SimpleNamespace(profile=None, chat_type="private")) == (
        profiles.PROFILE_PERSONAL
    )
    for chat_type in ("group", "supergroup"):
        assert profiles.resolve(SimpleNamespace(profile=None, chat_type=chat_type)) == (
            profiles.PROFILE_CLASS
        )


def test_unknown_stored_profile_falls_back_instead_of_breaking():
    chat = SimpleNamespace(profile="whatever-a-newer-version-wrote", chat_type="group")
    assert profiles.normalize(chat.profile) is None
    assert profiles.resolve(chat) == profiles.PROFILE_CLASS


def test_stored_profile_wins_over_chat_type():
    chat = SimpleNamespace(profile=profiles.PROFILE_TUTOR, chat_type="private")
    assert profiles.resolve(chat) == profiles.PROFILE_TUTOR


def test_only_the_tutor_profile_drops_the_school_timetable():
    assert profiles.features(profiles.PROFILE_TUTOR).school_schedule is False
    assert profiles.needs_schedule_onboarding(profiles.PROFILE_TUTOR) is False
    for name in (profiles.PROFILE_PERSONAL, profiles.PROFILE_CLASS):
        assert profiles.features(name).school_schedule is True
        assert profiles.needs_schedule_onboarding(name) is True


def test_homework_policy_is_meaningless_in_a_personal_diary():
    assert profiles.features(profiles.PROFILE_PERSONAL).homework_policy is False
    assert profiles.features(profiles.PROFILE_CLASS).homework_policy is True


def test_homework_stays_available_in_every_profile():
    for name in profiles.PROFILES:
        assert profiles.features(name).homework is True


# --- Storage ------------------------------------------------------------------

async def test_set_chat_profile_rejects_unknown_values(db):
    await get_or_create_chat(CHAT_ID, "private")
    assert await set_chat_profile(CHAT_ID, profiles.PROFILE_TUTOR) is True
    assert (await get_chat(CHAT_ID)).profile == profiles.PROFILE_TUTOR

    assert await set_chat_profile(CHAT_ID, "anarchy") is False
    # The rejected value must not have overwritten the good one.
    assert (await get_chat(CHAT_ID)).profile == profiles.PROFILE_TUTOR


async def test_finalize_onboarding_ignores_an_unknown_profile(db):
    await get_or_create_chat(CHAT_ID, "private")
    await finalize_onboarding(
        CHAT_ID, "private", [(1, "08:00", "08:45")], {0: [(1, "Math")]},
        profile="nonsense",
    )
    chat = await get_chat(CHAT_ID)
    assert chat.profile is None
    assert chat.is_onboarded is True  # the rest of onboarding still applied


# --- Onboarding ---------------------------------------------------------------

async def test_tutor_profile_skips_the_whole_schedule_setup(db):
    """A tutor is never asked for lesson counts or bell times."""
    await get_or_create_chat(GROUP_ID, "group")
    state = _state(GROUP_ID)
    await state.set_state(OnboardingStates.waiting_for_profile)
    msg = FakeMessage(GROUP_ID, chat_type="group")
    cb = FakeCallback(msg, data=f"set:{profiles.PROFILE_TUTOR}")
    cb.data = f"ob_profile:{profiles.PROFILE_TUTOR}"

    await process_profile_choice(cb, state)

    chat = await get_chat(GROUP_ID)
    assert chat.profile == profiles.PROFILE_TUTOR
    assert chat.is_onboarded is True          # usable immediately
    assert await state.get_state() is None    # no further questions
    assert await get_lesson_slots(GROUP_ID) == []
    # The reply must point at where a tutor actually adds sessions.
    assert any("Доп. занятия" in a[0] for a in msg.answers)


async def test_class_profile_continues_to_the_lesson_questions(db):
    await get_or_create_chat(GROUP_ID, "group")
    state = _state(GROUP_ID)
    await state.set_state(OnboardingStates.waiting_for_profile)
    msg = FakeMessage(GROUP_ID, chat_type="group")
    cb = FakeCallback(msg)
    cb.data = f"ob_profile:{profiles.PROFILE_CLASS}"

    await process_profile_choice(cb, state)

    assert await state.get_state() == OnboardingStates.waiting_for_lessons_count.state
    assert (await get_chat(GROUP_ID)).is_onboarded is False  # not finished yet


async def test_tampered_profile_callback_is_refused(db):
    await get_or_create_chat(GROUP_ID, "group")
    state = _state(GROUP_ID)
    await state.set_state(OnboardingStates.waiting_for_profile)
    cb = FakeCallback(FakeMessage(GROUP_ID, chat_type="group"))
    cb.data = "ob_profile:god_mode"

    await process_profile_choice(cb, state)

    assert cb.alerts  # user was told, nothing was written
    assert (await get_chat(GROUP_ID)).profile is None
    assert await state.get_state() == OnboardingStates.waiting_for_profile.state


async def test_new_class_starts_with_admin_only_homework(db):
    """"Only the teacher enters data" must hold out of the box for a new class."""
    await get_or_create_chat(GROUP_ID, "group")
    state = _state(GROUP_ID)
    await state.update_data(profile=profiles.PROFILE_CLASS, lesson_slots=[(1, "08:00", "08:45")])

    from handlers.onboarding import _finalize_onboarding
    await _finalize_onboarding(FakeMessage(GROUP_ID, chat_type="group"), state, {0: [(1, "Math")]})

    chat = await get_chat(GROUP_ID)
    assert chat.profile == profiles.PROFILE_CLASS
    assert chat.hw_edit_policy == "admin_only"


async def test_re_onboarding_the_same_profile_keeps_tuned_settings(db):
    """Running setup again must not silently undo settings changed by hand."""
    await get_or_create_chat(GROUP_ID, "group")
    await set_chat_profile(GROUP_ID, profiles.PROFILE_CLASS)
    await set_hw_edit_policy(GROUP_ID, "collaborative")  # deliberately relaxed

    state = _state(GROUP_ID)
    await state.update_data(profile=profiles.PROFILE_CLASS, lesson_slots=[(1, "08:00", "08:45")])
    from handlers.onboarding import _finalize_onboarding
    await _finalize_onboarding(FakeMessage(GROUP_ID, chat_type="group"), state, {0: [(1, "Math")]})

    assert (await get_chat(GROUP_ID)).hw_edit_policy == "collaborative"


async def test_switching_profile_applies_the_new_profiles_defaults(db):
    await get_or_create_chat(GROUP_ID, "group")
    await set_chat_profile(GROUP_ID, profiles.PROFILE_CLASS)

    state = _state(GROUP_ID)
    await state.set_state(OnboardingStates.waiting_for_profile)
    cb = FakeCallback(FakeMessage(GROUP_ID, chat_type="group"))
    cb.data = f"ob_profile:{profiles.PROFILE_TUTOR}"
    await process_profile_choice(cb, state)

    chat = await get_chat(GROUP_ID)
    assert chat.profile == profiles.PROFILE_TUTOR
    # A tutor chat has no timetable, so "pack your bag" and schedule-change
    # heads-ups are pointless and start off.
    assert chat.schedule_reminder_enabled is False
    assert chat.changes_reminder_enabled is False


async def test_tutor_chat_with_no_lessons_still_renders_today(db):
    """The tutor shortcut leaves a chat with zero lesson slots — every screen
    that reads the timetable must cope with that, not blow up."""
    import datetime

    from handlers.today import format_today_message, get_today_data

    await get_or_create_chat(GROUP_ID, "group")
    await finalize_onboarding(
        GROUP_ID, "group", [], {}, profile=profiles.PROFILE_TUTOR,
    )

    today = datetime.date(2024, 1, 15)
    text = format_today_message(await get_today_data(GROUP_ID, today), today)
    assert text  # renders something sensible instead of raising


async def test_main_menu_follows_the_profile(db):
    """A button that leads to an empty screen is worse than no button."""
    from keyboards.reply import get_main_menu, main_menu_for

    def labels(markup):
        return {button.text for row in markup.keyboard for button in row}

    # No chat in hand: the full menu, exactly as before the menu became
    # profile-aware.
    assert "📅 Расписание" in labels(get_main_menu())

    tutor = -720_001
    await get_or_create_chat(tutor, "group")
    await finalize_onboarding(tutor, "group", [], {}, profile=profiles.PROFILE_TUTOR)
    tutor_menu = labels(await main_menu_for(tutor, "group"))
    assert "📅 Расписание" not in tutor_menu
    assert "💳 Оплата" in tutor_menu
    assert "📝 Домашнее задание" in tutor_menu

    klass = -720_002
    await get_or_create_chat(klass, "group")
    await finalize_onboarding(
        klass, "group", [(1, "08:00", "08:45")], {0: [(1, "Математика")]},
        profile=profiles.PROFILE_CLASS,
    )
    class_menu = labels(await main_menu_for(klass, "group"))
    assert "📅 Расписание" in class_menu
    assert "💳 Оплата" not in class_menu


async def test_help_only_describes_what_this_chat_has(db):
    from aiogram.fsm.context import FSMContext
    from aiogram.fsm.storage.base import StorageKey
    from aiogram.fsm.storage.memory import MemoryStorage

    from handlers.common import cmd_help

    chat_id = -720_003
    await get_or_create_chat(chat_id, "group")
    await finalize_onboarding(chat_id, "group", [], {}, profile=profiles.PROFILE_TUTOR)

    msg = FakeMessage(chat_id, chat_type="group")
    state = FSMContext(
        storage=MemoryStorage(),
        key=StorageKey(bot_id=1, chat_id=chat_id, user_id=1),
    )
    await cmd_help(msg, state)

    text = msg.answers[0][0]
    assert "💳 <b>Оплата</b>" in text
    assert "📅 <b>Расписание</b>" not in text
    # Short enough to actually read: the old help was ~4000 characters.
    assert len(text) < 1200


async def test_switching_to_tutor_keeps_the_stored_timetable(db):
    """Switching profile hides the timetable; it must never delete it."""
    await get_or_create_chat(GROUP_ID, "group")
    await finalize_onboarding(
        GROUP_ID, "group", [(1, "08:00", "08:45")], {0: [(1, "Математика")]},
        profile=profiles.PROFILE_CLASS,
    )
    assert await set_chat_profile(GROUP_ID, profiles.PROFILE_TUTOR) is True

    assert len(await get_lesson_slots(GROUP_ID)) == 1
    assert (await get_schedule(GROUP_ID, 0))[0].subject_name == "Математика"

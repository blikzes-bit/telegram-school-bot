"""
Covers: re-onboarding confirmation flow, cancel mid-flow leaves no partial
data, atomic finalize_onboarding, lesson-count decrease pruning stale
schedule rows, and exact (not substring) Yes/No matching.
"""
from types import SimpleNamespace

from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.base import StorageKey
from aiogram.fsm.storage.memory import MemoryStorage

from database.db import (
    get_or_create_chat, finalize_onboarding, save_lesson_slots,
    get_lesson_slots, get_schedule,
)
from handlers.onboarding import (
    start_onboarding_callback, reconfigure_confirm, reconfigure_cancel,
    process_lessons_count, process_lesson_times_text, cancel_onboarding,
    process_saturday_decision, OnboardingStates,
)

CHAT_ID = 700001


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
        self.bot = None
        self.from_user = SimpleNamespace(id=message.chat.id)
        self.alerts = []
        self.acks = []

    async def answer(self, *args, **kwargs):
        if kwargs.get("show_alert"):
            self.alerts.append(args[0] if args else kwargs.get("text"))
        else:
            self.acks.append(args[0] if args else None)


def _state(chat_id=CHAT_ID):
    return FSMContext(storage=MemoryStorage(), key=StorageKey(bot_id=1, chat_id=chat_id, user_id=chat_id))


async def test_reconfigure_requires_explicit_confirmation(db):
    await get_or_create_chat(CHAT_ID, "private")
    await finalize_onboarding(CHAT_ID, "private", [(1, "08:00", "08:45")], {0: [(1, "Math")]})

    state = _state()
    msg = FakeMessage(CHAT_ID)
    cb = FakeCallback(msg)
    await start_onboarding_callback(cb, state)

    # Must NOT have entered the onboarding FSM flow yet — a confirmation
    # prompt should be shown instead.
    assert await state.get_state() is None
    assert any("переконфигурировать" in a[0].lower() for a in msg.answers)

    # Existing data must be untouched until the user explicitly confirms.
    slots = await get_lesson_slots(CHAT_ID)
    assert len(slots) == 1


async def test_reconfigure_confirm_starts_flow(db):
    await get_or_create_chat(CHAT_ID, "private")
    await finalize_onboarding(CHAT_ID, "private", [(1, "08:00", "08:45")], {0: [(1, "Math")]})

    state = _state()
    msg = FakeMessage(CHAT_ID)
    cb = FakeCallback(msg)
    await reconfigure_confirm(cb, state)

    assert await state.get_state() == OnboardingStates.waiting_for_lessons_count.state


async def test_reconfigure_cancel_leaves_data_untouched(db):
    await get_or_create_chat(CHAT_ID, "private")
    await finalize_onboarding(CHAT_ID, "private", [(1, "08:00", "08:45")], {0: [(1, "Math")]})

    state = _state()
    msg = FakeMessage(CHAT_ID)
    cb = FakeCallback(msg)
    await reconfigure_cancel(cb, state)

    slots = await get_lesson_slots(CHAT_ID)
    assert len(slots) == 1


async def test_cancel_midway_leaves_no_partial_lesson_slots(db):
    await get_or_create_chat(CHAT_ID, "private")
    state = _state()

    await process_lessons_count(FakeMessage(CHAT_ID, text="3"), state)
    await process_lesson_times_text(FakeMessage(CHAT_ID, text="08:00 - 08:45"), state)
    # Only 1 of 3 lesson times entered — cancel now.
    await cancel_onboarding(FakeMessage(CHAT_ID, text="❌ Сбросить настройку"), state)

    assert await state.get_state() is None
    slots = await get_lesson_slots(CHAT_ID)
    assert slots == []  # nothing was ever written mid-flow


async def test_finalize_onboarding_is_all_or_nothing(db):
    """
    A DB-level failure partway through finalize_onboarding (here: a duplicate
    lesson_number violating the new UNIQUE constraint) must leave the chat
    completely untouched — not half-updated.
    """
    await get_or_create_chat(CHAT_ID, "private")
    await finalize_onboarding(CHAT_ID, "private", [(1, "08:00", "08:45")], {0: [(1, "Old")]})

    bad_slots = [(1, "09:00", "09:45"), (1, "10:00", "10:45")]  # duplicate lesson_number=1
    raised = False
    try:
        await finalize_onboarding(CHAT_ID, "private", bad_slots, {0: [(1, "New")]})
    except Exception:
        raised = True

    assert raised
    # Previous configuration must still be intact — nothing partially applied.
    slots = await get_lesson_slots(CHAT_ID)
    assert len(slots) == 1
    assert slots[0].start_time == "08:00"
    schedule = await get_schedule(CHAT_ID, 0)
    assert schedule[0].subject_name == "Old"


async def test_decreasing_lesson_count_prunes_stale_schedule_rows(db):
    await get_or_create_chat(CHAT_ID, "private")
    await finalize_onboarding(
        CHAT_ID, "private",
        [(1, "08:00", "08:45"), (2, "09:00", "09:45"), (3, "10:00", "10:45")],
        {0: [(1, "Math"), (2, "Physics"), (3, "Art")]},
    )
    assert len(await get_schedule(CHAT_ID, 0)) == 3

    # Re-configure with only 1 lesson.
    await save_lesson_slots(CHAT_ID, [(1, "08:00", "08:45")])

    remaining = await get_schedule(CHAT_ID, 0)
    assert len(remaining) == 1
    assert remaining[0].lesson_number == 1


async def test_yes_no_uses_exact_match_not_substring(db):
    """
    Words that merely *contain* "да"/"нет" as a substring ("никогда",
    "интернет") must not be misread as Yes/No answers.
    """
    await get_or_create_chat(CHAT_ID, "private")
    state = _state()
    await state.set_state(OnboardingStates.waiting_for_saturday_decision)
    await state.update_data(
        lesson_slots=[(1, "08:00", "08:45")],
        all_schedule_data={0: [(1, "Math")], 4: [(1, "Math")]},
        target_days=[0, 1, 2, 3, 4],
    )

    for word in ("никогда", "интернет"):
        msg = FakeMessage(CHAT_ID, text=word)
        await process_saturday_decision(msg, state)
        assert await state.get_state() == OnboardingStates.waiting_for_saturday_decision.state
        assert any("Да или Нет" in a[0] for a in msg.answers)

    # A real exact answer still works afterwards.
    msg = FakeMessage(CHAT_ID, text="Нет")
    await process_saturday_decision(msg, state)
    assert await state.get_state() is None

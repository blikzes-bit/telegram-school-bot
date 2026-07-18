"""
Fix #3: non-text messages during FSM steps.

Verifies the per-router fallback handlers reply with a friendly hint, never
touch `message.text` (so a photo/sticker/voice cannot crash them), and keep the
current FSM step intact.
"""
from types import SimpleNamespace

import pytest
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.base import StorageKey
from aiogram.fsm.storage.memory import MemoryStorage

from handlers.homework import homework_non_text, AddHomeworkStates
from handlers.onboarding import onboarding_non_text, OnboardingStates
from handlers.schedule import schedule_non_text, EditScheduleStates
from handlers.settings import settings_non_text, SettingStates


class FakeMessage:
    """A non-text message (text is None, like a photo/sticker/voice)."""

    def __init__(self, chat_id=1):
        self.text = None
        self.chat = SimpleNamespace(id=chat_id, type="private")
        self.answers = []

    async def answer(self, text, **kwargs):
        self.answers.append((text, kwargs))


def _make_state():
    storage = MemoryStorage()
    key = StorageKey(bot_id=1, chat_id=1, user_id=1)
    return FSMContext(storage=storage, key=key)


@pytest.mark.parametrize(
    "handler, state_obj",
    [
        (homework_non_text, AddHomeworkStates.waiting_for_subject),
        (homework_non_text, AddHomeworkStates.waiting_for_description),
        (homework_non_text, AddHomeworkStates.waiting_for_due_date),
        (onboarding_non_text, OnboardingStates.waiting_for_lessons_count),
        (onboarding_non_text, OnboardingStates.waiting_for_lesson_times),
        (onboarding_non_text, OnboardingStates.waiting_for_schedule_subjects),
        (onboarding_non_text, OnboardingStates.waiting_for_saturday_decision),
        (schedule_non_text, EditScheduleStates.waiting_for_subject_name),
        (schedule_non_text, EditScheduleStates.waiting_for_lesson_times),
        (settings_non_text, SettingStates.waiting_for_hw_time),
    ],
)
async def test_non_text_fallback_keeps_state_and_replies(handler, state_obj):
    state = _make_state()
    await state.set_state(state_obj)

    msg = FakeMessage()
    # Must not raise even though msg.text is None.
    await handler(msg)

    # Replied with a hint.
    assert msg.answers, "fallback should reply to the user"
    assert "текст" in msg.answers[0][0].lower()

    # FSM step preserved — the user stays where they were.
    assert await state.get_state() == state_obj.state

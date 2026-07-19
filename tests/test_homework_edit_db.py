"""DB-level tests for editing an existing homework entry (written before the
implementation, per task requirements)."""
import datetime

from database.db import (
    get_or_create_chat, add_homework, get_homework_by_id, update_homework,
)

CHAT_ID = 424242
OTHER_CHAT_ID = 424243


async def test_update_subject(db):
    await get_or_create_chat(CHAT_ID, "private")
    hw = await add_homework(CHAT_ID, "Math", datetime.date(2026, 1, 10), "p.1")

    ok = await update_homework(CHAT_ID, hw.id, subject_name="Algebra")
    assert ok is True

    updated = await get_homework_by_id(CHAT_ID, hw.id)
    assert updated.subject_name == "Algebra"
    assert updated.description == "p.1"
    assert updated.due_date == datetime.date(2026, 1, 10)


async def test_update_description(db):
    await get_or_create_chat(CHAT_ID, "private")
    hw = await add_homework(CHAT_ID, "Math", datetime.date(2026, 1, 10), "p.1")

    ok = await update_homework(CHAT_ID, hw.id, description="p.42, exercises 1-5")
    assert ok is True

    updated = await get_homework_by_id(CHAT_ID, hw.id)
    assert updated.description == "p.42, exercises 1-5"
    assert updated.subject_name == "Math"


async def test_update_due_date(db):
    await get_or_create_chat(CHAT_ID, "private")
    hw = await add_homework(CHAT_ID, "Math", datetime.date(2026, 1, 10), "p.1")

    new_date = datetime.date(2026, 2, 20)
    ok = await update_homework(CHAT_ID, hw.id, due_date=new_date)
    assert ok is True

    updated = await get_homework_by_id(CHAT_ID, hw.id)
    assert updated.due_date == new_date


async def test_update_strips_whitespace(db):
    await get_or_create_chat(CHAT_ID, "private")
    hw = await add_homework(CHAT_ID, "Math", datetime.date(2026, 1, 10), "p.1")

    await update_homework(CHAT_ID, hw.id, subject_name="  Physics  ", description="  read ch.3  ")

    updated = await get_homework_by_id(CHAT_ID, hw.id)
    assert updated.subject_name == "Physics"
    assert updated.description == "read ch.3"


async def test_update_missing_homework_returns_false(db):
    await get_or_create_chat(CHAT_ID, "private")
    ok = await update_homework(CHAT_ID, 999999, subject_name="Ghost")
    assert ok is False


async def test_update_scoped_to_chat_id(db):
    """A chat must never be able to edit another chat's homework."""
    await get_or_create_chat(CHAT_ID, "private")
    await get_or_create_chat(OTHER_CHAT_ID, "private")
    hw = await add_homework(CHAT_ID, "Math", datetime.date(2026, 1, 10), "p.1")

    ok = await update_homework(OTHER_CHAT_ID, hw.id, subject_name="Hijacked")
    assert ok is False

    unchanged = await get_homework_by_id(CHAT_ID, hw.id)
    assert unchanged.subject_name == "Math"


async def test_get_homework_by_id_scoped_to_chat_id(db):
    await get_or_create_chat(CHAT_ID, "private")
    await get_or_create_chat(OTHER_CHAT_ID, "private")
    hw = await add_homework(CHAT_ID, "Math", datetime.date(2026, 1, 10), "p.1")

    assert await get_homework_by_id(CHAT_ID, hw.id) is not None
    assert await get_homework_by_id(OTHER_CHAT_ID, hw.id) is None


async def test_get_homework_by_id_missing(db):
    await get_or_create_chat(CHAT_ID, "private")
    assert await get_homework_by_id(CHAT_ID, 999999) is None

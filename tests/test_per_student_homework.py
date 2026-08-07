"""
Per-student homework marks.

The design decision this pins down: personal marks are a **layer on top of**
``homework.is_completed``, never a replacement.

  * ``homework.is_completed`` keeps its meaning — the class-level "this task is
    closed" flag — and stays the only thing the bot's chat-wide screens and the
    reminders look at. A group message is seen by everybody, so it cannot be
    personal, and one student ticking a box must not silence the reminder for
    the other twenty-nine.
  * a personal mark changes only what *that* person sees in the Mini App, plus
    the "how many are done" count the teacher sees.

A chat that never opts in has no rows in the new table and behaves exactly as
before — that is what most of these tests are really checking.
"""
import datetime

import database.db as dbm
from tests.conftest import FakeBot
from tests.web_helpers import authenticate, build_test_settings, now_iso, web_client

MON = datetime.date(2024, 1, 15)


async def _class_chat(chat_id: int, per_student: bool = False):
    await dbm.get_or_create_chat(chat_id, "group")
    await dbm.finalize_onboarding(
        chat_id, "group", [(1, "08:00", "08:45")], {0: [(1, "Математика")]},
    )
    if per_student:
        await dbm.set_per_student_homework(chat_id, True)


async def _member(chat_id: int, user_id: int, role: str = "member"):
    await dbm.upsert_membership(chat_id, user_id, role, now_iso())


# --- Storage ------------------------------------------------------------------

async def test_marking_is_idempotent_in_both_directions(db):
    chat_id = -9101
    await _class_chat(chat_id, per_student=True)
    hw = await dbm.add_homework(chat_id, "Физика", MON, "опыт")

    for _ in range(2):
        assert await dbm.set_homework_done_by(chat_id, hw.id, 11, True, now_iso()) is True
    assert (await dbm.get_homework_completions(chat_id))[hw.id] == [11]

    for _ in range(2):
        assert await dbm.set_homework_done_by(chat_id, hw.id, 11, False, now_iso()) is True
    assert await dbm.get_homework_completions(chat_id) == {}


async def test_marks_are_scoped_to_their_chat(db):
    mine, theirs = -9102, -9103
    await _class_chat(mine, per_student=True)
    await _class_chat(theirs, per_student=True)
    foreign = await dbm.add_homework(theirs, "Чужое", MON, "не трогать")

    assert await dbm.set_homework_done_by(mine, foreign.id, 11, True, now_iso()) is False
    assert await dbm.get_homework_completions(theirs) == {}


async def test_deleting_homework_takes_its_marks_with_it(db):
    chat_id = -9104
    await _class_chat(chat_id, per_student=True)
    hw = await dbm.add_homework(chat_id, "Химия", MON, "опыт")
    await dbm.set_homework_done_by(chat_id, hw.id, 11, True, now_iso())

    assert await dbm.delete_homework(chat_id, hw.id) is True
    assert await dbm.get_homework_completions(chat_id) == {}


async def test_one_query_answers_for_a_whole_list(db):
    chat_id = -9105
    await _class_chat(chat_id, per_student=True)
    first = await dbm.add_homework(chat_id, "Алгебра", MON, "a")
    second = await dbm.add_homework(chat_id, "Геометрия", MON, "b")
    await dbm.set_homework_done_by(chat_id, first.id, 11, True, now_iso())
    await dbm.set_homework_done_by(chat_id, first.id, 12, True, now_iso())
    await dbm.set_homework_done_by(chat_id, second.id, 12, True, now_iso())

    everyone = await dbm.get_homework_completions(chat_id)
    assert sorted(everyone[first.id]) == [11, 12]
    assert everyone[second.id] == [12]

    just_me = await dbm.get_homework_completions(chat_id, user_id=12)
    assert sorted(just_me) == sorted([first.id, second.id])
    assert just_me[first.id] == [12]


# --- Two people, two answers --------------------------------------------------

async def test_each_student_sees_their_own_mark(db):
    settings = build_test_settings()
    chat_id = -9106
    await _class_chat(chat_id, per_student=True)
    await _member(chat_id, 9201)
    await _member(chat_id, 9202)
    hw = await dbm.add_homework(chat_id, "Физика", MON, "опыт")

    async with web_client(settings) as (client, _s):
        await authenticate(client, 9201)
        resp = await client.patch(
            f"/api/v1/classes/{chat_id}/homework/{hw.id}/complete",
            json={"is_completed": True},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["is_completed"] is True
        assert body["per_student"] is True
        assert body["completed_count"] == 1

    async with web_client(settings) as (client, _s):
        await authenticate(client, 9202)
        listed = (await client.get(f"/api/v1/classes/{chat_id}/homework")).json()
        # The same entry, a different answer — and the count tells the teacher
        # how many are done without exposing who.
        assert listed[0]["is_completed"] is False
        assert listed[0]["completed_count"] == 1
        # Its due date is long past, so for this student it is simply overdue.
        assert listed[0]["status"] == "overdue"

    # The class-level flag was not touched by anybody's personal mark.
    assert (await dbm.get_homework_by_id(chat_id, hw.id)).is_completed is False


async def test_status_filters_follow_the_personal_answer(db):
    settings = build_test_settings()
    chat_id = -9107
    await _class_chat(chat_id, per_student=True)
    await _member(chat_id, 9301)
    await _member(chat_id, 9302)
    hw = await dbm.add_homework(chat_id, "Химия", MON, "опыт")

    async with web_client(settings) as (client, _s):
        await authenticate(client, 9301)
        await client.patch(
            f"/api/v1/classes/{chat_id}/homework/{hw.id}/complete",
            json={"is_completed": True},
        )
        done = await client.get(f"/api/v1/classes/{chat_id}/homework?status=completed")
        assert [item["id"] for item in done.json()] == [hw.id]
        assert (
            await client.get(f"/api/v1/classes/{chat_id}/homework?status=overdue")
        ).json() == []

    async with web_client(settings) as (client, _s):
        await authenticate(client, 9302)
        # For the other student it is still overdue (the due date has passed).
        assert (
            await client.get(f"/api/v1/classes/{chat_id}/homework?status=completed")
        ).json() == []
        overdue = await client.get(f"/api/v1/classes/{chat_id}/homework?status=overdue")
        assert [item["id"] for item in overdue.json()] == [hw.id]


async def test_a_viewer_may_still_tick_their_own_box(db):
    """A personal mark says something about you, not about the class's data."""
    from services import permissions as perms

    settings = build_test_settings()
    chat_id = -9108
    await _class_chat(chat_id, per_student=True)
    await dbm.set_chat_owner(chat_id, 9401)
    await dbm.set_access_mode(chat_id, perms.ACCESS_ROLES)
    await _member(chat_id, 9402)  # no app role at all -> viewer

    hw = await dbm.add_homework(chat_id, "Физика", MON, "опыт")

    async with web_client(settings) as (client, _s):
        await authenticate(client, 9402)
        resp = await client.patch(
            f"/api/v1/classes/{chat_id}/homework/{hw.id}/complete",
            json={"is_completed": True},
        )
        assert resp.status_code == 200
        assert resp.json()["is_completed"] is True
        # …and still cannot touch the entry itself.
        assert (
            await client.patch(
                f"/api/v1/classes/{chat_id}/homework/{hw.id}", json={"description": "x"}
            )
        ).status_code == 403


async def test_admin_only_policy_no_longer_blocks_a_personal_mark(db):
    settings = build_test_settings()
    chat_id = -9109
    await _class_chat(chat_id, per_student=True)
    await dbm.set_hw_edit_policy(chat_id, "admin_only")
    await _member(chat_id, 9501, role="member")
    hw = await dbm.add_homework(chat_id, "История", MON, "параграф")

    async with web_client(settings) as (client, _s):
        await authenticate(client, 9501)
        assert (
            await client.patch(
                f"/api/v1/classes/{chat_id}/homework/{hw.id}/complete",
                json={"is_completed": True},
            )
        ).status_code == 200

    assert (await dbm.get_homework_by_id(chat_id, hw.id)).is_completed is False


# --- Nothing changes when the chat has not opted in ---------------------------

async def test_shared_marks_remain_the_default(db):
    settings = build_test_settings()
    chat_id = -9110
    await _class_chat(chat_id)  # not opted in
    await _member(chat_id, 9601)
    await _member(chat_id, 9602)
    hw = await dbm.add_homework(chat_id, "Физика", MON, "опыт")

    async with web_client(settings) as (client, _s):
        await authenticate(client, 9601)
        body = (await client.patch(
            f"/api/v1/classes/{chat_id}/homework/{hw.id}/complete",
            json={"is_completed": True},
        )).json()
        assert body["per_student"] is False
        # None, not 0: the client must not read "nobody is done" into a chat
        # that does not count people at all.
        assert body["completed_count"] is None

    async with web_client(settings) as (client, _s):
        await authenticate(client, 9602)
        listed = (await client.get(f"/api/v1/classes/{chat_id}/homework")).json()
        assert listed[0]["is_completed"] is True  # one shared mark

    assert (await dbm.get_homework_by_id(chat_id, hw.id)).is_completed is True
    assert await dbm.get_homework_completions(chat_id) == {}


async def test_the_switch_is_off_for_every_existing_chat(db):
    chat_id = -9111
    await _class_chat(chat_id)
    chat = await dbm.get_chat(chat_id)
    assert chat.per_student_homework is None

    from application.queries import per_student_marks
    assert per_student_marks(chat) is False


async def test_switching_it_on_over_http(db):
    settings = build_test_settings()
    chat_id = -9112
    await _class_chat(chat_id)
    await _member(chat_id, 9701, role="admin")

    async with web_client(settings) as (client, _s):
        await authenticate(client, 9701)
        resp = await client.patch(
            f"/api/v1/classes/{chat_id}/settings/class",
            json={"per_student_homework": True},
        )
        assert resp.status_code == 200
        assert resp.json()["per_student_homework"] is True

    assert (await dbm.get_chat(chat_id)).per_student_homework is True
    entries = await dbm.get_audit_logs(chat_id, "settings")
    assert any("личные отметки" in (e.summary or "") for e in entries)


async def test_a_member_cannot_switch_it(db):
    settings = build_test_settings()
    chat_id = -9113
    await _class_chat(chat_id)
    await _member(chat_id, 9801, role="member")

    async with web_client(settings) as (client, _s):
        await authenticate(client, 9801)
        assert (
            await client.patch(
                f"/api/v1/classes/{chat_id}/settings/class",
                json={"per_student_homework": True},
            )
        ).status_code == 403

    assert (await dbm.get_chat(chat_id)).per_student_homework is None


# --- Chat-wide messages stay chat-wide ---------------------------------------

async def test_a_personal_mark_does_not_silence_the_chat_reminder(db):
    """The load-bearing rule: 1 of 30 students ticking a box changes nothing for
    the group message the other 29 are waiting for."""
    from services.scheduler import send_hw_reminder

    chat_id = -9114
    await _class_chat(chat_id, per_student=True)
    tomorrow = MON + datetime.timedelta(days=1)
    hw = await dbm.add_homework(chat_id, "Физика", tomorrow, "опыт")
    await dbm.set_homework_done_by(chat_id, hw.id, 11, True, now_iso())

    bot = FakeBot()
    tz = __import__("pytz").timezone("Europe/Kyiv")
    assert await send_hw_reminder(bot, chat_id, tz, today=MON) is True

    text = "\n".join(sent[1] for sent in bot.sent)
    assert "Физика" in text


async def test_the_bots_today_screen_still_shows_class_level_state(db):
    from handlers.today import format_today_message, get_today_data

    chat_id = -9115
    await _class_chat(chat_id, per_student=True)
    hw = await dbm.add_homework(chat_id, "Химия", MON, "опыт")
    await dbm.set_homework_done_by(chat_id, hw.id, 11, True, now_iso())

    text = format_today_message(await get_today_data(chat_id, MON), MON)
    # Still listed: the entry is not closed for the class, and a group message
    # cannot be personal.
    assert "Химия" in text

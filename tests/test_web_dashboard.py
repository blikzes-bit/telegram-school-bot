"""Web API — dashboard / read-only data tests.

Verifies the "Today" DTO matches the bot's effective-schedule logic (A/B weeks +
date overrides + timezone), permissions reflect member/admin/private, and data is
strictly isolated per chat_id.
"""
import datetime

import database.db as dbm
from tests.web_helpers import authenticate, build_test_settings, now_iso, web_client

MON = datetime.date(2024, 1, 15)  # a Monday -> weekday() == 0


async def _onboard(chat_id: int, chat_type: str = "group"):
    await dbm.get_or_create_chat(chat_id, chat_type)
    await dbm.finalize_onboarding(
        chat_id,
        chat_type,
        [(1, "08:00", "08:45"), (2, "09:00", "09:45")],
        {0: [(1, "Математика"), (2, "Физика")]},
    )


async def _member(chat_id: int, user_id: int, role: str = "member"):
    await dbm.upsert_membership(chat_id, user_id, role, now_iso())


async def test_today_dto_is_correct(db):
    settings = build_test_settings()
    chat_id = -700
    await _onboard(chat_id)
    await _member(chat_id, 2001, role="admin")
    await dbm.add_homework(chat_id, "Математика", MON, "стр. 5")
    await dbm.add_homework(chat_id, "История", datetime.date(2024, 1, 10), "доклад")
    await dbm.add_homework(chat_id, "Химия", datetime.date(2024, 1, 20), "опыт")
    await dbm.add_extra_activity(
        chat_id, "Английский", "weekly", "18:00", day_of_week=0
    )

    async with web_client(settings) as (client, _s):
        await authenticate(client, 2001)
        resp = await client.get(f"/api/v1/classes/{chat_id}/today?date=2024-01-15")
        assert resp.status_code == 200
        body = resp.json()

        assert body["date"] == "2024-01-15"
        assert body["weekday"] == 0
        assert body["timezone"]  # class timezone is populated
        subjects = [lesson["subject_name"] for lesson in body["lessons"]]
        assert subjects == ["Математика", "Физика"]

        assert [h["subject_name"] for h in body["homework_today"]] == ["Математика"]
        assert [h["subject_name"] for h in body["overdue"]] == ["История"]
        assert [h["subject_name"] for h in body["upcoming"]] == ["Химия"]
        assert body["overdue"][0]["status"] == "overdue"

        assert [e["title"] for e in body["extra"]] == ["Английский"]
        assert body["permissions"]["is_admin"] is True
        assert body["permissions"]["can_edit_schedule"] is True


async def test_member_is_not_admin(db):
    settings = build_test_settings()
    chat_id = -701
    await _onboard(chat_id)
    await _member(chat_id, 2002, role="member")

    async with web_client(settings) as (client, _s):
        await authenticate(client, 2002)
        resp = await client.get(f"/api/v1/classes/{chat_id}/today?date=2024-01-15")
        assert resp.status_code == 200
        perms = resp.json()["permissions"]
        assert perms["is_admin"] is False
        assert perms["can_edit_schedule"] is False


async def test_private_chat_user_is_admin(db):
    settings = build_test_settings()
    chat_id = 2003  # positive id -> private chat convention
    await _onboard(chat_id, chat_type="private")
    await _member(chat_id, 2003, role="member")

    async with web_client(settings) as (client, _s):
        await authenticate(client, 2003)
        resp = await client.get(f"/api/v1/classes/{chat_id}/today?date=2024-01-15")
        assert resp.status_code == 200
        assert resp.json()["permissions"]["is_admin"] is True


async def test_two_classes_are_isolated(db):
    settings = build_test_settings()
    await _onboard(-800)
    await _onboard(-801)
    await dbm.add_homework(-800, "Биология", MON, "класс 800")
    await dbm.add_homework(-801, "География", MON, "класс 801")
    await _member(-800, 3001, role="member")
    await _member(-801, 3001, role="member")

    async with web_client(settings) as (client, _s):
        await authenticate(client, 3001)

        a = await client.get("/api/v1/classes/-800/today?date=2024-01-15")
        b = await client.get("/api/v1/classes/-801/today?date=2024-01-15")
        assert a.status_code == 200 and b.status_code == 200
        assert [h["subject_name"] for h in a.json()["homework_today"]] == ["Биология"]
        assert [h["subject_name"] for h in b.json()["homework_today"]] == ["География"]


async def test_classes_list_returns_only_memberships(db):
    settings = build_test_settings()
    await _onboard(-810)
    await _onboard(-811)
    await _member(-810, 3002, role="admin")

    async with web_client(settings) as (client, _s):
        await authenticate(client, 3002)
        resp = await client.get("/api/v1/classes")
        assert resp.status_code == 200
        rows = resp.json()
        assert [r["chat_id"] for r in rows] == [-810]
        assert rows[0]["role"] == "admin"


async def test_homework_status_filter(db):
    settings = build_test_settings()
    chat_id = -820
    await _onboard(chat_id)
    await _member(chat_id, 3003)
    today = datetime.date.today()
    future = today + datetime.timedelta(days=10)
    past = today - datetime.timedelta(days=10)
    await dbm.add_homework(chat_id, "Активное", future, "a")
    await dbm.add_homework(chat_id, "Просроченное", past, "b")
    done = await dbm.add_homework(chat_id, "Готово", today, "c")
    await dbm.mark_homework_completed(chat_id, done.id, True)

    async with web_client(settings) as (client, _s):
        await authenticate(client, 3003)
        base = f"/api/v1/classes/{chat_id}/homework"

        active = await client.get(f"{base}?status=active")
        overdue = await client.get(f"{base}?status=overdue")
        completed = await client.get(f"{base}?status=completed")
        assert {h["subject_name"] for h in active.json()} == {"Активное"}
        assert {h["subject_name"] for h in overdue.json()} == {"Просроченное"}
        assert {h["subject_name"] for h in completed.json()} == {"Готово"}


async def test_schedule_and_extra_ranges(db):
    settings = build_test_settings()
    chat_id = -830
    await _onboard(chat_id)
    await _member(chat_id, 3004)
    await dbm.add_extra_activity(chat_id, "Шахматы", "weekly", "17:00", day_of_week=0)

    async with web_client(settings) as (client, _s):
        await authenticate(client, 3004)
        sched = await client.get(
            f"/api/v1/classes/{chat_id}/schedule?from=2024-01-15&to=2024-01-16"
        )
        assert sched.status_code == 200
        days = sched.json()["days"]
        assert [d["date"] for d in days] == ["2024-01-15", "2024-01-16"]
        # Monday has the configured lessons; Tuesday has none.
        assert len(days[0]["lessons"]) == 2

        extra = await client.get(
            f"/api/v1/classes/{chat_id}/extra?from=2024-01-15&to=2024-01-21"
        )
        assert extra.status_code == 200
        assert [e["title"] for e in extra.json()] == ["Шахматы"]

"""Web API — editing the weekly template and per-date changes.

The property that matters most: **the template and a per-date change are
different things**. Cancelling Monday's first lesson on one date must not touch
the template, and the effective schedule must show the change on that date and
only that date. That is the same rule the bot enforces, exercised here through
HTTP.
"""
import datetime

import database.db as dbm
from services import permissions as perms
from tests.web_helpers import authenticate, build_test_settings, now_iso, web_client

# 2024-01-15 is a Monday, so weekday 0 lines up with the template's day 0.
MON = datetime.date(2024, 1, 15)


async def _onboard(chat_id: int):
    await dbm.get_or_create_chat(chat_id, "group")
    await dbm.finalize_onboarding(
        chat_id, "group",
        [(1, "08:00", "08:45"), (2, "09:00", "09:45")],
        {0: [(1, "Математика"), (2, "Физика")]},
    )


async def _member(chat_id: int, user_id: int, role: str = "member", app_role=None):
    await dbm.upsert_membership(chat_id, user_id, role, now_iso())
    if app_role is not None:
        await dbm.set_member_app_role(chat_id, user_id, app_role)


# --- The weekly template ------------------------------------------------------

async def test_template_shows_slots_and_subjects(db):
    settings = build_test_settings()
    chat_id = -8001
    await _onboard(chat_id)
    await _member(chat_id, 8101, role="member")

    async with web_client(settings) as (client, _s):
        await authenticate(client, 8101)
        body = (await client.get(f"/api/v1/classes/{chat_id}/schedule/template")).json()

    assert body["week_type"] == "all"
    assert body["can_edit"] is False  # a plain member reads but cannot edit
    assert [s["start_time"] for s in body["slots"]] == ["08:00", "09:00"]
    monday = next(d for d in body["days"] if d["weekday"] == 0)
    assert [lesson["subject_name"] for lesson in monday["lessons"]] == [
        "Математика", "Физика",
    ]
    tuesday = next(d for d in body["days"] if d["weekday"] == 1)
    assert [lesson["subject_name"] for lesson in tuesday["lessons"]] == [None, None]


async def test_admin_replaces_a_days_subjects(db):
    settings = build_test_settings()
    chat_id = -8002
    await _onboard(chat_id)
    await _member(chat_id, 8201, role="admin")

    async with web_client(settings) as (client, _s):
        await authenticate(client, 8201)
        resp = await client.put(
            f"/api/v1/classes/{chat_id}/schedule/template/0",
            json={"lessons": [
                {"lesson_number": 1, "subject_name": "Химия"},
                {"lesson_number": 2, "subject_name": ""},
            ]},
        )
        assert resp.status_code == 200
        monday = next(d for d in resp.json()["days"] if d["weekday"] == 0)
        # A blank subject means "no lesson in this slot", not an empty string row.
        assert [lesson["subject_name"] for lesson in monday["lessons"]] == ["Химия", None]

    rows = await dbm.get_schedule(chat_id, 0)
    assert [(r.lesson_number, r.subject_name) for r in rows] == [(1, "Химия")]


async def test_replacing_bell_times_prunes_lessons_that_no_longer_exist(db):
    settings = build_test_settings()
    chat_id = -8003
    await _onboard(chat_id)
    await _member(chat_id, 8301, role="admin")

    async with web_client(settings) as (client, _s):
        await authenticate(client, 8301)
        resp = await client.put(
            f"/api/v1/classes/{chat_id}/schedule/slots",
            json={"slots": [{"lesson_number": 1, "start_time": "08:30", "end_time": "09:15"}]},
        )
        assert resp.status_code == 200
        assert len(resp.json()["slots"]) == 1

    # Physics was lesson 2, which no longer exists — it must not linger.
    rows = await dbm.get_schedule(chat_id, 0)
    assert [r.subject_name for r in rows] == ["Математика"]


async def test_incoherent_bell_times_are_rejected(db):
    settings = build_test_settings()
    chat_id = -8004
    await _onboard(chat_id)
    await _member(chat_id, 8401, role="admin")

    bad_payloads = [
        {"slots": []},  # nothing at all
        {"slots": [{"lesson_number": 2, "start_time": "08:00", "end_time": "08:45"}]},  # gap
        {"slots": [{"lesson_number": 1, "start_time": "09:00", "end_time": "08:00"}]},  # inverted
        {"slots": [  # second lesson starts before the first ends
            {"lesson_number": 1, "start_time": "08:00", "end_time": "09:00"},
            {"lesson_number": 2, "start_time": "08:30", "end_time": "09:15"},
        ]},
        {"slots": [{"lesson_number": 1, "start_time": "25:00", "end_time": "26:00"}]},
    ]
    async with web_client(settings) as (client, _s):
        await authenticate(client, 8401)
        for payload in bad_payloads:
            resp = await client.put(
                f"/api/v1/classes/{chat_id}/schedule/slots", json=payload
            )
            assert resp.status_code == 422, payload

    # The stored timetable is untouched by any of the rejected attempts.
    assert len(await dbm.get_lesson_slots(chat_id)) == 2


async def test_member_cannot_edit_the_template(db):
    settings = build_test_settings()
    chat_id = -8005
    await _onboard(chat_id)
    await _member(chat_id, 8501, role="member")

    async with web_client(settings) as (client, _s):
        await authenticate(client, 8501)
        assert (
            await client.put(
                f"/api/v1/classes/{chat_id}/schedule/template/0",
                json={"lessons": [{"lesson_number": 1, "subject_name": "Х"}]},
            )
        ).status_code == 403
        assert (
            await client.put(
                f"/api/v1/classes/{chat_id}/schedule/slots",
                json={"slots": [{"lesson_number": 1, "start_time": "08:00", "end_time": "08:45"}]},
            )
        ).status_code == 403

    assert (await dbm.get_schedule(chat_id, 0))[0].subject_name == "Математика"


async def test_an_editor_is_not_a_schedule_editor(db):
    """Roles are deliberately narrow: content yes, timetable no."""
    settings = build_test_settings()
    chat_id = -8006
    await _onboard(chat_id)
    await dbm.set_chat_owner(chat_id, 8601)
    await dbm.set_access_mode(chat_id, perms.ACCESS_ROLES)
    await _member(chat_id, 8602, role="member", app_role=perms.ROLE_EDITOR)

    async with web_client(settings) as (client, _s):
        await authenticate(client, 8602)
        assert (
            await client.put(
                f"/api/v1/classes/{chat_id}/schedule/template/0",
                json={"lessons": [{"lesson_number": 1, "subject_name": "Х"}]},
            )
        ).status_code == 403


async def test_bad_weekday_is_rejected(db):
    settings = build_test_settings()
    chat_id = -8007
    await _onboard(chat_id)
    await _member(chat_id, 8701, role="admin")

    async with web_client(settings) as (client, _s):
        await authenticate(client, 8701)
        assert (
            await client.put(
                f"/api/v1/classes/{chat_id}/schedule/template/9", json={"lessons": []}
            )
        ).status_code == 400


async def test_ab_week_request_is_ignored_while_alternation_is_off(db):
    """Editing a template nobody can see would be a silent no-op — refuse to."""
    settings = build_test_settings()
    chat_id = -8008
    await _onboard(chat_id)
    await _member(chat_id, 8801, role="admin")

    async with web_client(settings) as (client, _s):
        await authenticate(client, 8801)
        body = (await client.get(
            f"/api/v1/classes/{chat_id}/schedule/template?week_type=A"
        )).json()
        assert body["week_type"] == "all"
        assert body["week_mode"] is False

        await client.put(
            f"/api/v1/classes/{chat_id}/schedule/template/0?week_type=A",
            json={"lessons": [{"lesson_number": 1, "subject_name": "Биология"}]},
        )

    # Written to the single visible template, not to a hidden A one.
    assert [r.week_type for r in await dbm.get_all_schedule(chat_id)] == ["all"]


async def test_ab_weeks_are_edited_separately_when_enabled(db):
    settings = build_test_settings()
    chat_id = -8009
    await _onboard(chat_id)
    await _member(chat_id, 8901, role="admin")
    await dbm.set_week_mode(chat_id, True, anchor_monday=MON)

    async with web_client(settings) as (client, _s):
        await authenticate(client, 8901)
        for week, subject in (("A", "Алгебра"), ("B", "Геометрия")):
            resp = await client.put(
                f"/api/v1/classes/{chat_id}/schedule/template/0?week_type={week}",
                json={"lessons": [{"lesson_number": 1, "subject_name": subject}]},
            )
            assert resp.status_code == 200
            assert resp.json()["week_type"] == week

    stored = {
        (r.week_type, r.subject_name)
        for r in await dbm.get_all_schedule(chat_id)
        if r.week_type in ("A", "B")
    }
    assert stored == {("A", "Алгебра"), ("B", "Геометрия")}


# --- Per-date changes ---------------------------------------------------------

async def test_a_free_day_affects_only_that_date(db):
    settings = build_test_settings()
    chat_id = -8010
    await _onboard(chat_id)
    await _member(chat_id, 9001, role="admin")

    async with web_client(settings) as (client, _s):
        await authenticate(client, 9001)
        resp = await client.put(
            f"/api/v1/classes/{chat_id}/overrides/2024-01-15",
            json={"day_type": "holiday", "note": "День города"},
        )
        assert resp.status_code == 200
        assert resp.json()["day"]["day_type"] == "holiday"
        assert resp.json()["day"]["day_type_label"]

        # The effective schedule: no lessons on the 15th, normal on the 22nd.
        window = (await client.get(
            f"/api/v1/classes/{chat_id}/schedule?from=2024-01-15&to=2024-01-22"
        )).json()
        by_date = {d["date"]: d for d in window["days"]}
        assert by_date["2024-01-15"]["lessons"] == []
        assert by_date["2024-01-15"]["day_type"] == "holiday"
        assert len(by_date["2024-01-22"]["lessons"]) == 2

    # The template itself is untouched.
    assert len(await dbm.get_schedule(chat_id, 0)) == 2


async def test_cancelling_one_lesson_on_one_date(db):
    settings = build_test_settings()
    chat_id = -8011
    await _onboard(chat_id)
    await _member(chat_id, 9101, role="admin")

    async with web_client(settings) as (client, _s):
        await authenticate(client, 9101)
        resp = await client.put(
            f"/api/v1/classes/{chat_id}/overrides/2024-01-15/lessons/1",
            json={"action": "cancel"},
        )
        assert resp.status_code == 200
        assert resp.json()["lessons"][0]["action"] == "cancel"

        day = (await client.get(
            f"/api/v1/classes/{chat_id}/schedule?from=2024-01-15&to=2024-01-15"
        )).json()["days"][0]
        first = next(le for le in day["lessons"] if le["lesson_number"] == 1)
        assert first["cancelled"] is True


async def test_replacing_a_subject_and_its_time_on_one_date(db):
    settings = build_test_settings()
    chat_id = -8012
    await _onboard(chat_id)
    await _member(chat_id, 9201, role="admin")

    async with web_client(settings) as (client, _s):
        await authenticate(client, 9201)
        resp = await client.put(
            f"/api/v1/classes/{chat_id}/overrides/2024-01-15/lessons/2",
            json={
                "action": "set",
                "subject_name": "Астрономия",
                "start_time": "10:00",
                "end_time": "10:45",
            },
        )
        assert resp.status_code == 200

        day = (await client.get(
            f"/api/v1/classes/{chat_id}/schedule?from=2024-01-15&to=2024-01-15"
        )).json()["days"][0]
        second = next(le for le in day["lessons"] if le["lesson_number"] == 2)
        assert second["subject_name"] == "Астрономия"
        assert second["start_time"] == "10:00"
        assert second["subject_changed"] is True
        assert second["time_changed"] is True


async def test_an_empty_set_change_is_refused(db):
    """A 'set' with nothing to set would look like it worked and do nothing."""
    settings = build_test_settings()
    chat_id = -8013
    await _onboard(chat_id)
    await _member(chat_id, 9301, role="admin")

    async with web_client(settings) as (client, _s):
        await authenticate(client, 9301)
        assert (
            await client.put(
                f"/api/v1/classes/{chat_id}/overrides/2024-01-15/lessons/1",
                json={"action": "set"},
            )
        ).status_code == 422
        assert (
            await client.put(
                f"/api/v1/classes/{chat_id}/overrides/2024-01-15/lessons/1",
                json={"action": "teleport"},
            )
        ).status_code == 422


async def test_clearing_changes_returns_the_date_to_normal(db):
    settings = build_test_settings()
    chat_id = -8014
    await _onboard(chat_id)
    await _member(chat_id, 9401, role="admin")

    async with web_client(settings) as (client, _s):
        await authenticate(client, 9401)
        await client.put(
            f"/api/v1/classes/{chat_id}/overrides/2024-01-15",
            json={"day_type": "remote"},
        )
        await client.put(
            f"/api/v1/classes/{chat_id}/overrides/2024-01-15/lessons/1",
            json={"action": "cancel"},
        )

        # Removing one lesson change leaves the day setting alone…
        after_one = (await client.delete(
            f"/api/v1/classes/{chat_id}/overrides/2024-01-15/lessons/1"
        )).json()
        assert after_one["lessons"] == []
        assert after_one["day"]["day_type"] == "remote"

        # …and clearing everything wipes the date clean.
        cleared = (await client.delete(
            f"/api/v1/classes/{chat_id}/overrides/2024-01-15"
        )).json()
        assert cleared["day"] is None and cleared["lessons"] == []


async def test_a_bad_date_is_rejected(db):
    settings = build_test_settings()
    chat_id = -8015
    await _onboard(chat_id)
    await _member(chat_id, 9501, role="admin")

    async with web_client(settings) as (client, _s):
        await authenticate(client, 9501)
        assert (
            await client.get(f"/api/v1/classes/{chat_id}/overrides/15-01-2024")
        ).status_code == 400


async def test_member_cannot_change_a_date(db):
    settings = build_test_settings()
    chat_id = -8016
    await _onboard(chat_id)
    await _member(chat_id, 9601, role="member")

    async with web_client(settings) as (client, _s):
        await authenticate(client, 9601)
        assert (
            await client.put(
                f"/api/v1/classes/{chat_id}/overrides/2024-01-15",
                json={"day_type": "holiday"},
            )
        ).status_code == 403
        assert (
            await client.delete(f"/api/v1/classes/{chat_id}/overrides/2024-01-15")
        ).status_code == 403

    assert await dbm.get_day_override(chat_id, MON) is None


async def test_schedule_changes_are_journalled(db):
    settings = build_test_settings()
    chat_id = -8017
    await _onboard(chat_id)
    await _member(chat_id, 9701, role="admin")

    async with web_client(settings) as (client, _s):
        await authenticate(client, 9701)
        await client.put(
            f"/api/v1/classes/{chat_id}/schedule/template/0",
            json={"lessons": [{"lesson_number": 1, "subject_name": "Химия"}]},
        )
        await client.put(
            f"/api/v1/classes/{chat_id}/overrides/2024-01-15",
            json={"day_type": "free"},
        )

    assert await dbm.get_audit_logs(chat_id, "schedule")
    assert await dbm.get_audit_logs(chat_id, "day_override")

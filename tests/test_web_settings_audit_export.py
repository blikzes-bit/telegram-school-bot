"""Web API — reminder settings, audit log, and data export.

All three mirror the bot's rules exactly:
  * reminder settings are visible to any member but editable only by admins
    in a group (unrestricted in a private chat);
  * the audit log and every export are admin-only in a group (unrestricted
    in a private chat) — same as the bot's history screen / backup menu.
"""
import database.db as dbm
from tests.web_helpers import authenticate, build_test_settings, now_iso, web_client


async def _onboard(chat_id: int, chat_type: str = "group"):
    await dbm.get_or_create_chat(chat_id, chat_type)
    await dbm.finalize_onboarding(
        chat_id, chat_type, [(1, "08:00", "08:45")], {0: [(1, "Математика")]},
    )


async def _member(chat_id: int, user_id: int, role: str = "member"):
    await dbm.upsert_membership(chat_id, user_id, role, now_iso())


# --- Reminder settings --------------------------------------------------------

async def test_member_can_view_but_not_edit_settings(db):
    settings = build_test_settings()
    chat_id = -960
    await _onboard(chat_id)
    await _member(chat_id, 6001, role="member")

    async with web_client(settings) as (client, _s):
        await authenticate(client, 6001)
        resp = await client.get(f"/api/v1/classes/{chat_id}/settings/reminders")
        assert resp.status_code == 200
        body = resp.json()
        assert body["can_edit"] is False
        assert body["hw_reminder_time"] == "18:00"

        edit = await client.patch(
            f"/api/v1/classes/{chat_id}/settings/reminders",
            json={"hw_reminder_time": "19:00"},
        )
        assert edit.status_code == 403


async def test_admin_can_edit_settings(db):
    settings = build_test_settings()
    chat_id = -961
    await _onboard(chat_id)
    await _member(chat_id, 6002, role="admin")

    async with web_client(settings) as (client, _s):
        await authenticate(client, 6002)
        edit = await client.patch(
            f"/api/v1/classes/{chat_id}/settings/reminders",
            json={
                "hw_reminder_time": "19:30",
                "extra_reminder_enabled": False,
                "quiet_start": "22:00",
                "quiet_end": "07:00",
            },
        )
        assert edit.status_code == 200
        body = edit.json()
        assert body["hw_reminder_time"] == "19:30"
        assert body["extra_reminder_enabled"] is False
        assert body["quiet_start"] == "22:00"
        assert body["quiet_end"] == "07:00"

        cleared = await client.patch(
            f"/api/v1/classes/{chat_id}/settings/reminders",
            json={"clear_quiet_hours": True},
        )
        assert cleared.json()["quiet_start"] is None
        assert cleared.json()["quiet_end"] is None


async def test_private_chat_member_can_edit_settings(db):
    settings = build_test_settings()
    chat_id = 6100
    await _onboard(chat_id, chat_type="private")
    await _member(chat_id, 6100, role="member")

    async with web_client(settings) as (client, _s):
        await authenticate(client, 6100)
        resp = await client.patch(
            f"/api/v1/classes/{chat_id}/settings/reminders",
            json={"hw_duetoday_time": "08:00"},
        )
        assert resp.status_code == 200
        assert resp.json()["hw_duetoday_time"] == "08:00"


async def test_bad_time_format_is_rejected(db):
    settings = build_test_settings()
    chat_id = -962
    await _onboard(chat_id)
    await _member(chat_id, 6003, role="admin")

    async with web_client(settings) as (client, _s):
        await authenticate(client, 6003)
        resp = await client.patch(
            f"/api/v1/classes/{chat_id}/settings/reminders",
            json={"hw_reminder_time": "25:99"},
        )
        assert resp.status_code == 422


# --- Class settings (name / timezone / homework-edit policy) ------------------

async def test_class_settings_visible_to_member_read_only(db):
    settings = build_test_settings()
    chat_id = -970
    await _onboard(chat_id)
    await _member(chat_id, 6010, role="member")
    await dbm.set_chat_title(chat_id, "9-А")

    async with web_client(settings) as (client, _s):
        await authenticate(client, 6010)
        resp = await client.get(f"/api/v1/classes/{chat_id}/settings/class")
        assert resp.status_code == 200
        body = resp.json()
        assert body["title"] == "9-А"
        assert body["can_edit"] is False
        assert body["hw_edit_policy"] == "collaborative"
        # The server renders the timezone; the frontend never computes it.
        assert body["timezone"] and body["timezone_label"] and body["local_time"]

        edit = await client.patch(
            f"/api/v1/classes/{chat_id}/settings/class", json={"title": "9-Б"}
        )
        assert edit.status_code == 403

    assert (await dbm.get_chat(chat_id)).title == "9-А"


async def test_admin_can_rename_class_change_tz_and_policy(db):
    settings = build_test_settings()
    chat_id = -971
    await _onboard(chat_id)
    await _member(chat_id, 6011, role="admin")

    async with web_client(settings) as (client, _s):
        await authenticate(client, 6011)
        resp = await client.patch(
            f"/api/v1/classes/{chat_id}/settings/class",
            json={
                "title": "  10-В  ",
                "timezone": "Europe/Warsaw",
                "hw_edit_policy": "admin_only",
            },
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["title"] == "10-В"  # trimmed
        assert body["timezone"] == "Europe/Warsaw"
        assert body["hw_edit_policy"] == "admin_only"

    chat = await dbm.get_chat(chat_id)
    assert (chat.title, chat.timezone, chat.hw_edit_policy) == (
        "10-В", "Europe/Warsaw", "admin_only",
    )
    entries = await dbm.get_audit_logs(chat_id, "settings")
    assert any("часовой пояс" in (e.summary or "") for e in entries)


async def test_blank_title_clears_the_class_name(db):
    settings = build_test_settings()
    chat_id = -972
    await _onboard(chat_id)
    await _member(chat_id, 6012, role="admin")
    await dbm.set_chat_title(chat_id, "Старое")

    async with web_client(settings) as (client, _s):
        await authenticate(client, 6012)
        resp = await client.patch(
            f"/api/v1/classes/{chat_id}/settings/class", json={"title": "   "}
        )
        assert resp.status_code == 200
        assert resp.json()["title"] is None

    assert (await dbm.get_chat(chat_id)).title is None


async def test_unknown_timezone_and_policy_are_rejected(db):
    settings = build_test_settings()
    chat_id = -973
    await _onboard(chat_id)
    await _member(chat_id, 6013, role="admin")

    async with web_client(settings) as (client, _s):
        await authenticate(client, 6013)
        bad_tz = await client.patch(
            f"/api/v1/classes/{chat_id}/settings/class",
            json={"timezone": "Mars/Olympus"},
        )
        assert bad_tz.status_code == 400
        bad_policy = await client.patch(
            f"/api/v1/classes/{chat_id}/settings/class",
            json={"hw_edit_policy": "anarchy"},
        )
        assert bad_policy.status_code == 400

    chat = await dbm.get_chat(chat_id)
    assert chat.timezone != "Mars/Olympus"
    assert chat.hw_edit_policy == "collaborative"


async def test_over_long_title_is_rejected(db):
    settings = build_test_settings()
    chat_id = -974
    await _onboard(chat_id)
    await _member(chat_id, 6014, role="admin")

    async with web_client(settings) as (client, _s):
        await authenticate(client, 6014)
        resp = await client.patch(
            f"/api/v1/classes/{chat_id}/settings/class", json={"title": "я" * 101}
        )
        assert resp.status_code == 422


async def test_class_settings_report_the_resolved_profile_and_features(db):
    """A chat that was never asked still gets a profile — resolved, not null."""
    settings = build_test_settings()
    chat_id = -975
    await _onboard(chat_id)
    await _member(chat_id, 6015, role="member")

    async with web_client(settings) as (client, _s):
        await authenticate(client, 6015)
        body = (await client.get(f"/api/v1/classes/{chat_id}/settings/class")).json()

    assert body["profile"] == "class"  # a group with profile IS NULL
    assert body["features"]["school_schedule"] is True
    assert body["features"]["homework_policy"] is True
    assert {o["name"] for o in body["profile_options"]} == {"personal", "class", "tutor"}


async def test_switching_to_the_tutor_profile_drops_the_timetable_feature(db):
    settings = build_test_settings()
    chat_id = -976
    await _onboard(chat_id)
    await _member(chat_id, 6016, role="admin")

    async with web_client(settings) as (client, _s):
        await authenticate(client, 6016)
        resp = await client.patch(
            f"/api/v1/classes/{chat_id}/settings/class", json={"profile": "tutor"}
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["profile"] == "tutor"
        assert body["features"]["school_schedule"] is False
        assert body["features"]["homework"] is True

    assert (await dbm.get_chat(chat_id)).profile == "tutor"
    entries = await dbm.get_audit_logs(chat_id, "settings")
    assert any("режим чата" in (e.summary or "") for e in entries)


async def test_unknown_profile_is_rejected(db):
    settings = build_test_settings()
    chat_id = -977
    await _onboard(chat_id)
    await _member(chat_id, 6017, role="admin")

    async with web_client(settings) as (client, _s):
        await authenticate(client, 6017)
        resp = await client.patch(
            f"/api/v1/classes/{chat_id}/settings/class", json={"profile": "wizard"}
        )
        assert resp.status_code == 400

    assert (await dbm.get_chat(chat_id)).profile is None


async def test_member_may_not_switch_the_profile(db):
    settings = build_test_settings()
    chat_id = -978
    await _onboard(chat_id)
    await _member(chat_id, 6018, role="member")

    async with web_client(settings) as (client, _s):
        await authenticate(client, 6018)
        resp = await client.patch(
            f"/api/v1/classes/{chat_id}/settings/class", json={"profile": "tutor"}
        )
        assert resp.status_code == 403

    assert (await dbm.get_chat(chat_id)).profile is None


async def test_private_chat_owner_can_name_their_diary(db):
    settings = build_test_settings()
    chat_id = 6200
    await _onboard(chat_id, chat_type="private")
    await _member(chat_id, 6200, role="member")

    async with web_client(settings) as (client, _s):
        await authenticate(client, 6200)
        resp = await client.patch(
            f"/api/v1/classes/{chat_id}/settings/class", json={"title": "Мой дневник"}
        )
        assert resp.status_code == 200
        assert resp.json()["title"] == "Мой дневник"
        assert resp.json()["can_edit"] is True


# --- Audit log ----------------------------------------------------------------

async def test_audit_log_is_admin_only_in_group(db):
    settings = build_test_settings()
    chat_id = -963
    await _onboard(chat_id)
    await _member(chat_id, 6004, role="member")
    await _member(chat_id, 6005, role="admin")
    await dbm.add_audit_log(
        chat_id, "homework", "create", now_iso(),
        actor_user_id=6005, actor_name="Admin", summary="Физика",
    )

    async with web_client(settings) as (client, _s):
        await authenticate(client, 6004)
        resp = await client.get(f"/api/v1/classes/{chat_id}/audit")
        assert resp.status_code == 403

    async with web_client(settings) as (client, _s):
        await authenticate(client, 6005)
        resp = await client.get(f"/api/v1/classes/{chat_id}/audit")
        assert resp.status_code == 200
        body = resp.json()
        assert body["total"] >= 1
        assert body["items"][0]["entity_type"] == "homework"


async def test_audit_log_filters_by_entity_type(db):
    settings = build_test_settings()
    chat_id = -964
    await _onboard(chat_id)
    await _member(chat_id, 6006, role="admin")
    await dbm.add_audit_log(chat_id, "homework", "create", now_iso(), summary="Химия")
    await dbm.add_audit_log(chat_id, "extra", "create", now_iso(), summary="Шахматы")

    async with web_client(settings) as (client, _s):
        await authenticate(client, 6006)
        resp = await client.get(f"/api/v1/classes/{chat_id}/audit?entity_type=homework")
        assert resp.status_code == 200
        items = resp.json()["items"]
        assert len(items) == 1
        assert all(item["entity_type"] == "homework" for item in items)

        bad = await client.get(f"/api/v1/classes/{chat_id}/audit?entity_type=nonsense")
        assert bad.status_code == 400


# --- Export ---------------------------------------------------------------------

async def test_export_is_admin_only_in_group(db):
    settings = build_test_settings()
    chat_id = -965
    await _onboard(chat_id)
    await _member(chat_id, 6007, role="member")
    await _member(chat_id, 6008, role="admin")

    async with web_client(settings) as (client, _s):
        await authenticate(client, 6007)
        for path in ("backup.json", "audit.json", "schedule.csv", "calendar.ics"):
            resp = await client.get(f"/api/v1/classes/{chat_id}/export/{path}")
            assert resp.status_code == 403, path

    async with web_client(settings) as (client, _s):
        await authenticate(client, 6008)
        backup = await client.get(f"/api/v1/classes/{chat_id}/export/backup.json")
        assert backup.status_code == 200
        assert backup.headers["content-type"].startswith("application/json")
        assert "attachment" in backup.headers["content-disposition"]
        assert backup.json()["kind"] == "chat_backup"

        csv_resp = await client.get(f"/api/v1/classes/{chat_id}/export/schedule.csv")
        assert csv_resp.status_code == 200
        assert csv_resp.headers["content-type"].startswith("text/csv")

        ics_resp = await client.get(f"/api/v1/classes/{chat_id}/export/calendar.ics")
        assert ics_resp.status_code == 200
        assert b"BEGIN:VCALENDAR" in ics_resp.content

        audit_resp = await client.get(f"/api/v1/classes/{chat_id}/export/audit.json")
        assert audit_resp.status_code == 200
        assert audit_resp.json()["kind"] == "audit_log"

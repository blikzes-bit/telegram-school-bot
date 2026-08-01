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

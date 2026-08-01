"""Web API — extra-activity mutations (add / edit / delete).

Mirrors the bot's rule exactly: admins only in a group/supergroup, anyone in a
private chat. ``ClassContext.permissions.is_admin`` already encodes that (see
``web_api.deps.build_permissions``), so these tests exercise it through the
HTTP layer.
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


async def test_admin_can_add_weekly_activity(db):
    settings = build_test_settings()
    chat_id = -950
    await _onboard(chat_id)
    await _member(chat_id, 5001, role="admin")

    async with web_client(settings) as (client, _s):
        await authenticate(client, 5001)
        resp = await client.post(
            f"/api/v1/classes/{chat_id}/extra",
            json={
                "title": "Английский", "kind": "weekly", "day_of_week": 0,
                "start_time": "18:00", "end_time": "19:00",
            },
        )
        assert resp.status_code == 201
        body = resp.json()
        assert body["title"] == "Английский"
        assert body["can_edit"] is True


async def test_member_cannot_add_activity_in_group(db):
    settings = build_test_settings()
    chat_id = -951
    await _onboard(chat_id)
    await _member(chat_id, 5002, role="member")

    async with web_client(settings) as (client, _s):
        await authenticate(client, 5002)
        resp = await client.post(
            f"/api/v1/classes/{chat_id}/extra",
            json={"title": "Шахматы", "kind": "weekly", "day_of_week": 1, "start_time": "17:00"},
        )
        assert resp.status_code == 403


async def test_private_chat_member_can_add_activity(db):
    settings = build_test_settings()
    chat_id = 5100
    await _onboard(chat_id, chat_type="private")
    await _member(chat_id, 5100, role="member")

    async with web_client(settings) as (client, _s):
        await authenticate(client, 5100)
        resp = await client.post(
            f"/api/v1/classes/{chat_id}/extra",
            json={"title": "Плавание", "kind": "once", "activity_date": "2024-02-10", "start_time": "10:00"},
        )
        assert resp.status_code == 201


async def test_invalid_recurrence_is_rejected(db):
    settings = build_test_settings()
    chat_id = -952
    await _onboard(chat_id)
    await _member(chat_id, 5003, role="admin")

    async with web_client(settings) as (client, _s):
        await authenticate(client, 5003)
        # weekly + activity_date together is invalid.
        resp = await client.post(
            f"/api/v1/classes/{chat_id}/extra",
            json={
                "title": "Бассейн", "kind": "weekly", "day_of_week": 2,
                "activity_date": "2024-02-10", "start_time": "10:00",
            },
        )
        assert resp.status_code == 422


async def test_edit_and_delete_activity(db):
    settings = build_test_settings()
    chat_id = -953
    await _onboard(chat_id)
    await _member(chat_id, 5004, role="admin")
    activity = await dbm.add_extra_activity(chat_id, "Робототехника", "weekly", "16:00", day_of_week=3)

    async with web_client(settings) as (client, _s):
        await authenticate(client, 5004)

        edited = await client.patch(
            f"/api/v1/classes/{chat_id}/extra/{activity.id}",
            json={"location": "каб. 12"},
        )
        assert edited.status_code == 200
        assert edited.json()["location"] == "каб. 12"

        deleted = await client.delete(f"/api/v1/classes/{chat_id}/extra/{activity.id}")
        assert deleted.status_code == 204

        missing = await client.patch(
            f"/api/v1/classes/{chat_id}/extra/{activity.id}",
            json={"location": "каб. 13"},
        )
        assert missing.status_code == 404


async def test_member_cannot_edit_or_delete_in_group(db):
    settings = build_test_settings()
    chat_id = -954
    await _onboard(chat_id)
    await _member(chat_id, 5005, role="member")
    activity = await dbm.add_extra_activity(chat_id, "Танцы", "weekly", "17:30", day_of_week=4)

    async with web_client(settings) as (client, _s):
        await authenticate(client, 5005)
        resp = await client.patch(
            f"/api/v1/classes/{chat_id}/extra/{activity.id}",
            json={"location": "зал"},
        )
        assert resp.status_code == 403

        resp = await client.delete(f"/api/v1/classes/{chat_id}/extra/{activity.id}")
        assert resp.status_code == 403

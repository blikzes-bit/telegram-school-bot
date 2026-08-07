"""Web API — homework mutations (add / edit / delete / toggle completion).

Adding is unrestricted for any class member. Every change to an existing entry
is gated by ``hw_edit_policy``, enforced server-side exactly like the bot's
``services.permissions.can_edit_homework`` (see ``can_edit_homework_sync``) —
these tests exercise that gate through the HTTP layer, not just the pure
function.
"""
import datetime

import database.db as dbm
from tests.web_helpers import authenticate, build_test_settings, now_iso, web_client

MON = datetime.date(2024, 1, 15)


async def _onboard(chat_id: int, chat_type: str = "group"):
    await dbm.get_or_create_chat(chat_id, chat_type)
    await dbm.finalize_onboarding(
        chat_id, chat_type,
        [(1, "08:00", "08:45")],
        {0: [(1, "Математика")]},
    )


async def _member(chat_id: int, user_id: int, role: str = "member"):
    await dbm.upsert_membership(chat_id, user_id, role, now_iso())


async def test_add_homework_appears_in_list(db):
    settings = build_test_settings()
    chat_id = -900
    await _onboard(chat_id)
    await _member(chat_id, 4001)

    async with web_client(settings) as (client, _s):
        await authenticate(client, 4001)
        resp = await client.post(
            f"/api/v1/classes/{chat_id}/homework",
            json={"subject_name": "Математика", "due_date": "2024-02-01", "description": "стр. 10"},
        )
        assert resp.status_code == 201
        body = resp.json()
        assert body["subject_name"] == "Математика"
        assert body["can_edit"] is True

        listed = await client.get(f"/api/v1/classes/{chat_id}/homework")
        assert [h["subject_name"] for h in listed.json()] == ["Математика"]


async def test_add_homework_rejects_blank_fields(db):
    settings = build_test_settings()
    chat_id = -901
    await _onboard(chat_id)
    await _member(chat_id, 4002)

    async with web_client(settings) as (client, _s):
        await authenticate(client, 4002)
        resp = await client.post(
            f"/api/v1/classes/{chat_id}/homework",
            json={"subject_name": "  ", "due_date": "2024-02-01", "description": "x"},
        )
        assert resp.status_code == 400


async def test_complete_homework_collaborative(db):
    settings = build_test_settings()
    chat_id = -902
    await _onboard(chat_id)
    await _member(chat_id, 4003)
    hw = await dbm.add_homework(chat_id, "Физика", MON, "опыт")

    async with web_client(settings) as (client, _s):
        await authenticate(client, 4003)
        resp = await client.patch(
            f"/api/v1/classes/{chat_id}/homework/{hw.id}/complete",
            json={"is_completed": True},
        )
        assert resp.status_code == 200
        assert resp.json()["is_completed"] is True
        assert resp.json()["status"] == "completed"


async def test_complete_homework_missing_entry_is_404(db):
    settings = build_test_settings()
    chat_id = -903
    await _onboard(chat_id)
    await _member(chat_id, 4004)

    async with web_client(settings) as (client, _s):
        await authenticate(client, 4004)
        resp = await client.patch(
            f"/api/v1/classes/{chat_id}/homework/999999/complete",
            json={"is_completed": True},
        )
        assert resp.status_code == 404


async def test_admin_only_policy_blocks_non_admin(db):
    settings = build_test_settings()
    chat_id = -904
    await _onboard(chat_id)
    await dbm.set_hw_edit_policy(chat_id, "admin_only")
    await _member(chat_id, 4005, role="member")
    await _member(chat_id, 4006, role="admin")
    hw = await dbm.add_homework(chat_id, "Химия", MON, "опыт")

    async with web_client(settings) as (client, _s):
        await authenticate(client, 4005)
        resp = await client.patch(
            f"/api/v1/classes/{chat_id}/homework/{hw.id}/complete",
            json={"is_completed": True},
        )
        assert resp.status_code == 403

    async with web_client(settings) as (client, _s):
        await authenticate(client, 4006)
        resp = await client.patch(
            f"/api/v1/classes/{chat_id}/homework/{hw.id}/complete",
            json={"is_completed": True},
        )
        assert resp.status_code == 200


async def test_edit_homework_updates_fields_and_journals_it(db):
    settings = build_test_settings()
    chat_id = -906
    await _onboard(chat_id)
    await _member(chat_id, 4009)
    hw = await dbm.add_homework(chat_id, "Физика", MON, "старое")

    async with web_client(settings) as (client, _s):
        await authenticate(client, 4009)
        resp = await client.patch(
            f"/api/v1/classes/{chat_id}/homework/{hw.id}",
            json={"description": "новое", "due_date": "2024-03-05"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["description"] == "новое"
        assert body["due_date"] == "2024-03-05"
        assert body["subject_name"] == "Физика"  # untouched field stays put

    stored = await dbm.get_homework_by_id(chat_id, hw.id)
    assert stored.description == "новое"
    assert stored.due_date == datetime.date(2024, 3, 5)

    entries = await dbm.get_audit_logs(chat_id, "homework")
    assert any(e.action == "update" and "дата сдачи" in (e.summary or "") for e in entries)


async def test_edit_homework_empty_patch_is_a_no_op(db):
    settings = build_test_settings()
    chat_id = -907
    await _onboard(chat_id)
    await _member(chat_id, 4010)
    hw = await dbm.add_homework(chat_id, "Химия", MON, "опыт")

    async with web_client(settings) as (client, _s):
        await authenticate(client, 4010)
        resp = await client.patch(f"/api/v1/classes/{chat_id}/homework/{hw.id}", json={})
        assert resp.status_code == 200
        assert resp.json()["description"] == "опыт"


async def test_edit_homework_rejects_blank_field(db):
    settings = build_test_settings()
    chat_id = -908
    await _onboard(chat_id)
    await _member(chat_id, 4011)
    hw = await dbm.add_homework(chat_id, "Химия", MON, "опыт")

    async with web_client(settings) as (client, _s):
        await authenticate(client, 4011)
        resp = await client.patch(
            f"/api/v1/classes/{chat_id}/homework/{hw.id}", json={"subject_name": "   "}
        )
        assert resp.status_code == 422


async def test_edit_and_delete_respect_admin_only_policy(db):
    settings = build_test_settings()
    chat_id = -909
    await _onboard(chat_id)
    await dbm.set_hw_edit_policy(chat_id, "admin_only")
    await _member(chat_id, 4012, role="member")
    await _member(chat_id, 4013, role="admin")
    hw = await dbm.add_homework(chat_id, "История", MON, "параграф 5")

    async with web_client(settings) as (client, _s):
        await authenticate(client, 4012)
        assert (
            await client.patch(
                f"/api/v1/classes/{chat_id}/homework/{hw.id}", json={"description": "x"}
            )
        ).status_code == 403
        assert (
            await client.delete(f"/api/v1/classes/{chat_id}/homework/{hw.id}")
        ).status_code == 403

    # The rejected edit must not have touched the row.
    assert (await dbm.get_homework_by_id(chat_id, hw.id)).description == "параграф 5"

    async with web_client(settings) as (client, _s):
        await authenticate(client, 4013)
        assert (
            await client.delete(f"/api/v1/classes/{chat_id}/homework/{hw.id}")
        ).status_code == 204

    assert await dbm.get_homework_by_id(chat_id, hw.id) is None


async def test_delete_homework_missing_entry_is_404(db):
    settings = build_test_settings()
    chat_id = -910
    await _onboard(chat_id)
    await _member(chat_id, 4014)

    async with web_client(settings) as (client, _s):
        await authenticate(client, 4014)
        resp = await client.delete(f"/api/v1/classes/{chat_id}/homework/999999")
        assert resp.status_code == 404


async def test_delete_homework_scoped_to_its_own_chat(db):
    """A homework id from another class must never be reachable."""
    settings = build_test_settings()
    mine, theirs = -911, -912
    await _onboard(mine)
    await _onboard(theirs)
    await _member(mine, 4015)
    foreign = await dbm.add_homework(theirs, "Чужое", MON, "не трогать")

    async with web_client(settings) as (client, _s):
        await authenticate(client, 4015)
        resp = await client.delete(f"/api/v1/classes/{mine}/homework/{foreign.id}")
        assert resp.status_code == 404

    assert await dbm.get_homework_by_id(theirs, foreign.id) is not None


async def test_creator_or_admin_policy_allows_own_entry(db):
    settings = build_test_settings()
    chat_id = -905
    await _onboard(chat_id)
    await dbm.set_hw_edit_policy(chat_id, "creator_or_admin")
    await _member(chat_id, 4007, role="member")
    await _member(chat_id, 4008, role="member")

    async with web_client(settings) as (client, _s):
        await authenticate(client, 4007)
        created = await client.post(
            f"/api/v1/classes/{chat_id}/homework",
            json={"subject_name": "Биология", "due_date": "2024-02-01", "description": "x"},
        )
        hw_id = created.json()["id"]
        assert created.json()["can_edit"] is True

        own = await client.patch(
            f"/api/v1/classes/{chat_id}/homework/{hw_id}/complete",
            json={"is_completed": True},
        )
        assert own.status_code == 200

    async with web_client(settings) as (client, _s):
        await authenticate(client, 4008)
        other = await client.patch(
            f"/api/v1/classes/{chat_id}/homework/{hw_id}/complete",
            json={"is_completed": False},
        )
        assert other.status_code == 403

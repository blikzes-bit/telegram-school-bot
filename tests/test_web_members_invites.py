"""Web API — members, roles, the access mode and invitations.

Exercised through HTTP, because the guarantee that matters is not "the function
returns False" but "the endpoint refuses". In particular: a viewer must be
unable to write *anything* even though the same requests succeed for an editor,
and an invitation must be single-use, expiring and role-bound.
"""
import datetime

import database.db as dbm
from services import permissions as perms
from tests.web_helpers import authenticate, build_test_settings, now_iso, web_client

MON = datetime.date(2024, 1, 15)


async def _onboard(chat_id: int, chat_type: str = "group"):
    await dbm.get_or_create_chat(chat_id, chat_type)
    await dbm.finalize_onboarding(
        chat_id, chat_type, [(1, "08:00", "08:45")], {0: [(1, "Математика")]},
    )


async def _member(chat_id: int, user_id: int, role: str = "member", app_role=None):
    await dbm.upsert_membership(chat_id, user_id, role, now_iso())
    if app_role is not None:
        await dbm.set_member_app_role(chat_id, user_id, app_role)


async def _role_mode(chat_id: int, owner_id: int):
    await dbm.set_chat_owner(chat_id, owner_id)
    await dbm.set_access_mode(chat_id, perms.ACCESS_ROLES)


# --- Member list --------------------------------------------------------------

async def test_any_member_sees_the_list_but_cannot_manage(db):
    settings = build_test_settings()
    chat_id = -7001
    await _onboard(chat_id)
    await _member(chat_id, 7101, role="admin")
    await _member(chat_id, 7102, role="member")
    await _role_mode(chat_id, 7101)

    async with web_client(settings) as (client, _s):
        await authenticate(client, 7102)
        resp = await client.get(f"/api/v1/classes/{chat_id}/members")
        assert resp.status_code == 200
        body = resp.json()
        assert body["can_manage"] is False
        assert body["access_mode"] == "roles"
        assert {m["user_id"] for m in body["members"]} == {7101, 7102}
        me = next(m for m in body["members"] if m["user_id"] == 7102)
        assert me["is_self"] is True and me["role"] == "viewer"

        refused = await client.patch(
            f"/api/v1/classes/{chat_id}/members/7101", json={"app_role": "viewer"}
        )
        assert refused.status_code == 403


async def test_owner_assigns_and_clears_a_role(db):
    settings = build_test_settings()
    chat_id = -7002
    await _onboard(chat_id)
    await _member(chat_id, 7201, role="admin")
    await _member(chat_id, 7202, role="member")
    await _role_mode(chat_id, 7201)

    async with web_client(settings) as (client, _s):
        await authenticate(client, 7201)
        resp = await client.patch(
            f"/api/v1/classes/{chat_id}/members/7202", json={"app_role": "editor"}
        )
        assert resp.status_code == 200
        assert next(
            m for m in resp.json()["members"] if m["user_id"] == 7202
        )["app_role"] == "editor"

        cleared = await client.patch(
            f"/api/v1/classes/{chat_id}/members/7202", json={"app_role": None}
        )
        assert cleared.status_code == 200
        target = next(m for m in cleared.json()["members"] if m["user_id"] == 7202)
        assert target["app_role"] is None and target["role"] == "viewer"


async def test_owner_role_cannot_be_assigned_or_taken_away(db):
    settings = build_test_settings()
    chat_id = -7003
    await _onboard(chat_id)
    await _member(chat_id, 7301, role="admin")
    await _member(chat_id, 7302, role="member")
    await _role_mode(chat_id, 7301)

    async with web_client(settings) as (client, _s):
        await authenticate(client, 7301)
        # "owner" is not in the assignable set.
        assert (
            await client.patch(
                f"/api/v1/classes/{chat_id}/members/7302", json={"app_role": "owner"}
            )
        ).status_code == 400
        # The owner's own membership is off-limits, even to themselves.
        assert (
            await client.patch(
                f"/api/v1/classes/{chat_id}/members/7301", json={"app_role": "viewer"}
            )
        ).status_code == 403
        assert (
            await client.delete(f"/api/v1/classes/{chat_id}/members/7301")
        ).status_code == 403


async def test_owner_revokes_access_and_the_class_disappears(db):
    settings = build_test_settings()
    chat_id = -7004
    await _onboard(chat_id)
    await _member(chat_id, 7401, role="admin")
    await _member(chat_id, 7402, role="member")
    await _role_mode(chat_id, 7401)

    async with web_client(settings) as (client, _s):
        await authenticate(client, 7401)
        assert (
            await client.delete(f"/api/v1/classes/{chat_id}/members/7402")
        ).status_code == 204

    async with web_client(settings) as (client, _s):
        await authenticate(client, 7402)
        assert (await client.get("/api/v1/classes")).json() == []
        assert (
            await client.get(f"/api/v1/classes/{chat_id}/today")
        ).status_code == 403


async def test_unknown_member_is_404(db):
    settings = build_test_settings()
    chat_id = -7005
    await _onboard(chat_id)
    await _member(chat_id, 7501, role="admin")
    await _role_mode(chat_id, 7501)

    async with web_client(settings) as (client, _s):
        await authenticate(client, 7501)
        assert (
            await client.patch(
                f"/api/v1/classes/{chat_id}/members/999999", json={"app_role": "viewer"}
            )
        ).status_code == 404
        assert (
            await client.delete(f"/api/v1/classes/{chat_id}/members/999999")
        ).status_code == 404


# --- Roles actually gate the data endpoints -----------------------------------

async def test_viewer_can_read_but_write_nothing(db):
    settings = build_test_settings()
    chat_id = -7006
    await _onboard(chat_id)
    await _member(chat_id, 7601, role="admin")
    # A Telegram *admin* with no app role: role mode must still make them a viewer.
    await _member(chat_id, 7602, role="admin")
    await _role_mode(chat_id, 7601)
    hw = await dbm.add_homework(chat_id, "Физика", MON, "опыт")

    async with web_client(settings) as (client, _s):
        await authenticate(client, 7602)
        assert (await client.get(f"/api/v1/classes/{chat_id}/today")).status_code == 200

        assert (
            await client.post(
                f"/api/v1/classes/{chat_id}/homework",
                json={"subject_name": "X", "due_date": "2024-02-01", "description": "y"},
            )
        ).status_code == 403
        assert (
            await client.patch(
                f"/api/v1/classes/{chat_id}/homework/{hw.id}", json={"description": "z"}
            )
        ).status_code == 403
        assert (
            await client.patch(
                f"/api/v1/classes/{chat_id}/homework/{hw.id}/complete",
                json={"is_completed": True},
            )
        ).status_code == 403
        assert (
            await client.delete(f"/api/v1/classes/{chat_id}/homework/{hw.id}")
        ).status_code == 403
        assert (
            await client.post(
                f"/api/v1/classes/{chat_id}/extra",
                json={"title": "Кружок", "kind": "weekly", "day_of_week": 1,
                      "start_time": "17:00"},
            )
        ).status_code == 403
        assert (
            await client.patch(
                f"/api/v1/classes/{chat_id}/settings/reminders",
                json={"hw_reminder_time": "19:00"},
            )
        ).status_code == 403

    # Nothing was written by any of the above.
    assert (await dbm.get_homework_by_id(chat_id, hw.id)).description == "опыт"
    assert await dbm.get_extra_activities(chat_id) == []


async def test_student_may_complete_homework_but_not_edit_it(db):
    settings = build_test_settings()
    chat_id = -7007
    await _onboard(chat_id)
    await _member(chat_id, 7701, role="admin")
    await _member(chat_id, 7702, role="member", app_role=perms.ROLE_STUDENT)
    await _role_mode(chat_id, 7701)
    hw = await dbm.add_homework(chat_id, "Химия", MON, "опыт")

    async with web_client(settings) as (client, _s):
        await authenticate(client, 7702)
        listed = (await client.get(f"/api/v1/classes/{chat_id}/homework")).json()
        assert listed[0]["can_complete"] is True
        assert listed[0]["can_edit"] is False

        assert (
            await client.patch(
                f"/api/v1/classes/{chat_id}/homework/{hw.id}/complete",
                json={"is_completed": True},
            )
        ).status_code == 200
        assert (
            await client.patch(
                f"/api/v1/classes/{chat_id}/homework/{hw.id}", json={"description": "z"}
            )
        ).status_code == 403


async def test_editor_manages_content_but_not_the_schedule_or_settings(db):
    settings = build_test_settings()
    chat_id = -7008
    await _onboard(chat_id)
    await _member(chat_id, 7801, role="admin")
    await _member(chat_id, 7802, role="member", app_role=perms.ROLE_EDITOR)
    await _role_mode(chat_id, 7801)

    async with web_client(settings) as (client, _s):
        await authenticate(client, 7802)
        created = await client.post(
            f"/api/v1/classes/{chat_id}/homework",
            json={"subject_name": "Алгебра", "due_date": "2024-02-01", "description": "п.5"},
        )
        assert created.status_code == 201
        assert (
            await client.post(
                f"/api/v1/classes/{chat_id}/extra",
                json={"title": "Кружок", "kind": "weekly", "day_of_week": 1,
                      "start_time": "17:00"},
            )
        ).status_code == 201

        # Settings and member management stay with the owner.
        assert (
            await client.patch(
                f"/api/v1/classes/{chat_id}/settings/reminders",
                json={"hw_reminder_time": "19:00"},
            )
        ).status_code == 403
        perms_body = (await client.get(f"/api/v1/classes/{chat_id}/today")).json()
        assert perms_body["permissions"]["can_edit_schedule"] is False
        assert perms_body["permissions"]["role"] == "editor"


async def test_switching_the_access_mode_takes_effect_immediately(db):
    settings = build_test_settings()
    chat_id = -7009
    await _onboard(chat_id)
    await _member(chat_id, 7901, role="admin")
    await _member(chat_id, 7902, role="member")
    await dbm.set_chat_owner(chat_id, 7901)

    async with web_client(settings) as (client, _s):
        await authenticate(client, 7902)
        # Default mode: a plain member may still add homework.
        assert (
            await client.post(
                f"/api/v1/classes/{chat_id}/homework",
                json={"subject_name": "A", "due_date": "2024-02-01", "description": "b"},
            )
        ).status_code == 201

    async with web_client(settings) as (client, _s):
        await authenticate(client, 7901)
        switched = await client.put(f"/api/v1/classes/{chat_id}/access-mode?mode=roles")
        assert switched.status_code == 200
        assert switched.json()["access_mode"] == "roles"

    async with web_client(settings) as (client, _s):
        await authenticate(client, 7902)
        assert (
            await client.post(
                f"/api/v1/classes/{chat_id}/homework",
                json={"subject_name": "A", "due_date": "2024-02-01", "description": "b"},
            )
        ).status_code == 403


async def test_unknown_access_mode_is_rejected(db):
    settings = build_test_settings()
    chat_id = -7010
    await _onboard(chat_id)
    await _member(chat_id, 7950, role="admin")
    await dbm.set_chat_owner(chat_id, 7950)

    async with web_client(settings) as (client, _s):
        await authenticate(client, 7950)
        assert (
            await client.put(f"/api/v1/classes/{chat_id}/access-mode?mode=anarchy")
        ).status_code == 400


# --- Invitations --------------------------------------------------------------

async def test_invite_grants_exactly_its_role_to_an_outsider(db):
    """The whole point: somebody not in the Telegram chat gets in, read-only."""
    settings = build_test_settings()
    chat_id = -7011
    await _onboard(chat_id)
    await _member(chat_id, 8001, role="admin")
    await _role_mode(chat_id, 8001)

    async with web_client(settings) as (client, _s):
        await authenticate(client, 8001)
        created = await client.post(
            f"/api/v1/classes/{chat_id}/invites",
            json={"app_role": "viewer", "ttl_hours": 24},
        )
        assert created.status_code == 201
        token = created.json()["token"]
        assert token and created.json()["url"]

    # A user with no membership at all.
    async with web_client(settings) as (client, _s):
        await authenticate(client, 8002)
        assert (await client.get("/api/v1/classes")).json() == []

        accepted = await client.post("/api/v1/invites/accept", json={"token": token})
        assert accepted.status_code == 200
        assert accepted.json()["app_role"] == "viewer"

        assert [c["chat_id"] for c in (await client.get("/api/v1/classes")).json()] == [chat_id]
        assert (await client.get(f"/api/v1/classes/{chat_id}/today")).status_code == 200
        # Granted role, not more.
        assert (
            await client.post(
                f"/api/v1/classes/{chat_id}/homework",
                json={"subject_name": "A", "due_date": "2024-02-01", "description": "b"},
            )
        ).status_code == 403


async def test_invite_arriving_inside_signed_initdata_is_redeemed_at_login(db):
    """The real path: the deep link puts ``inv_<token>`` into start_param."""
    settings = build_test_settings()
    chat_id = -7019
    await _onboard(chat_id)
    await _member(chat_id, 8801, role="admin")
    await _role_mode(chat_id, 8801)

    async with web_client(settings) as (client, _s):
        await authenticate(client, 8801)
        token = (await client.post(
            f"/api/v1/classes/{chat_id}/invites", json={"app_role": "student"}
        )).json()["token"]

    async with web_client(settings) as (client, _s):
        resp = await authenticate(client, 8802, start_param=f"inv_{token}")
        assert resp.status_code == 200
        assert [c["chat_id"] for c in (await client.get("/api/v1/classes")).json()] == [chat_id]


async def test_a_dead_invite_still_lets_you_sign_in(db):
    """A spent link must not turn into a login loop — you get in, with no class."""
    settings = build_test_settings()
    async with web_client(settings) as (client, _s):
        resp = await authenticate(client, 8901, start_param="inv_never-existed")
        assert resp.status_code == 200
        assert (await client.get("/api/v1/classes")).json() == []


async def test_an_invite_is_single_use(db):
    settings = build_test_settings()
    chat_id = -7012
    await _onboard(chat_id)
    await _member(chat_id, 8101, role="admin")
    await _role_mode(chat_id, 8101)

    async with web_client(settings) as (client, _s):
        await authenticate(client, 8101)
        token = (await client.post(
            f"/api/v1/classes/{chat_id}/invites", json={"app_role": "student"}
        )).json()["token"]

    async with web_client(settings) as (client, _s):
        await authenticate(client, 8102)
        assert (await client.post("/api/v1/invites/accept", json={"token": token})).status_code == 200

    async with web_client(settings) as (client, _s):
        await authenticate(client, 8103)
        second = await client.post("/api/v1/invites/accept", json={"token": token})
        assert second.status_code == 400
        assert (await client.get("/api/v1/classes")).json() == []


async def test_an_expired_invite_is_refused(db):
    settings = build_test_settings()
    chat_id = -7013
    await _onboard(chat_id)
    await _member(chat_id, 8201, role="admin")
    await _role_mode(chat_id, 8201)

    # Minted directly so it can be given a past expiry.
    from web_api.security import generate_token, hash_token
    raw = generate_token()
    past = (
        datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(hours=1)
    ).isoformat()
    await dbm.create_chat_invite(
        hash_token(settings.session_secret, raw), chat_id, "viewer",
        created_at=past, expires_at=past,
    )

    async with web_client(settings) as (client, _s):
        await authenticate(client, 8202)
        assert (await client.post("/api/v1/invites/accept", json={"token": raw})).status_code == 400
        assert (await client.get("/api/v1/classes")).json() == []


async def test_a_bogus_token_is_refused(db):
    settings = build_test_settings()
    async with web_client(settings) as (client, _s):
        await authenticate(client, 8301)
        assert (
            await client.post("/api/v1/invites/accept", json={"token": "not-a-token"})
        ).status_code == 400
        assert (await client.post("/api/v1/invites/accept", json={"token": "  "})).status_code == 400


async def test_only_the_owner_may_mint_or_list_invites(db):
    settings = build_test_settings()
    chat_id = -7014
    await _onboard(chat_id)
    await _member(chat_id, 8401, role="admin")
    await _member(chat_id, 8402, role="member", app_role=perms.ROLE_EDITOR)
    await _role_mode(chat_id, 8401)

    async with web_client(settings) as (client, _s):
        await authenticate(client, 8402)
        assert (
            await client.post(
                f"/api/v1/classes/{chat_id}/invites", json={"app_role": "viewer"}
            )
        ).status_code == 403
        assert (await client.get(f"/api/v1/classes/{chat_id}/invites")).status_code == 403


async def test_a_revoked_invite_can_no_longer_be_used(db):
    settings = build_test_settings()
    chat_id = -7015
    await _onboard(chat_id)
    await _member(chat_id, 8501, role="admin")
    await _role_mode(chat_id, 8501)

    async with web_client(settings) as (client, _s):
        await authenticate(client, 8501)
        created = (await client.post(
            f"/api/v1/classes/{chat_id}/invites", json={"app_role": "viewer"}
        )).json()
        listed = (await client.get(f"/api/v1/classes/{chat_id}/invites")).json()
        # Listing never re-exposes the token.
        assert listed[0]["token"] is None and listed[0]["url"] is None

        assert (
            await client.delete(f"/api/v1/classes/{chat_id}/invites/{created['id']}")
        ).status_code == 204

    async with web_client(settings) as (client, _s):
        await authenticate(client, 8502)
        assert (
            await client.post("/api/v1/invites/accept", json={"token": created["token"]})
        ).status_code == 400


async def test_an_invite_id_from_another_chat_cannot_be_revoked(db):
    settings = build_test_settings()
    mine, theirs = -7016, -7017
    await _onboard(mine)
    await _onboard(theirs)
    await _member(mine, 8601, role="admin")
    await _member(theirs, 8602, role="admin")
    await _role_mode(mine, 8601)
    await _role_mode(theirs, 8602)

    async with web_client(settings) as (client, _s):
        await authenticate(client, 8602)
        foreign_id = (await client.post(
            f"/api/v1/classes/{theirs}/invites", json={"app_role": "viewer"}
        )).json()["id"]

    async with web_client(settings) as (client, _s):
        await authenticate(client, 8601)
        assert (
            await client.delete(f"/api/v1/classes/{mine}/invites/{foreign_id}")
        ).status_code == 404

    assert len(await dbm.get_chat_invites(theirs)) == 1


async def test_invite_role_must_be_assignable(db):
    settings = build_test_settings()
    chat_id = -7018
    await _onboard(chat_id)
    await _member(chat_id, 8701, role="admin")
    await _role_mode(chat_id, 8701)

    async with web_client(settings) as (client, _s):
        await authenticate(client, 8701)
        assert (
            await client.post(
                f"/api/v1/classes/{chat_id}/invites", json={"app_role": "owner"}
            )
        ).status_code == 400

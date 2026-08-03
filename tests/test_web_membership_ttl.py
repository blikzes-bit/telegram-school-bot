"""Web API — membership expiry.

Only the bot can ask Telegram whether someone is still in a chat, so the API
treats ``ChatMembership.last_verified_at`` as a vouch that ages out. Without
this, a user removed from the class — or demoted from admin — would keep their
Mini App access indefinitely, since sessions slide forward on every request.
"""
import datetime

import database.db as dbm
from web_api.security import generate_token, hash_token
from tests.web_helpers import (
    TEST_SESSION_SECRET, authenticate, build_test_settings, iso_in, now_iso,
    web_client,
)

CHAT_ID = -900
USER_ID = 9001

DAY = 24 * 3600


def _ago(seconds: int) -> str:
    return (
        datetime.datetime.now(datetime.timezone.utc)
        - datetime.timedelta(seconds=seconds)
    ).isoformat()


async def _member(verified_at: str, role: str = "member"):
    await dbm.get_or_create_chat(CHAT_ID, "group")
    await dbm.upsert_membership(CHAT_ID, USER_ID, role, verified_at)


async def _get_class(client):
    return await client.get(f"/api/v1/classes/{CHAT_ID}/homework")


async def test_recently_verified_membership_is_accepted(db):
    settings = build_test_settings(membership_max_age_seconds=30 * DAY)
    await _member(now_iso())

    async with web_client(settings) as (client, _s):
        await authenticate(client, USER_ID)
        assert (await _get_class(client)).status_code == 200


async def test_membership_just_inside_the_window_is_accepted(db):
    """Boundary: 29 days old against a 30-day limit still works."""
    settings = build_test_settings(membership_max_age_seconds=30 * DAY)
    await _member(_ago(29 * DAY))

    async with web_client(settings) as (client, _s):
        await authenticate(client, USER_ID)
        assert (await _get_class(client)).status_code == 200


async def test_stale_membership_is_refused(db):
    settings = build_test_settings(membership_max_age_seconds=30 * DAY)
    await _member(_ago(31 * DAY))

    async with web_client(settings) as (client, _s):
        await authenticate(client, USER_ID)
        resp = await _get_class(client)
        assert resp.status_code == 403
        # The message must point at the fix, not just deny.
        assert "/web" in resp.json()["detail"]


async def test_zero_disables_the_check(db):
    """MEMBERSHIP_MAX_AGE=0 keeps the pre-existing behaviour, like AUDIT_RETENTION_DAYS."""
    settings = build_test_settings(membership_max_age_seconds=0)
    await _member(_ago(3650 * DAY))

    async with web_client(settings) as (client, _s):
        await authenticate(client, USER_ID)
        assert (await _get_class(client)).status_code == 200


async def test_unparseable_timestamp_fails_closed(db):
    """Corrupt timestamp must deny access, not raise a 500."""
    settings = build_test_settings(membership_max_age_seconds=30 * DAY)
    await _member("not-a-timestamp")

    async with web_client(settings) as (client, _s):
        await authenticate(client, USER_ID)
        assert (await _get_class(client)).status_code == 403


async def test_running_web_again_restores_access(db):
    """The denial is self-healing: a fresh launch token re-verifies the membership."""
    settings = build_test_settings(membership_max_age_seconds=30 * DAY)
    await _member(_ago(31 * DAY))

    async with web_client(settings) as (client, _s):
        await authenticate(client, USER_ID)
        assert (await _get_class(client)).status_code == 403

        # What the bot does on /web: mint a token bound to (user, chat).
        raw_token = generate_token()
        await dbm.create_launch_token(
            hash_token(TEST_SESSION_SECRET, raw_token),
            USER_ID,
            CHAT_ID,
            now_iso(),
            iso_in(600),
        )
        await authenticate(client, USER_ID, start_param=raw_token)

        assert (await _get_class(client)).status_code == 200


async def test_expiry_is_per_class(db):
    """A stale membership in one class must not affect a fresh one in another."""
    other_chat = -901
    settings = build_test_settings(membership_max_age_seconds=30 * DAY)
    await _member(_ago(31 * DAY))
    await dbm.get_or_create_chat(other_chat, "group")
    await dbm.upsert_membership(other_chat, USER_ID, "member", now_iso())

    async with web_client(settings) as (client, _s):
        await authenticate(client, USER_ID)
        assert (await _get_class(client)).status_code == 403
        fresh = await client.get(f"/api/v1/classes/{other_chat}/homework")
        assert fresh.status_code == 200

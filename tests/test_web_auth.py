"""Web API — authentication & access-control tests.

Covers the security-critical paths from the stage-1 spec: initData signature
(valid/invalid), auth_date freshness, launch-token single-use / wrong-user, and
foreign-chat 403.
"""
import time

import database.db as dbm
from tests.web_helpers import (
    authenticate, build_test_settings, iso_in, make_init_data, now_iso,
    web_client,
)
from web_api.security import hash_token


async def _mint_launch_token(secret: str, raw: str, user_id: int, chat_id: int,
                             *, ttl: int = 600):
    await dbm.create_launch_token(
        hash_token(secret, raw), user_id, chat_id, now_iso(), iso_in(ttl)
    )


async def test_valid_initdata_opens_session(db):
    async with web_client() as (client, _settings):
        resp = await authenticate(client, 1001, first_name="Ann")
        assert resp.status_code == 200
        body = resp.json()
        assert body["telegram_user_id"] == 1001
        assert body["display_name"] == "Ann"

        # The session cookie now authenticates /me.
        me = await client.get("/api/v1/me")
        assert me.status_code == 200
        assert me.json()["telegram_user_id"] == 1001


async def test_invalid_signature_rejected(db):
    async with web_client() as (client, _settings):
        init_data = make_init_data(1002, tamper=True)
        resp = await client.post(
            "/api/v1/auth/telegram", json={"init_data": init_data}
        )
        assert resp.status_code == 401


async def test_wrong_bot_token_signature_rejected(db):
    async with web_client() as (client, _settings):
        init_data = make_init_data(1003, bot_token="999999:SOME-OTHER-TOKEN")
        resp = await client.post(
            "/api/v1/auth/telegram", json={"init_data": init_data}
        )
        assert resp.status_code == 401


async def test_expired_auth_date_rejected(db):
    async with web_client() as (client, _settings):
        old = int(time.time()) - 48 * 3600  # older than the 24h max age
        resp = await authenticate(client, 1004, auth_date=old)
        assert resp.status_code == 401


async def test_launch_token_single_use(db):
    settings = build_test_settings()
    await dbm.get_or_create_chat(-500, "group")
    raw = "launch-raw-token-abc"
    await _mint_launch_token(settings.session_secret, raw, 1005, -500)

    async with web_client(settings) as (client, _s):
        first = await authenticate(client, 1005, start_param=raw)
        assert first.status_code == 200
        # Membership was established by consuming the token.
        assert await dbm.get_membership(-500, 1005) is not None

    # A second exchange with the same token must fail (single-use).
    async with web_client(settings) as (client, _s):
        second = await authenticate(client, 1005, start_param=raw)
        assert second.status_code == 401


async def test_launch_token_other_user_forbidden(db):
    settings = build_test_settings()
    await dbm.get_or_create_chat(-501, "group")
    raw = "launch-raw-token-bound-to-1006"
    await _mint_launch_token(settings.session_secret, raw, 1006, -501)

    async with web_client(settings) as (client, _s):
        # A different Telegram user presents someone else's launch token.
        resp = await authenticate(client, 9999, start_param=raw)
        assert resp.status_code == 403
        assert await dbm.get_membership(-501, 9999) is None


async def test_expired_launch_token_rejected(db):
    settings = build_test_settings()
    await dbm.get_or_create_chat(-502, "group")
    raw = "expired-launch-token"
    # Already-expired token.
    await dbm.create_launch_token(
        hash_token(settings.session_secret, raw), 1007, -502, now_iso(),
        iso_in(-60),
    )
    async with web_client(settings) as (client, _s):
        resp = await authenticate(client, 1007, start_param=raw)
        assert resp.status_code == 401


async def test_foreign_chat_returns_403(db):
    settings = build_test_settings()
    await dbm.get_or_create_chat(-600, "group")
    await dbm.get_or_create_chat(-601, "group")
    await dbm.upsert_membership(-600, 1008, "member", now_iso())

    async with web_client(settings) as (client, _s):
        await authenticate(client, 1008)
        # Member of -600 tries to read -601 (no membership) -> 403, not empty.
        resp = await client.get("/api/v1/classes/-601/today")
        assert resp.status_code == 403


async def test_unauthenticated_me_is_401(db):
    async with web_client() as (client, _s):
        resp = await client.get("/api/v1/me")
        assert resp.status_code == 401


async def test_logout_invalidates_session(db):
    async with web_client() as (client, settings):
        await authenticate(client, 1009)
        assert (await client.get("/api/v1/me")).status_code == 200

        logout = await client.post("/api/v1/auth/logout")
        assert logout.status_code == 204
        # Cookie cleared -> /me now unauthenticated.
        client.cookies.clear()
        assert (await client.get("/api/v1/me")).status_code == 401


async def test_health_is_public(db):
    async with web_client() as (client, _s):
        resp = await client.get("/api/v1/health")
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"


async def test_cleanup_expired_web_auth_prunes_stale_rows(db):
    secret = "test-fixed-session-secret"
    before = now_iso()
    chat_id = -900
    await dbm.get_or_create_chat(chat_id, "group")  # launch tokens FK -> chats

    # Sessions: one already expired, one still valid.
    await dbm.create_web_session(hash_token(secret, "s-old"), 1, now_iso(), iso_in(-10))
    await dbm.create_web_session(hash_token(secret, "s-new"), 1, now_iso(), iso_in(3600))

    # Launch tokens: fresh-unused (kept), expired-unused (dropped), used (dropped).
    await dbm.create_launch_token(hash_token(secret, "t-fresh"), 1, chat_id, now_iso(), iso_in(600))
    await dbm.create_launch_token(hash_token(secret, "t-exp"), 1, chat_id, now_iso(), iso_in(-10))
    await dbm.create_launch_token(hash_token(secret, "t-used"), 1, chat_id, now_iso(), iso_in(600))
    await dbm.consume_launch_token(hash_token(secret, "t-used"), now_iso())

    removed = await dbm.cleanup_expired_web_auth(before)
    assert removed == 3  # 1 expired session + expired-unused + used token

    # Valid session survives; expired one is gone.
    assert await dbm.get_web_session(hash_token(secret, "s-new")) is not None
    assert await dbm.get_web_session(hash_token(secret, "s-old")) is None

    # The fresh, unused launch token is still consumable exactly once.
    kept = await dbm.consume_launch_token(hash_token(secret, "t-fresh"), now_iso())
    assert kept is not None
    # The used token was purged, so it cannot be found/re-consumed.
    assert await dbm.consume_launch_token(hash_token(secret, "t-used"), now_iso()) is None


async def test_logout_all_invalidates_every_session(db):
    settings = build_test_settings()
    async with web_client(settings) as (client_a, _s):
        await authenticate(client_a, 5001)
        async with web_client(settings) as (client_b, _s2):
            await authenticate(client_b, 5001)
            assert (await client_a.get("/api/v1/me")).status_code == 200
            assert (await client_b.get("/api/v1/me")).status_code == 200

            resp = await client_a.post("/api/v1/auth/logout-all")
            assert resp.status_code == 204

            # Every session of the user is gone, on both clients.
            assert (await client_a.get("/api/v1/me")).status_code == 401
            assert (await client_b.get("/api/v1/me")).status_code == 401


async def test_auth_rate_limit_blocks_after_threshold(db):
    settings = build_test_settings(auth_rate_limit=2, auth_rate_window_seconds=60)
    async with web_client(settings) as (client, _s):
        assert (await authenticate(client, 6001)).status_code == 200
        assert (await authenticate(client, 6001)).status_code == 200
        # Third attempt within the window is refused.
        assert (await authenticate(client, 6001)).status_code == 429


async def _user_session_expiry(user_id: int) -> str:
    from sqlalchemy import select as _select

    from database.models import WebSession
    async with dbm.AsyncSessionLocal() as s:
        row = (
            await s.execute(_select(WebSession).where(WebSession.user_id == user_id))
        ).scalar_one()
        return row.expires_at


async def test_session_sliding_expiration_extends_expiry(db):
    settings = build_test_settings()  # session_ttl_seconds == 3600
    async with web_client(settings) as (client, _s):
        await authenticate(client, 7001)  # real cookie via Set-Cookie

        # Push the session near expiry (past halfway of a 3600s ttl) so the next
        # authenticated request must slide it forward.
        near = iso_in(10)
        from sqlalchemy import update as _update

        from database.models import WebSession
        async with dbm.AsyncSessionLocal() as s:
            await s.execute(
                _update(WebSession)
                .where(WebSession.user_id == 7001)
                .values(expires_at=near)
            )
            await s.commit()

        assert (await client.get("/api/v1/me")).status_code == 200

    after = await _user_session_expiry(7001)
    # ISO-8601 UTC strings compare chronologically; the session was extended.
    assert after > near

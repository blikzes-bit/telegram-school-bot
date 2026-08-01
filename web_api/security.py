"""Security primitives for the Mini App: initData verification + token hashing.

No secret ever leaves this module in a reversible form:

  * launch tokens and session tokens are random 256-bit values; only their
    keyed HMAC-SHA256 hash (peppered with ``SESSION_SECRET``) is stored, so a
    database leak alone cannot reconstruct or verify a token;
  * Telegram ``initData`` is verified with the official algorithm (HMAC-SHA256
    keyed by ``HMAC_SHA256("WebAppData", bot_token)``), and its ``auth_date`` is
    range-checked so stale payloads are rejected.
"""
import hashlib
import hmac
import json
import secrets
from dataclasses import dataclass
from typing import Optional
from urllib.parse import parse_qsl


class InitDataError(Exception):
    """Raised when Telegram initData fails verification or freshness checks."""


@dataclass(frozen=True)
class VerifiedInitData:
    user_id: int
    display_name: Optional[str]
    auth_date: int
    start_param: Optional[str]


def generate_token() -> str:
    """A fresh, URL-safe, cryptographically random token (~256 bits)."""
    return secrets.token_urlsafe(32)


def hash_token(secret: str, token: str) -> str:
    """Keyed (peppered) SHA-256 hash of a token, hex-encoded.

    Uses HMAC with the server ``SESSION_SECRET`` so the stored value is useless
    without both the token *and* the secret.
    """
    return hmac.new(secret.encode("utf-8"), token.encode("utf-8"), hashlib.sha256).hexdigest()


def _telegram_secret_key(bot_token: str) -> bytes:
    return hmac.new(b"WebAppData", bot_token.encode("utf-8"), hashlib.sha256).digest()


def _display_name(user: dict) -> Optional[str]:
    parts = [str(user.get("first_name", "")).strip(), str(user.get("last_name", "")).strip()]
    name = " ".join(p for p in parts if p).strip()
    return name or None


def verify_init_data(
    init_data: str, bot_token: str, *, max_age_seconds: int, now_ts: int
) -> VerifiedInitData:
    """Verify a raw Telegram Mini App ``initData`` query string.

    Returns the minimal, trustworthy fields (user id + display name + auth_date
    + start_param). Raises ``InitDataError`` on any tampering, a missing hash, a
    missing/blank user, or an ``auth_date`` older than ``max_age_seconds`` (or in
    the future). Only ``initData`` — never ``initDataUnsafe`` — must be passed in.
    """
    if not init_data:
        raise InitDataError("empty initData")

    pairs = parse_qsl(init_data, keep_blank_values=True)
    data = dict(pairs)

    received_hash = data.pop("hash", None)
    if not received_hash:
        raise InitDataError("initData is missing the hash field")

    data_check_string = "\n".join(f"{k}={data[k]}" for k in sorted(data))
    secret_key = _telegram_secret_key(bot_token)
    computed_hash = hmac.new(
        secret_key, data_check_string.encode("utf-8"), hashlib.sha256
    ).hexdigest()

    if not hmac.compare_digest(computed_hash, received_hash):
        raise InitDataError("initData hash mismatch")

    raw_auth_date = data.get("auth_date")
    if not raw_auth_date:
        raise InitDataError("initData is missing auth_date")
    try:
        auth_date = int(raw_auth_date)
    except ValueError:
        raise InitDataError("initData auth_date is not an integer")

    age = now_ts - auth_date
    if age > max_age_seconds:
        raise InitDataError("initData is expired")
    # A small negative tolerance absorbs minor clock skew; a large future
    # auth_date is treated as invalid.
    if age < -300:
        raise InitDataError("initData auth_date is in the future")

    raw_user = data.get("user")
    if not raw_user:
        raise InitDataError("initData is missing user")
    try:
        user = json.loads(raw_user)
    except (ValueError, TypeError):
        raise InitDataError("initData user is not valid JSON")

    user_id = user.get("id")
    if not isinstance(user_id, int):
        raise InitDataError("initData user has no integer id")

    start_param = data.get("start_param") or None

    return VerifiedInitData(
        user_id=user_id,
        display_name=_display_name(user),
        auth_date=auth_date,
        start_param=start_param,
    )

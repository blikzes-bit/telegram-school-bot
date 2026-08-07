# Telegram Mini App — Architecture

This document describes the **web** surface added on top of the existing Telegram
bot: a secure Telegram **Mini App** covering authorisation → class picker →
*Today* / *Schedule* / *Homework* / *Extra* / *Payments* / *Members* / *Settings*,
with full read **and write** access gated by the shared permission core.

The bot itself is unchanged in behaviour. The web app and the bot **share the
same domain logic** (schedule resolution, extra-activity filtering) and the same
SQLite database — they are two adapters over one core, not two implementations.

---

## 1. Components

```
                       ┌──────────────────────────┐
                       │      Telegram client     │
                       │  (chat  +  Mini App WebView)
                       └───────────┬──────────────┘
              /web command         │  initData (signed)
        ┌───────────────────┐      │
        │  Telegram Bot     │      ▼
        │  (aiogram)        │  ┌──────────────────────┐    ┌──────────────┐
        │  handlers/…       │  │  Web API (FastAPI)   │    │  Frontend    │
        │                   │  │  web_api/…           │◄───┤ React + Vite │
        └─────────┬─────────┘  └──────────┬───────────┘    │  web/        │
                  │                        │               └──────────────┘
                  │   application/ (shared DTOs + query use-cases)
                  │                        │
                  ▼                        ▼
        ┌────────────────────────────────────────────────┐
        │  services/  (effective_schedule, timeservice,   │
        │             extra_activities, permissions …)    │
        ├────────────────────────────────────────────────┤
        │  database/  (SQLAlchemy 2 async + SQLite)        │
        └────────────────────────────────────────────────┘
```

Directory roles:

| Path            | Role |
|-----------------|------|
| `handlers/`     | Telegram adapter (unchanged). |
| `web_api/`      | FastAPI app: settings, security, dependencies, v1 routers. **Never imports `handlers/`.** |
| `application/`  | Shared DTOs (Pydantic) + query use-cases + repository protocols. The single place ORM → DTO mapping happens. |
| `services/`     | Domain logic reused by both the bot and the API. |
| `database/`     | Models + async CRUD + migrations. |
| `web/`          | React + TypeScript + Vite frontend. |

**Key rule:** the API returns only `application/dto.py` Pydantic DTOs; SQLAlchemy
ORM instances are never serialised directly, so a new column can never silently
leak over the wire.

---

## 2. Authentication & authorisation flow

```
  User in chat                Bot                     API                    Frontend
      │  /web                  │                        │                        │
      │───────────────────────►│ verify membership      │                        │
      │                        │ (get_chat_member)      │                        │
      │                        │ upsert ChatMembership  │                        │
      │                        │ mint launch token      │                        │
      │                        │ (store HASH only,      │                        │
      │                        │  TTL 10 min, 1-use)    │                        │
      │  deep link with        │                        │                        │
      │  startapp=<token>  ◄───│                        │                        │
      │                        │                        │                        │
      │  opens Mini App ─────────────────────────────────────────────────────►  │
      │                        │                        │  POST /auth/telegram   │
      │                        │                        │◄───── initData ────────│
      │                        │                        │ verify HMAC signature  │
      │                        │                        │ check auth_date        │
      │                        │                        │ consume launch token   │
      │                        │                        │  (hash lookup, unused, │
      │                        │                        │   unexpired, same user)│
      │                        │                        │ touch ChatMembership   │
      │                        │                        │ create web session     │
      │                        │                        │  (cookie = random,     │
      │                        │                        │   DB = hash)           │
      │                        │                        │──── Set-Cookie ───────►│
```

Guarantees (all enforced **server-side**, before any tenant data is touched):

1. **Signed `initData` only.** Verified with Telegram's official algorithm
   (`HMAC_SHA256(key = HMAC_SHA256("WebAppData", bot_token), data_check_string)`).
   `initDataUnsafe`, query params and `localStorage` are never trusted.
2. **Freshness.** `auth_date` older than `INITDATA_MAX_AGE` (default 24 h) or in
   the future is rejected.
3. **Launch tokens** are 256-bit random, **hashed** (HMAC peppered with
   `SESSION_SECRET`) before storage, **single-use** (atomic `UPDATE … WHERE
   used_at IS NULL`), **10-minute TTL**, and **bound to (telegram_user_id,
   chat_id)**. A token presented by another user → `403`.
4. **Membership is verified before a token is issued** (the caller ran `/web`
   from inside the chat; their admin/member role is read from Telegram).
5. **Opaque sessions.** The cookie holds only a random token; the DB stores only
   its hash. Cookie is `HttpOnly`, `SameSite=Lax`, and `Secure` in production.
6. **Minimal PII.** Only Telegram id + display name are stored — never username,
   phone, or the raw Update.
7. **Unknown / unverified `chat_id` → `403`**, never an empty body
   (`web_api/deps.require_class`).
8. **Every data query is scoped by `chat_id`.**
9. **Brute-force guard.** `POST /auth/telegram` is rate-limited per client IP
   (`AUTH_RATE_LIMIT` / `AUTH_RATE_WINDOW`); over the limit → `429`. The limiter
   is process-local (single-host stage 1); stage 2 replaces it with a shared
   store.
10. **Sliding sessions.** An authenticated request past the halfway point of a
    session's lifetime extends it (best-effort), so active users are not logged
    out mid-use; `POST /auth/logout-all` drops all of a user's sessions. Expired
    sessions and used/expired launch tokens are pruned nightly
    (`db.cleanup_expired_web_auth`).

---

## 3. API (v1)

Base path `/api/v1`. All non-auth endpoints require the session cookie; class
endpoints additionally require a verified membership (else `403`).

| Method | Path | Description |
|--------|------|-------------|
| GET  | `/health` | Liveness (public). |
| POST | `/auth/telegram` | Exchange `initData` (+ optional launch token) for a session. |
| POST | `/auth/logout` | Invalidate the current session, clear the cookie. |
| POST | `/auth/logout-all` | Invalidate **every** session of the current user. |
| GET  | `/me` | Current web user (id + display name). |
| GET  | `/classes` | Classes the user has a verified membership in. |
| GET  | `/classes/{chat_id}/today?date=YYYY-MM-DD` | Dashboard (mirror of the bot's `/today`). |
| GET  | `/classes/{chat_id}/schedule?from=…&to=…` | Effective schedule per day (≤ 62 days). |
| GET  | `/classes/{chat_id}/homework?status=active\|completed\|overdue` | Homework list. |
| GET  | `/classes/{chat_id}/extra?from=…&to=…` | Extra activities in a window. |
| POST/PATCH/DELETE | `/classes/{chat_id}/homework[/{id}]` | Add, edit, delete homework. |
| PATCH | `/classes/{chat_id}/homework/{id}/complete` | Tick homework off (a looser gate than editing). |
| POST/PATCH/DELETE | `/classes/{chat_id}/extra[/{id}]` | Manage extra activities. |
| GET/PUT | `/classes/{chat_id}/schedule/template`, `…/schedule/slots` | The weekly template and bell times. |
| GET/PUT/DELETE | `/classes/{chat_id}/overrides/{date}[/lessons/{n}]` | Per-date changes. |
| GET/POST/PATCH/DELETE | `/classes/{chat_id}/payments[/{id}]` | Lesson payments (tutor profile). |
| GET/PATCH/DELETE | `/classes/{chat_id}/members[/{user_id}]` | Members and their app roles. |
| PUT | `/classes/{chat_id}/access-mode?mode=…` | Switch Telegram-derived ↔ role-based rights. |
| GET/POST/DELETE | `/classes/{chat_id}/invites[/{id}]` | Invitation links (owner only). |
| POST | `/invites/accept` | Redeem an invitation (session required, membership not). |
| GET/PATCH | `/classes/{chat_id}/settings/class`, `…/settings/reminders` | Chat settings. |
| GET  | `/classes/{chat_id}/audit`, `…/export/*` | History and exports. |

The **Today** payload contains: the class's local date + timezone, the effective
schedule (A/B week + per-date overrides applied), homework buckets (due today /
overdue / upcoming), extra activities, and the current user's permissions.

OpenAPI/Swagger (`/api/docs`, `/api/v1/openapi.json`) is exposed **only in
development** (or when `WEB_ENABLE_OPENAPI` is set).

---

## 4. Data model (new tables)

All new timestamps are **UTC ISO-8601** strings (matching the existing
authorship/outbox convention). All new tables have FKs, constraints and indexes.

| Table | Purpose | Notes |
|-------|---------|-------|
| `web_users` | A person using the web app | Telegram id (PK) + display name only. |
| `chat_memberships` | Which user may access which class | Unique `(chat_id, user_id)`; role `member`/`admin`; `last_verified_at`; FK → `chats`. |
| `web_launch_tokens` | One-time deep-link tokens | Stores only the token **hash**; `used_at`, `expires_at`; bound to `(telegram_user_id, chat_id)`. |
| `web_sessions` | Opaque cookie sessions | Stores only the session-token **hash**; `expires_at`. |
| `chats.title` | Display name of a class | New **nullable** column; existing rows unaffected. |

Migration: `alembic/versions/e5c1a2f3d4b6_add_web_app_models.py`
(`down_revision = d1a6c85b3e97`). `alembic revision --autogenerate` produces an
empty diff against the models.

---

## 5. Configuration

New settings (see `.env.example`), read once into a typed `web_api/settings.py`
`WebSettings`:

| Variable | Default | Meaning |
|----------|---------|---------|
| `APP_ENV` | `development` | `development` \| `production`. Gates OpenAPI + cookie `Secure`. |
| `WEB_APP_URL` | `http://localhost:5173` | Frontend origin (dev fallback launch URL). |
| `WEB_APP_SHORT_NAME` | *(empty)* | Mini App short name for the `t.me/<bot>/<short_name>?startapp=` deep link. |
| `SESSION_SECRET` | dev-only default | Pepper for token hashing + session integrity. **Required in production.** |
| `SESSION_TTL` | `604800` | Web session lifetime (seconds). |
| `LAUNCH_TOKEN_TTL` | `600` | Launch-token lifetime (seconds). |
| `INITDATA_MAX_AGE` | `86400` | Max accepted `initData` age (seconds). |
| `SESSION_COOKIE_NAME` | `school_web_session` | Cookie name. |
| `WEB_ALLOWED_ORIGINS` | `http://localhost:5173` | CORS allow-list (credentialed). |
| `AUTH_RATE_LIMIT` | `20` | Max `/auth/telegram` attempts per IP per window (`0` disables). |
| `AUTH_RATE_WINDOW` | `60` | Rate-limit window in seconds. |

`BOT_TOKEN` is validated **lazily** (`config.require_bot_token()`) only when the
bot or web-auth actually needs it — importing the models/API no longer requires
it.

> **SQLite limitation (stage 1).** The bot and the API share one SQLite file.
> This is intended for a **single host under light load**. Concurrent writers
> across processes rely on SQLite's file locking; horizontal scaling requires the
> PostgreSQL migration planned for stage 2. The session factory in
> `database/db.py` is deliberately structured so that swap is an infrastructure
> change, not an API rewrite.

---

## 6. Running locally

**Backend (API):**

```bash
pip install -r requirements-dev.txt        # prod + web + test tooling
$env:BOT_TOKEN = "…"                        # PowerShell (or export on *nix)
uvicorn web_api.main:app --reload --port 8000
```

**Frontend:**

```bash
cd web
npm install
npm run dev          # http://localhost:5173, proxies /api → :8000
```

**Everything in Docker (dev):**

```bash
docker compose -f docker-compose.dev.yml up --build
# open http://localhost:5173
```

**Checks:**

```bash
# backend
pytest -q && ruff check . && mypy .
# frontend
cd web && npm run typecheck && npm run lint && npm run test && npm run build
```

---

## 7. Roadmap

**Done since stage 1:** writes for homework / extra / schedule / per-date changes,
chat profiles (`personal` / `class` / `tutor`), app roles with an explicit access
mode, invitation links, lesson payments, and a single permission core
(`services/permissions.capabilities`) shared by the bot and the API.

**Still open**
- Migrate from SQLite to **PostgreSQL** (async driver), keeping the API contract.
- **Optimistic locking** on mutations: two people can currently overwrite each
  other's edit silently.
- **Per-student homework completion** — needs a decision on what a chat-wide
  reminder means once "done" is per person.
- **S3-compatible** storage for homework attachments (still references only).
- A dedicated **worker** and durable queue for reminders, off the request path.
- **Telegram OIDC** login outside the Mini App, **PWA** packaging, and
  **metrics + tracing** (OpenTelemetry) across bot, API and worker.
- Shared (not process-local) store for the auth rate limiter.

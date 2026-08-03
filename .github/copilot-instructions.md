# Copilot instructions for telegram-school-bot

Telegram bot for a school class (Python 3.14 + aiogram 3, async). A class configures
its lesson schedule and bell times once; the bot then reminds about homework and
upcoming lessons in the chat.

## Commands

Dependencies, tool config and the Python version all live in `pyproject.toml`
(+ `uv.lock`, `.python-version`). Install everything: `uv sync --all-extras --dev`.
The `dev` group is test/lint tooling and must never reach the Docker image; the
`web` extra is the Mini App API's FastAPI stack.

- Full test suite: `uv run pytest` (`[tool.pytest.ini_options]`: `asyncio_mode=auto`, `testpaths=tests`)
- Single test file: `uv run pytest tests/test_chat_timezone.py`
- Single test by name: `uv run pytest tests/test_db_flow.py -k reonboarding -v`
- Coverage: `uv run pytest --cov=. --cov-report=term-missing`
- Lint: `uv run ruff check .`
- Type-check: `uv run mypy .` (see `[tool.mypy]` — several SQLAlchemy/aiogram error codes
  are intentionally disabled to cut framework false positives; do not re-enable them casually)
- Dependency audit: `uv audit --frozen --no-dev`

CI (`.github/workflows/ci.yml`) runs ruff + mypy + pytest (coverage) + `uv audit` on every
PR. Keep all four green.

## Architecture (big picture)

Entry point `bot.py` wires everything: runs Alembic migrations (`database/migrate.py`,
**not** `create_all`), builds the Bot/Dispatcher, registers middleware, then routers, then
the APScheduler reminder scheduler. Router registration order matters (onboarding state
handlers vs. common fallback commands).

- `handlers/` — one router per feature (onboarding, today, schedule, date_overrides,
  homework, extra, settings, history, migration, data_backup, common). Each is
  `include_router`'d in `bot.py`.
- `middleware/access.py` — `ChatContextMiddleware` (chat context), `OnboardingGuardMiddleware`
  (blocks feature routers until onboarding completes, incl. stale inline keyboards),
  and admin-permission checks.
- `database/` — `models.py` (SQLAlchemy 2 legacy `Column(...)` style + constraints/indexes +
  `AuthorshipMixin` + `AuditLog`), `db.py` (async CRUD, atomic onboarding, outbox jobs),
  `fsm_storage.py` (persistent FSM storage on SQLite), `migrate.py`.
- `services/` — cross-cutting logic that spans handlers: `timeservice.py` (per-chat timezone,
  DST, quiet hours — the single source of user-facing time/date), `effective_schedule.py`
  (weekly template + date overrides + A/B weeks → final schedule), `scheduler.py` (5 reminder
  categories via APScheduler + idempotent outbox), `permissions.py` (server-side homework edit
  policy), `audit.py` (authorship + safe short audit entries), `attachments.py` (Telegram
  file-reference validation only).
- `alembic/` — versioned schema migrations. `tests/` — pytest + pytest-asyncio.

**Stack:** aiogram 3, SQLAlchemy 2 + aiosqlite (async), Alembic, APScheduler, pytz,
python-dotenv. **DB: SQLite only** — `config.py` rejects any non-`sqlite` `DATABASE_URL`.

## Project invariants (do not violate)

These are load-bearing conventions unique to this codebase:

1. **No user-facing date/time via a global timezone.** `config.TIMEZONE` is used *only* inside
   `services/timeservice.py` (default for new chats + fallback). In handlers use
   `await ts.today_for_chat_id(chat_id)` / `await ts.tz_for_chat_id(chat_id)`; in the scheduler
   use `ChatClock`. Never call `pytz.timezone(...)` directly in handlers — use `ts.tz_from_name`.
   Service timestamps (outbox, audit, authorship) are always UTC ISO.
2. **Permission checks run server-side, before touching the DB.** A hidden button protects
   nothing. Admin actions → `middleware.access.require_admin`; homework edits → `_guard(...)`
   in `handlers/homework.py` → `services.permissions.require_homework_access`, re-checked again
   at write time (not just when opening a menu).
3. **Every DB query is scoped by `chat_id`.** Attachments scope via a JOIN on `homework`.
4. **Data backwards-compatibility.** New columns are nullable or have a server-default; rows
   with `NULL` authorship are a supported state (don't back-fill an author on edit). Defaults are
   chosen so existing chats' behavior is unchanged.
5. **No excess personal data.** Audit/authorship stores only Telegram id + display name — never
   tokens, username, phone, full Update, or homework text. Audit descriptions go through
   `audit.summarize(...)` and are truncated.
6. **Files are never downloaded, unpacked, or executed.** Store only `file_id`/`file_unique_id`
   + metadata; filenames are untrusted, sanitized via `utils.safe_file_name`, display-only.
7. **HTML-escape all user text** (`utils.html_escape`) under `parse_mode="HTML"`, including
   author names and captions.
8. **Audit must never break the action.** `audit.record` swallows/logs its own errors; nightly
   housekeeping must not crash a scheduler tick.
9. **Reminders are idempotent** via outbox `(chat_id, kind, job_date)` + `last_*_reminder_date`
   stamps. Dedup is by calendar date (this is why a repeated DST hour doesn't double-fire) — do
   not switch to minute comparison.
10. **Every schema change = a new Alembic migration** (+ an `_ensure_column` entry in
    `database.db.init_db` for dev/test DBs only). Afterwards verify `alembic revision --autogenerate`
    produces an empty diff.

## Style

- Comments and docstrings in **English**; user-facing strings in **Russian**.
- Comments explain *why*, not *what*.
- When a feature changes, update `README.md` and the `/help` text in `handlers/common.py` in the
  same change.

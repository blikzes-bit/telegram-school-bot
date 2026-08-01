"""add web app models (Mini App auth + membership)

Adds the tables backing the Telegram Mini App (see web_api/):

  * ``web_users``          — a Telegram user who has authenticated to the app;
  * ``chat_memberships``   — which chats a user may see and with what role;
  * ``web_launch_tokens``  — single-use, short-lived Mini App launch tokens;
  * ``web_sessions``       — opaque cookie-backed web sessions.

and a nullable ``chats.title`` (human-readable class name for the picker).

All new timestamps are ISO-8601 UTC strings (same convention as authorship /
outbox rows). Every table carries the FK/constraints/indexes declared on the
ORM model, so ``alembic revision --autogenerate`` produces an empty diff.

Revision ID: e5c1a2f3d4b6
Revises: d1a6c85b3e97
Create Date: 2026-07-31 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'e5c1a2f3d4b6'
down_revision: Union[str, Sequence[str], None] = 'd1a6c85b3e97'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("chats", sa.Column("title", sa.String(), nullable=True))

    op.create_table(
        "web_users",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("telegram_user_id", sa.BigInteger(), nullable=False),
        sa.Column("display_name", sa.String(), nullable=True),
        sa.Column("created_at", sa.String(), nullable=False),
        sa.Column("updated_at", sa.String(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("telegram_user_id", name="uq_web_users_tg_id"),
    )

    op.create_table(
        "chat_memberships",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("chat_id", sa.BigInteger(), nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("role", sa.String(), nullable=False),
        sa.Column("last_verified_at", sa.String(), nullable=False),
        sa.Column("created_at", sa.String(), nullable=False),
        sa.ForeignKeyConstraint(["chat_id"], ["chats.chat_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("chat_id", "user_id", name="uq_chat_memberships_chat_user"),
        sa.CheckConstraint("role IN ('member', 'admin')", name="ck_chat_memberships_role"),
    )
    op.create_index("ix_chat_memberships_user", "chat_memberships", ["user_id"])
    op.create_index("ix_chat_memberships_chat", "chat_memberships", ["chat_id"])

    op.create_table(
        "web_launch_tokens",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("token_hash", sa.String(), nullable=False),
        sa.Column("telegram_user_id", sa.BigInteger(), nullable=False),
        sa.Column("chat_id", sa.BigInteger(), nullable=False),
        sa.Column("created_at", sa.String(), nullable=False),
        sa.Column("expires_at", sa.String(), nullable=False),
        sa.Column("used_at", sa.String(), nullable=True),
        sa.ForeignKeyConstraint(["chat_id"], ["chats.chat_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("token_hash", name="uq_web_launch_tokens_hash"),
    )
    op.create_index("ix_web_launch_tokens_hash", "web_launch_tokens", ["token_hash"])

    op.create_table(
        "web_sessions",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("session_hash", sa.String(), nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("created_at", sa.String(), nullable=False),
        sa.Column("expires_at", sa.String(), nullable=False),
        sa.Column("last_seen_at", sa.String(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("session_hash", name="uq_web_sessions_hash"),
    )
    op.create_index("ix_web_sessions_hash", "web_sessions", ["session_hash"])
    op.create_index("ix_web_sessions_user", "web_sessions", ["user_id"])


def downgrade() -> None:
    op.drop_index("ix_web_sessions_user", table_name="web_sessions")
    op.drop_index("ix_web_sessions_hash", table_name="web_sessions")
    op.drop_table("web_sessions")

    op.drop_index("ix_web_launch_tokens_hash", table_name="web_launch_tokens")
    op.drop_table("web_launch_tokens")

    op.drop_index("ix_chat_memberships_chat", table_name="chat_memberships")
    op.drop_index("ix_chat_memberships_user", table_name="chat_memberships")
    op.drop_table("chat_memberships")

    op.drop_table("web_users")

    with op.batch_alter_table("chats", recreate="always") as batch_op:
        batch_op.drop_column("title")

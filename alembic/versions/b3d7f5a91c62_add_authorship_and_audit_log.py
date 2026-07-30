"""add record authorship, the audit log and the homework-edit policy

Adds the "who did this, and when" layer:

  * authorship columns on ``homework``, ``extra_activities``, ``day_overrides``
    and ``lesson_overrides``: ``created_by_user_id`` / ``created_by_name`` /
    ``updated_by_user_id`` / ``updated_by_name`` / ``created_at`` /
    ``updated_at``. All nullable — every pre-existing row keeps NULL, which the
    app treats as a supported "unknown author" state (see
    services/permissions.py), so nothing breaks and no data is invented.
  * ``chats.hw_edit_policy`` — who may edit homework:
    ``collaborative`` (the default, and exactly how every existing chat already
    behaved) | ``creator_or_admin`` | ``admin_only``.
  * a new ``audit_log`` table: chat, actor id + display name, entity type/id,
    action (create/update/delete/complete/restore), a short safe summary and an
    ISO-8601 UTC timestamp. Only ``chat_id`` has a FK (ON DELETE CASCADE) —
    ``entity_id`` deliberately has none so an entry outlives the record it
    describes (a deleted homework leaves its audit trail behind).

Indexes cover the two read patterns of the "📜 История" screen (newest-first per
chat, optionally filtered by entity type) plus ``created_at`` for the nightly
retention pruning, and (chat_id, created_by_user_id) on ``homework`` for the
``creator_or_admin`` policy check.

Revision ID: b3d7f5a91c62
Revises: a2c4e6f80b13
Create Date: 2026-07-27 20:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'b3d7f5a91c62'
down_revision: Union[str, Sequence[str], None] = 'a2c4e6f80b13'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# Tables that gain authorship columns.
_AUTHORED_TABLES = ("homework", "extra_activities", "day_overrides", "lesson_overrides")

_AUTHORSHIP_COLUMNS = (
    ("created_by_user_id", sa.BigInteger()),
    ("created_by_name", sa.String()),
    ("updated_by_user_id", sa.BigInteger()),
    ("updated_by_name", sa.String()),
    ("created_at", sa.String()),
    ("updated_at", sa.String()),
)


def upgrade() -> None:
    for table in _AUTHORED_TABLES:
        with op.batch_alter_table(table, recreate="always") as batch_op:
            for name, type_ in _AUTHORSHIP_COLUMNS:
                batch_op.add_column(sa.Column(name, type_, nullable=True))

    op.create_index(
        "ix_homework_chat_creator", "homework", ["chat_id", "created_by_user_id"]
    )

    with op.batch_alter_table("chats", recreate="always") as batch_op:
        batch_op.add_column(sa.Column(
            "hw_edit_policy", sa.String(), nullable=False, server_default="collaborative"
        ))

    op.create_table(
        "audit_log",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("chat_id", sa.BigInteger(), nullable=False),
        sa.Column("actor_user_id", sa.BigInteger(), nullable=True),
        sa.Column("actor_name", sa.String(), nullable=True),
        sa.Column("entity_type", sa.String(), nullable=False),
        sa.Column("entity_id", sa.Integer(), nullable=True),
        sa.Column("action", sa.String(), nullable=False),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("created_at", sa.String(), nullable=False),
        sa.ForeignKeyConstraint(["chat_id"], ["chats.chat_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint(
            "action IN ('create', 'update', 'delete', 'complete', 'restore')",
            name="ck_audit_log_action",
        ),
    )
    op.create_index("ix_audit_log_chat_id_desc", "audit_log", ["chat_id", "id"])
    op.create_index("ix_audit_log_chat_entity", "audit_log", ["chat_id", "entity_type", "id"])
    op.create_index("ix_audit_log_created_at", "audit_log", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_audit_log_created_at", table_name="audit_log")
    op.drop_index("ix_audit_log_chat_entity", table_name="audit_log")
    op.drop_index("ix_audit_log_chat_id_desc", table_name="audit_log")
    op.drop_table("audit_log")

    with op.batch_alter_table("chats", recreate="always") as batch_op:
        batch_op.drop_column("hw_edit_policy")

    op.drop_index("ix_homework_chat_creator", table_name="homework")

    for table in _AUTHORED_TABLES:
        with op.batch_alter_table(table, recreate="always") as batch_op:
            for name, _ in reversed(_AUTHORSHIP_COLUMNS):
                batch_op.drop_column(name)

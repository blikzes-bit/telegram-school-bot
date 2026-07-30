"""add homework attachments (Telegram file references)

Adds ``homework_attachments``: photos and documents attached to a homework
entry, stored as *references only* — Telegram's ``file_id`` /
``file_unique_id`` plus kind, sanitised file name, size and an optional
caption. No binary is ever downloaded or kept by the bot.

  * FK ``homework_id`` → ``homework.id`` with ON DELETE CASCADE, so deleting a
    homework entry takes its attachments with it and no orphans can remain;
  * unique ``(homework_id, file_unique_id)`` so the same file can't be attached
    twice to the same entry (this is also the race backstop when two identical
    sends arrive at once);
  * check constraints pinning ``file_type`` to photo/document and forbidding a
    negative size.

Existing homework rows simply have no attachments — nothing to backfill, and
every screen already handles the empty case.

Revision ID: c9e2b41f7a83
Revises: b3d7f5a91c62
Create Date: 2026-07-27 21:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'c9e2b41f7a83'
down_revision: Union[str, Sequence[str], None] = 'b3d7f5a91c62'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "homework_attachments",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("homework_id", sa.Integer(), nullable=False),
        sa.Column("file_id", sa.String(), nullable=False),
        sa.Column("file_unique_id", sa.String(), nullable=False),
        sa.Column("file_type", sa.String(), nullable=False),
        sa.Column("file_name", sa.String(), nullable=True),
        sa.Column("file_size", sa.Integer(), nullable=True),
        sa.Column("caption", sa.Text(), nullable=True),
        sa.Column("created_at", sa.String(), nullable=True),
        sa.Column("created_by_user_id", sa.BigInteger(), nullable=True),
        sa.Column("created_by_name", sa.String(), nullable=True),
        sa.ForeignKeyConstraint(["homework_id"], ["homework.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "homework_id", "file_unique_id",
            name="uq_homework_attachments_homework_file",
        ),
        sa.CheckConstraint(
            "file_type IN ('photo', 'document')",
            name="ck_homework_attachments_file_type",
        ),
        sa.CheckConstraint(
            "file_size IS NULL OR file_size >= 0",
            name="ck_homework_attachments_file_size",
        ),
    )
    op.create_index(
        "ix_homework_attachments_homework", "homework_attachments", ["homework_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_homework_attachments_homework", table_name="homework_attachments")
    op.drop_table("homework_attachments")

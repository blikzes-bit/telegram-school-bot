"""add per-student homework completion

Adds ``homework_completions`` (one row per homework + person) and the opt-in
switch ``chats.per_student_homework``.

This is a **layer on top of** ``homework.is_completed``, not a replacement:
that column keeps its meaning (the class-level "this task is closed" flag) and
remains the only thing chat-wide messages and reminders consult, because a group
message is seen by everybody and cannot be personal. The new rows only change
what a given person sees in the Mini App, plus the "how many are done" count the
teacher sees.

The switch is nullable with no backfill: ``NULL`` means "one shared mark", which
is exactly how every existing chat already behaves, so nothing changes for anyone
until a chat opts in.

Revision ID: c8f1b40d2e73
Revises: b6d3e8a72f14
Create Date: 2026-08-08 02:20:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'c8f1b40d2e73'
down_revision: Union[str, Sequence[str], None] = 'b6d3e8a72f14'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "homework_completions",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("homework_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("completed_at", sa.String(), nullable=False),
        sa.ForeignKeyConstraint(["homework_id"], ["homework.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "homework_id", "user_id", name="uq_homework_completions_hw_user"
        ),
    )
    op.create_index(
        "ix_homework_completions_hw", "homework_completions", ["homework_id"]
    )

    with op.batch_alter_table("chats", recreate="always") as batch_op:
        batch_op.add_column(
            sa.Column("per_student_homework", sa.Boolean(), nullable=True)
        )


def downgrade() -> None:
    with op.batch_alter_table("chats", recreate="always") as batch_op:
        batch_op.drop_column("per_student_homework")

    op.drop_index("ix_homework_completions_hw", table_name="homework_completions")
    op.drop_table("homework_completions")

"""add date overrides (day_overrides, lesson_overrides)

Adds the two tables that let a specific calendar date deviate from the weekly
schedule template without ever mutating it:

  * ``day_overrides``   — whole-day setting (free / holiday / vacation / remote)
    with an optional reason;
  * ``lesson_overrides`` — per-lesson change on a date (cancel / replace subject
    / change time / add a one-off lesson).

Both carry the recurrence/validity CHECK constraints, a per-chat+date index and
an ON DELETE CASCADE foreign key. Existing data is untouched.

Revision ID: d5a1c9f42b7e
Revises: c7f3a9b2d1e4
Create Date: 2026-07-27 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'd5a1c9f42b7e'
down_revision: Union[str, Sequence[str], None] = 'c7f3a9b2d1e4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "day_overrides",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("chat_id", sa.BigInteger(), sa.ForeignKey("chats.chat_id", ondelete="CASCADE"), nullable=False),
        sa.Column("date", sa.Date(), nullable=False),
        sa.Column("day_type", sa.String(), nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
        sa.CheckConstraint("day_type IN ('free', 'holiday', 'vacation', 'remote')", name="ck_day_overrides_type"),
        sa.UniqueConstraint("chat_id", "date", name="uq_day_overrides_chat_date"),
    )
    op.create_index("ix_day_overrides_chat_date", "day_overrides", ["chat_id", "date"])

    op.create_table(
        "lesson_overrides",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("chat_id", sa.BigInteger(), sa.ForeignKey("chats.chat_id", ondelete="CASCADE"), nullable=False),
        sa.Column("date", sa.Date(), nullable=False),
        sa.Column("lesson_number", sa.Integer(), nullable=False),
        sa.Column("action", sa.String(), nullable=False),
        sa.Column("subject_name", sa.String(), nullable=True),
        sa.Column("start_time", sa.String(), nullable=True),
        sa.Column("end_time", sa.String(), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.CheckConstraint("action IN ('cancel', 'set')", name="ck_lesson_overrides_action"),
        sa.CheckConstraint("lesson_number > 0", name="ck_lesson_overrides_lesson_number_positive"),
        sa.UniqueConstraint("chat_id", "date", "lesson_number", name="uq_lesson_overrides_chat_date_lesson"),
    )
    op.create_index("ix_lesson_overrides_chat_date", "lesson_overrides", ["chat_id", "date"])


def downgrade() -> None:
    op.drop_index("ix_lesson_overrides_chat_date", table_name="lesson_overrides")
    op.drop_table("lesson_overrides")
    op.drop_index("ix_day_overrides_chat_date", table_name="day_overrides")
    op.drop_table("day_overrides")

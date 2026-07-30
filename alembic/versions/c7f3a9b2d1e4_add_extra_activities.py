"""add extra_activities table

Adds the ``extra_activities`` table for supplementary activities (clubs,
tutors, sections, extra classes) that are deliberately kept separate from the
regular school schedule (lesson_slots / schedule). Includes the recurrence
CHECK constraints, per-chat indexes and an ON DELETE CASCADE foreign key.

Revision ID: c7f3a9b2d1e4
Revises: 8f4eb80a9671
Create Date: 2026-07-26 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'c7f3a9b2d1e4'
down_revision: Union[str, Sequence[str], None] = '8f4eb80a9671'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "extra_activities",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("chat_id", sa.BigInteger(), sa.ForeignKey("chats.chat_id", ondelete="CASCADE"), nullable=False),
        sa.Column("title", sa.String(), nullable=False),
        sa.Column("kind", sa.String(), nullable=False),
        sa.Column("day_of_week", sa.Integer(), nullable=True),
        sa.Column("activity_date", sa.Date(), nullable=True),
        sa.Column("start_time", sa.String(), nullable=False),
        sa.Column("end_time", sa.String(), nullable=True),
        sa.Column("location", sa.String(), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.CheckConstraint("kind IN ('weekly', 'once')", name="ck_extra_activities_kind"),
        sa.CheckConstraint("day_of_week IS NULL OR (day_of_week BETWEEN 0 AND 6)", name="ck_extra_activities_day_range"),
        sa.CheckConstraint(
            "(kind = 'weekly' AND day_of_week IS NOT NULL AND activity_date IS NULL) "
            "OR (kind = 'once' AND activity_date IS NOT NULL AND day_of_week IS NULL)",
            name="ck_extra_activities_recurrence",
        ),
    )
    op.create_index("ix_extra_activities_chat_day", "extra_activities", ["chat_id", "day_of_week"])
    op.create_index("ix_extra_activities_chat_date", "extra_activities", ["chat_id", "activity_date"])


def downgrade() -> None:
    op.drop_index("ix_extra_activities_chat_date", table_name="extra_activities")
    op.drop_index("ix_extra_activities_chat_day", table_name="extra_activities")
    op.drop_table("extra_activities")

"""add alternating (A/B) weeks

Adds support for alternating "even/odd" (A/B) week schedules:

  * ``chats.week_mode``          — whether the chat uses alternating weeks;
  * ``chats.week_anchor_monday`` — the Monday that starts week A;
  * ``schedule.week_type``       — 'all' (single template, the default and how
    every existing row behaves) | 'A' | 'B'.

The ``schedule`` unique constraint is widened to include ``week_type`` so the
same (day, lesson) can hold different subjects on week A vs week B. Existing
rows are backfilled to ``week_type = 'all'`` and are otherwise untouched.

Revision ID: f1b8e3c26a9d
Revises: d5a1c9f42b7e
Create Date: 2026-07-27 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'f1b8e3c26a9d'
down_revision: Union[str, Sequence[str], None] = 'd5a1c9f42b7e'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("chats", recreate="always") as batch_op:
        batch_op.add_column(sa.Column("week_mode", sa.Boolean(), nullable=False, server_default=sa.false()))
        batch_op.add_column(sa.Column("week_anchor_monday", sa.Date(), nullable=True))

    # Add week_type with a default so existing rows backfill to 'all', then
    # swap the unique constraint to include week_type. Done in one batch
    # recreate so SQLite (which can't ALTER constraints in place) copies the
    # data across intact.
    with op.batch_alter_table("schedule", recreate="always") as batch_op:
        batch_op.add_column(sa.Column("week_type", sa.String(), nullable=False, server_default="all"))
        batch_op.drop_constraint("uq_schedule_chat_day_lesson", type_="unique")
        batch_op.create_unique_constraint(
            "uq_schedule_chat_week_day_lesson",
            ["chat_id", "week_type", "day_of_week", "lesson_number"],
        )
        batch_op.create_check_constraint("ck_schedule_week_type", "week_type IN ('all', 'A', 'B')")


def downgrade() -> None:
    with op.batch_alter_table("schedule", recreate="always") as batch_op:
        batch_op.drop_constraint("ck_schedule_week_type", type_="check")
        batch_op.drop_constraint("uq_schedule_chat_week_day_lesson", type_="unique")
        batch_op.create_unique_constraint(
            "uq_schedule_chat_day_lesson",
            ["chat_id", "day_of_week", "lesson_number"],
        )
        batch_op.drop_column("week_type")

    with op.batch_alter_table("chats", recreate="always") as batch_op:
        batch_op.drop_column("week_anchor_monday")
        batch_op.drop_column("week_mode")

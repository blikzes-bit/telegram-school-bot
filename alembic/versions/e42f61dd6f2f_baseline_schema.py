"""baseline schema

Mirrors the schema produced by the pre-Alembic code (Base.metadata.create_all
plus the ad-hoc _ensure_column patches) so that existing production databases
can be `alembic stamp`-ed to this revision before upgrading further. A brand
new database goes through this revision on its way to head.

Revision ID: e42f61dd6f2f
Revises:
Create Date: 2026-07-19 23:54:19.849785

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'e42f61dd6f2f'
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "chats",
        sa.Column("chat_id", sa.BigInteger(), primary_key=True),
        sa.Column("chat_type", sa.String(), nullable=False),
        sa.Column("hw_reminder_time", sa.String(), nullable=False, server_default="18:00"),
        sa.Column("schedule_reminder_time", sa.String(), nullable=False, server_default="20:00"),
        sa.Column("is_onboarded", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("last_hw_reminder_date", sa.Date(), nullable=True),
        sa.Column("last_sch_reminder_date", sa.Date(), nullable=True),
        sa.Column("hw_reminder_enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("schedule_reminder_enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
    )
    op.create_table(
        "lesson_slots",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("chat_id", sa.BigInteger(), sa.ForeignKey("chats.chat_id", ondelete="CASCADE"), nullable=False),
        sa.Column("lesson_number", sa.Integer(), nullable=False),
        sa.Column("start_time", sa.String(), nullable=False),
        sa.Column("end_time", sa.String(), nullable=False),
    )
    op.create_table(
        "schedule",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("chat_id", sa.BigInteger(), sa.ForeignKey("chats.chat_id", ondelete="CASCADE"), nullable=False),
        sa.Column("day_of_week", sa.Integer(), nullable=False),
        sa.Column("lesson_number", sa.Integer(), nullable=False),
        sa.Column("subject_name", sa.String(), nullable=False),
    )
    op.create_table(
        "homework",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("chat_id", sa.BigInteger(), sa.ForeignKey("chats.chat_id", ondelete="CASCADE"), nullable=False),
        sa.Column("subject_name", sa.String(), nullable=False),
        sa.Column("due_date", sa.Date(), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("is_completed", sa.Boolean(), nullable=False, server_default=sa.false()),
    )


def downgrade() -> None:
    op.drop_table("homework")
    op.drop_table("schedule")
    op.drop_table("lesson_slots")
    op.drop_table("chats")

"""add constraints, indexes, outbox and fsm tables

Adds the data-integrity constraints, the homework index, the is_blocked
column, and the two new tables (reminder_jobs outbox, fsm_state persistent
FSM storage) introduced by the security/reliability audit.

SQLite cannot ALTER a table to add a CHECK/UNIQUE constraint in place, so the
three existing tables are rebuilt via batch mode (Alembic copies data into a
new table with the desired schema and swaps it in).

Revision ID: 8f4eb80a9671
Revises: e42f61dd6f2f
Create Date: 2026-07-19 23:54:20.367328

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '8f4eb80a9671'
down_revision: Union[str, Sequence[str], None] = 'e42f61dd6f2f'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("chats", recreate="always") as batch_op:
        batch_op.add_column(sa.Column("is_blocked", sa.Boolean(), nullable=False, server_default=sa.false()))

    with op.batch_alter_table("lesson_slots", recreate="always") as batch_op:
        batch_op.create_unique_constraint("uq_lesson_slots_chat_lesson", ["chat_id", "lesson_number"])
        batch_op.create_check_constraint("ck_lesson_slots_lesson_number_positive", "lesson_number > 0")

    with op.batch_alter_table("schedule", recreate="always") as batch_op:
        batch_op.create_unique_constraint("uq_schedule_chat_day_lesson", ["chat_id", "day_of_week", "lesson_number"])
        batch_op.create_check_constraint("ck_schedule_day_of_week_range", "day_of_week BETWEEN 0 AND 6")
        batch_op.create_check_constraint("ck_schedule_lesson_number_positive", "lesson_number > 0")

    op.create_index("ix_homework_chat_completed_due", "homework", ["chat_id", "is_completed", "due_date"])

    op.create_table(
        "reminder_jobs",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("chat_id", sa.BigInteger(), sa.ForeignKey("chats.chat_id", ondelete="CASCADE"), nullable=False),
        sa.Column("kind", sa.String(), nullable=False),
        sa.Column("job_date", sa.Date(), nullable=False),
        sa.Column("chunks_json", sa.Text(), nullable=False),
        sa.Column("chunks_total", sa.Integer(), nullable=False),
        sa.Column("chunks_sent", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("status", sa.String(), nullable=False, server_default="pending"),
        sa.Column("updated_at", sa.String(), nullable=False),
        sa.UniqueConstraint("chat_id", "kind", "job_date", name="uq_reminder_job_chat_kind_date"),
    )

    op.create_table(
        "fsm_state",
        sa.Column("key", sa.String(), primary_key=True),
        sa.Column("state", sa.String(), nullable=True),
        sa.Column("data", sa.Text(), nullable=False, server_default="{}"),
    )


def downgrade() -> None:
    op.drop_table("fsm_state")
    op.drop_table("reminder_jobs")
    op.drop_index("ix_homework_chat_completed_due", table_name="homework")

    with op.batch_alter_table("schedule", recreate="always") as batch_op:
        batch_op.drop_constraint("ck_schedule_lesson_number_positive", type_="check")
        batch_op.drop_constraint("ck_schedule_day_of_week_range", type_="check")
        batch_op.drop_constraint("uq_schedule_chat_day_lesson", type_="unique")

    with op.batch_alter_table("lesson_slots", recreate="always") as batch_op:
        batch_op.drop_constraint("ck_lesson_slots_lesson_number_positive", type_="check")
        batch_op.drop_constraint("uq_lesson_slots_chat_lesson", type_="unique")

    with op.batch_alter_table("chats", recreate="always") as batch_op:
        batch_op.drop_column("is_blocked")

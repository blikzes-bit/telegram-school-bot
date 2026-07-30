"""add reminder categories, quiet hours and per-activity reminders

Adds the settings for the richer reminder system:

  * ``chats``:
      - ``hw_duetoday_enabled`` / ``hw_duetoday_time`` — morning "homework due
        today" reminder;
      - ``changes_reminder_enabled`` — next-day schedule changes heads-up;
      - ``extra_reminder_enabled`` — chat-wide master switch for extra-activity
        reminders;
      - ``last_duetoday_reminder_date`` / ``last_changes_reminder_date`` — the
        per-category "already sent today" stamps;
      - ``quiet_start`` / ``quiet_end`` — quiet hours (HH:MM, may wrap midnight).
  * ``extra_activities``:
      - ``reminder_enabled`` / ``reminder_minutes`` (0..10080) — per-activity
        reminder config.

All existing rows keep working: new toggles default to enabled, quiet hours to
NULL (off), and per-activity reminders to off. Existing data is untouched.

Revision ID: a2c4e6f80b13
Revises: f1b8e3c26a9d
Create Date: 2026-07-27 18:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'a2c4e6f80b13'
down_revision: Union[str, Sequence[str], None] = 'f1b8e3c26a9d'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("chats", recreate="always") as batch_op:
        batch_op.add_column(sa.Column("hw_duetoday_enabled", sa.Boolean(), nullable=False, server_default=sa.true()))
        batch_op.add_column(sa.Column("hw_duetoday_time", sa.String(), nullable=False, server_default="07:30"))
        batch_op.add_column(sa.Column("changes_reminder_enabled", sa.Boolean(), nullable=False, server_default=sa.true()))
        batch_op.add_column(sa.Column("extra_reminder_enabled", sa.Boolean(), nullable=False, server_default=sa.true()))
        batch_op.add_column(sa.Column("last_duetoday_reminder_date", sa.Date(), nullable=True))
        batch_op.add_column(sa.Column("last_changes_reminder_date", sa.Date(), nullable=True))
        batch_op.add_column(sa.Column("quiet_start", sa.String(), nullable=True))
        batch_op.add_column(sa.Column("quiet_end", sa.String(), nullable=True))

    with op.batch_alter_table("extra_activities", recreate="always") as batch_op:
        batch_op.add_column(sa.Column("reminder_enabled", sa.Boolean(), nullable=False, server_default=sa.false()))
        batch_op.add_column(sa.Column("reminder_minutes", sa.Integer(), nullable=False, server_default="60"))
        batch_op.create_check_constraint(
            "ck_extra_activities_reminder_minutes",
            "reminder_minutes >= 0 AND reminder_minutes <= 10080",
        )


def downgrade() -> None:
    with op.batch_alter_table("extra_activities", recreate="always") as batch_op:
        batch_op.drop_constraint("ck_extra_activities_reminder_minutes", type_="check")
        batch_op.drop_column("reminder_minutes")
        batch_op.drop_column("reminder_enabled")

    with op.batch_alter_table("chats", recreate="always") as batch_op:
        batch_op.drop_column("quiet_end")
        batch_op.drop_column("quiet_start")
        batch_op.drop_column("last_changes_reminder_date")
        batch_op.drop_column("last_duetoday_reminder_date")
        batch_op.drop_column("extra_reminder_enabled")
        batch_op.drop_column("changes_reminder_enabled")
        batch_op.drop_column("hw_duetoday_time")
        batch_op.drop_column("hw_duetoday_enabled")

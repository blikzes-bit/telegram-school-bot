"""add lesson payments and their reminder category

The tutor profile's money side: a ``payments`` table (title, amount in **minor
units**, currency label, due date, paid flag, period, per-entry "remind N days
before") plus the chat-level switch and time for the reminder category.

Existing chats are unaffected in practice as well as in principle: the switch
defaults to on, but a chat with no payment rows never has anything to send.
Amounts are integers (kopecks/cents) — never floats — so money cannot pick up
binary rounding error.

Revision ID: b6d3e8a72f14
Revises: a4e9f1c37d20
Create Date: 2026-08-08 01:40:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'b6d3e8a72f14'
down_revision: Union[str, Sequence[str], None] = 'a4e9f1c37d20'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "payments",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("chat_id", sa.BigInteger(), nullable=False),
        sa.Column("title", sa.String(), nullable=False),
        sa.Column("amount_minor", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("currency", sa.String(), nullable=False, server_default="UAH"),
        sa.Column("due_date", sa.Date(), nullable=False),
        sa.Column("period", sa.String(), nullable=False, server_default="one_time"),
        sa.Column("is_paid", sa.Boolean(), nullable=False, server_default=sa.text("0")),
        sa.Column("paid_at", sa.String(), nullable=True),
        sa.Column("note", sa.String(), nullable=True),
        sa.Column("remind_days_before", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_by_user_id", sa.BigInteger(), nullable=True),
        sa.Column("created_by_name", sa.String(), nullable=True),
        sa.Column("updated_by_user_id", sa.BigInteger(), nullable=True),
        sa.Column("updated_by_name", sa.String(), nullable=True),
        sa.Column("created_at", sa.String(), nullable=True),
        sa.Column("updated_at", sa.String(), nullable=True),
        sa.CheckConstraint(
            "period IN ('one_time', 'monthly', 'per_lesson')", name="ck_payments_period"
        ),
        sa.CheckConstraint("amount_minor >= 0", name="ck_payments_amount_non_negative"),
        sa.CheckConstraint(
            "remind_days_before BETWEEN 0 AND 30", name="ck_payments_remind_days"
        ),
        sa.ForeignKeyConstraint(["chat_id"], ["chats.chat_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_payments_chat_due", "payments", ["chat_id", "due_date"])

    with op.batch_alter_table("chats", recreate="always") as batch_op:
        batch_op.add_column(sa.Column(
            "payment_reminder_enabled", sa.Boolean(), nullable=False,
            server_default=sa.text("1"),
        ))
        batch_op.add_column(sa.Column(
            "payment_reminder_time", sa.String(), nullable=False, server_default="10:00",
        ))
        batch_op.add_column(sa.Column("last_payment_reminder_date", sa.Date(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("chats", recreate="always") as batch_op:
        batch_op.drop_column("last_payment_reminder_date")
        batch_op.drop_column("payment_reminder_time")
        batch_op.drop_column("payment_reminder_enabled")

    op.drop_index("ix_payments_chat_due", table_name="payments")
    op.drop_table("payments")

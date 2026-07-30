"""add a per-chat timezone

Adds ``chats.timezone``: the IANA zone name (e.g. ``Europe/Kyiv``) used for every
user-visible date and time of that chat — "Сегодня", homework due dates,
even/odd week resolution, date overrides, extra activities and all reminders.

The column is NOT NULL with a server default of ``config.TIMEZONE``, so **every
existing chat is backfilled with the value it was already effectively running
on** and its behaviour does not change. The default is captured at migration
time from the environment; a chat can then diverge from it freely, and
``services.timeservice.chat_tz`` falls back to the process default if a stored
name ever stops being a zone pytz knows — one bad value must never stop the
scheduler for everyone else.

Revision ID: d1a6c85b3e97
Revises: c9e2b41f7a83
Create Date: 2026-07-28 09:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

from config import TIMEZONE


revision: str = 'd1a6c85b3e97'
down_revision: Union[str, Sequence[str], None] = 'c9e2b41f7a83'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("chats", recreate="always") as batch_op:
        batch_op.add_column(sa.Column(
            "timezone", sa.String(), nullable=False, server_default=TIMEZONE
        ))
    # Belt and braces: the server default covers rows copied by the batch
    # recreate, this covers any row that somehow arrived with an empty value.
    op.execute(
        sa.text("UPDATE chats SET timezone = :tz WHERE timezone IS NULL OR timezone = ''")
        .bindparams(tz=TIMEZONE)
    )


def downgrade() -> None:
    with op.batch_alter_table("chats", recreate="always") as batch_op:
        batch_op.drop_column("timezone")

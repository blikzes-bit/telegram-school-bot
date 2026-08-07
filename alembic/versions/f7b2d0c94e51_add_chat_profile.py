"""add a per-chat profile (personal / class / tutor)

Adds ``chats.profile``: what a chat is *for* — a single person's diary
(``personal``), a school class (``class``), or lessons with a tutor
(``tutor``, which has no school timetable at all).

The column is **nullable with no backfill and no server default**. ``NULL``
means "never asked" and is resolved at read time from ``chat_type``
(``services.profiles.resolve``): private → personal, group → class. That is
precisely how every existing chat already behaved, so this migration changes
no behaviour for anyone — a chat only diverges once someone picks a profile.

Deliberately no DB CHECK constraint, matching ``chats.hw_edit_policy``: the
allowed set lives in ``services.profiles`` and is enforced by
``database.db.set_chat_profile``, which refuses to write an unknown value.

Revision ID: f7b2d0c94e51
Revises: e5c1a2f3d4b6
Create Date: 2026-08-08 00:40:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'f7b2d0c94e51'
down_revision: Union[str, Sequence[str], None] = 'e5c1a2f3d4b6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("chats", recreate="always") as batch_op:
        batch_op.add_column(sa.Column("profile", sa.String(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("chats", recreate="always") as batch_op:
        batch_op.drop_column("profile")

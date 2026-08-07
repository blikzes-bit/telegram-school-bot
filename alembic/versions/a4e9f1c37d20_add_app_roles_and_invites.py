"""add app roles, an explicit access mode, a chat owner and invitations

Three additions, all backwards-compatible by construction:

  * ``chat_memberships.app_role`` — the member's role *inside the app*
    (``owner`` / ``editor`` / ``student`` / ``viewer``). A separate nullable
    column rather than a widening of ``role``, whose CHECK constraint and
    Telegram-derived meaning are left completely untouched. ``NULL`` = "no app
    role assigned".
  * ``chats.access_mode`` (``NULL``/``telegram`` | ``roles``) and
    ``chats.owner_user_id``. ``NULL`` access mode means "Telegram admin status
    decides", i.e. exactly how every existing chat already behaves, so this
    migration changes nobody's rights. A chat only switches to role-based
    rights when its owner explicitly turns them on.
  * a new ``chat_invites`` table: single-use, expiring invitations that grant a
    chosen role. Only the token **hash** is stored (same posture as
    ``web_launch_tokens`` / ``web_sessions``), so a database leak cannot
    reconstruct a usable invite link.

Like the other role/policy columns these carry no DB CHECK: the allowed sets
live in ``services.permissions`` and are enforced by the ``database.db``
setters, which refuse to write an unknown value.

Revision ID: a4e9f1c37d20
Revises: f7b2d0c94e51
Create Date: 2026-08-08 01:10:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'a4e9f1c37d20'
down_revision: Union[str, Sequence[str], None] = 'f7b2d0c94e51'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("chats", recreate="always") as batch_op:
        batch_op.add_column(sa.Column("access_mode", sa.String(), nullable=True))
        batch_op.add_column(sa.Column("owner_user_id", sa.BigInteger(), nullable=True))

    with op.batch_alter_table("chat_memberships", recreate="always") as batch_op:
        batch_op.add_column(sa.Column("app_role", sa.String(), nullable=True))

    op.create_table(
        "chat_invites",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("token_hash", sa.String(), nullable=False),
        sa.Column("chat_id", sa.BigInteger(), nullable=False),
        sa.Column("app_role", sa.String(), nullable=False),
        sa.Column("created_by_user_id", sa.BigInteger(), nullable=True),
        sa.Column("created_by_name", sa.String(), nullable=True),
        sa.Column("created_at", sa.String(), nullable=False),
        sa.Column("expires_at", sa.String(), nullable=False),
        sa.Column("used_at", sa.String(), nullable=True),
        sa.Column("used_by_user_id", sa.BigInteger(), nullable=True),
        sa.ForeignKeyConstraint(["chat_id"], ["chats.chat_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("token_hash", name="uq_chat_invites_hash"),
    )
    op.create_index("ix_chat_invites_hash", "chat_invites", ["token_hash"])
    op.create_index("ix_chat_invites_chat", "chat_invites", ["chat_id"])


def downgrade() -> None:
    op.drop_index("ix_chat_invites_chat", table_name="chat_invites")
    op.drop_index("ix_chat_invites_hash", table_name="chat_invites")
    op.drop_table("chat_invites")

    with op.batch_alter_table("chat_memberships", recreate="always") as batch_op:
        batch_op.drop_column("app_role")

    with op.batch_alter_table("chats", recreate="always") as batch_op:
        batch_op.drop_column("owner_user_id")
        batch_op.drop_column("access_mode")

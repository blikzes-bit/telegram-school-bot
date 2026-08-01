"""Repository protocols for the application layer.

Stage 1 uses the concrete async helpers in ``database.db`` directly. These
``Protocol`` definitions document the read surface the query use-cases rely on
so that a later stage (PostgreSQL, a Unit-of-Work, test fakes) can supply an
alternative implementation without touching ``application/queries.py``. They are
intentionally read-only for this stage.
"""
from typing import Dict, List, Optional, Protocol

from database.models import (
    Chat, ChatMembership, ExtraActivity, Homework, WebSession, WebUser,
)


class SchoolReadRepository(Protocol):
    """Read access to a single tenant's (chat's) school data, scoped by chat_id."""

    async def get_chat(self, chat_id: int) -> Optional[Chat]: ...

    async def get_homework(
        self, chat_id: int, is_completed: Optional[bool] = None
    ) -> List[Homework]: ...

    async def get_extra_activities(self, chat_id: int) -> List[ExtraActivity]: ...


class MembershipRepository(Protocol):
    """Read access to web users and their verified class memberships."""

    async def get_membership(
        self, chat_id: int, user_id: int
    ) -> Optional[ChatMembership]: ...

    async def get_memberships_for_user(self, user_id: int) -> List[ChatMembership]: ...

    async def get_chats_by_ids(self, chat_ids: List[int]) -> Dict[int, Chat]: ...


class SessionRepository(Protocol):
    """Read/write access to opaque web sessions (hash-only)."""

    async def get_web_session(self, session_hash: str) -> Optional[WebSession]: ...

    async def create_web_session(
        self, session_hash: str, user_id: int, now_iso: str, expires_iso: str
    ) -> WebSession: ...

    async def delete_web_session(self, session_hash: str) -> None: ...

    async def upsert_web_user(
        self, telegram_user_id: int, display_name: Optional[str], now_iso: str
    ) -> WebUser: ...

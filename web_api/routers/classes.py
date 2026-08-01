"""Current user (/me) and the class picker (/classes)."""
from typing import List

from fastapi import APIRouter, Depends

import database.db as db
from application.dto import ClassDTO, MeDTO
from web_api.deps import get_current_user
from database.models import WebUser

router = APIRouter(prefix="/api/v1", tags=["classes"])


@router.get("/me", response_model=MeDTO)
async def me(user: WebUser = Depends(get_current_user)) -> MeDTO:
    return MeDTO(telegram_user_id=user.telegram_user_id, display_name=user.display_name)


@router.get("/classes", response_model=List[ClassDTO])
async def list_classes(user: WebUser = Depends(get_current_user)) -> List[ClassDTO]:
    """Every class the user has a verified membership in."""
    memberships = await db.get_memberships_for_user(user.telegram_user_id)
    chats = await db.get_chats_by_ids([m.chat_id for m in memberships])
    result: List[ClassDTO] = []
    for m in memberships:
        chat = chats.get(m.chat_id)
        result.append(
            ClassDTO(
                chat_id=m.chat_id,
                title=getattr(chat, "title", None),
                role=m.role,
                timezone=getattr(chat, "timezone", None) or "",
            )
        )
    return result

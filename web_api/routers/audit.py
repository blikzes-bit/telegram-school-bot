"""Audit journal (📜 История): admin-only in a group/supergroup, unrestricted
in a private chat — mirrors handlers/history.py exactly."""
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status

from application.dto import AuditPageDTO
from application.queries import list_audit_log
from services.audit import ENTITY_TYPES
from web_api.deps import ClassContext, require_class

router = APIRouter(prefix="/api/v1/classes", tags=["audit"])

_MAX_PAGE_SIZE = 50


@router.get("/{chat_id}/audit", response_model=AuditPageDTO)
async def audit_log(
    chat_id: int,
    entity_type: Optional[str] = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=_MAX_PAGE_SIZE),
    ctx: ClassContext = Depends(require_class),
) -> AuditPageDTO:
    if not ctx.permissions.is_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="only chat admins may view the audit log here",
        )
    if entity_type is not None and entity_type not in ENTITY_TYPES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"entity_type must be one of: {', '.join(ENTITY_TYPES)}",
        )
    return await list_audit_log(chat_id, entity_type, page, page_size)

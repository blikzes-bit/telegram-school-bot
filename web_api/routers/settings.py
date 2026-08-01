"""Reminder settings: view (any member) and edit (admin-only in a group,
unrestricted in a private chat — mirrors handlers/settings.py exactly)."""
from fastapi import APIRouter, Depends, HTTPException, status

from application.dto import ReminderSettingsDTO, ReminderSettingsUpdateDTO
from application.queries import (
    SettingsAccessError, get_reminder_settings, update_reminder_settings,
)
from database.models import WebUser
from web_api.deps import ClassContext, get_current_user, require_class

router = APIRouter(prefix="/api/v1/classes", tags=["settings"])


@router.get("/{chat_id}/settings/reminders", response_model=ReminderSettingsDTO)
async def reminder_settings(
    chat_id: int, ctx: ClassContext = Depends(require_class)
) -> ReminderSettingsDTO:
    return await get_reminder_settings(chat_id, ctx.permissions.is_admin)


@router.patch("/{chat_id}/settings/reminders", response_model=ReminderSettingsDTO)
async def update_reminder_settings_endpoint(
    chat_id: int,
    payload: ReminderSettingsUpdateDTO,
    ctx: ClassContext = Depends(require_class),
    user: WebUser = Depends(get_current_user),
) -> ReminderSettingsDTO:
    try:
        return await update_reminder_settings(
            chat_id, payload, ctx.permissions.is_admin,
            actor_user_id=user.telegram_user_id, actor_name=user.display_name,
        )
    except SettingsAccessError:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="only chat admins may change reminder settings here",
        )

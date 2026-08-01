"""Export: JSON backup, CSV schedule, ICS calendar, audit journal.

Mirrors handlers/data_backup.py exactly: every entry point is admin-only in a
group/supergroup (unrestricted in a private chat), because these files can
contain the whole class's data. Read-only — the web API never accepts an
uploaded backup; import stays a bot-only, FSM-confirmed action.
"""
from fastapi import APIRouter, Depends, HTTPException, Response, status

import services.backup as backup
import services.timeservice as ts
from web_api.deps import ClassContext, require_class

router = APIRouter(prefix="/api/v1/classes", tags=["export"])


def _require_admin(ctx: ClassContext) -> None:
    if not ctx.permissions.is_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="only chat admins may export data here",
        )


def _attachment(filename: str) -> dict:
    return {"Content-Disposition": f'attachment; filename="{filename}"'}


@router.get("/{chat_id}/export/backup.json")
async def export_backup(chat_id: int, ctx: ClassContext = Depends(require_class)) -> Response:
    _require_admin(ctx)
    payload = await backup.build_backup(chat_id)
    today = await ts.today_for_chat_id(chat_id)
    return Response(
        content=backup.dump_json(payload),
        media_type="application/json",
        headers=_attachment(backup.backup_file_name(chat_id, today)),
    )


@router.get("/{chat_id}/export/audit.json")
async def export_audit(chat_id: int, ctx: ClassContext = Depends(require_class)) -> Response:
    _require_admin(ctx)
    payload = await backup.build_audit_export(chat_id)
    today = await ts.today_for_chat_id(chat_id)
    return Response(
        content=backup.dump_json(payload),
        media_type="application/json",
        headers=_attachment(backup.backup_file_name(chat_id, today, kind="audit")),
    )


@router.get("/{chat_id}/export/schedule.csv")
async def export_schedule_csv(chat_id: int, ctx: ClassContext = Depends(require_class)) -> Response:
    _require_admin(ctx)
    content = await backup.schedule_csv(chat_id)
    return Response(
        content=content,
        media_type="text/csv",
        headers=_attachment(f"schedule_{abs(chat_id)}.csv"),
    )


@router.get("/{chat_id}/export/calendar.ics")
async def export_calendar_ics(chat_id: int, ctx: ClassContext = Depends(require_class)) -> Response:
    _require_admin(ctx)
    today = await ts.today_for_chat_id(chat_id)
    content = await backup.calendar_ics(chat_id, today)
    return Response(
        content=content,
        media_type="text/calendar",
        headers=_attachment(f"calendar_{abs(chat_id)}.ics"),
    )

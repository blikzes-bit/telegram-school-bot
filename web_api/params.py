"""Shared query-parameter parsing for the web API routers."""
import datetime
from typing import Optional

from fastapi import HTTPException, status


def parse_date_param(value: Optional[str], field: str) -> Optional[datetime.date]:
    """Parse a ``YYYY-MM-DD`` query parameter, or 400 on a malformed value."""
    if value is None or value == "":
        return None
    try:
        return datetime.date.fromisoformat(value)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"{field} must be an ISO date (YYYY-MM-DD)",
        )

"""Health check — the only unauthenticated data endpoint."""
from fastapi import APIRouter

import config
from application.dto import HealthDTO

router = APIRouter(prefix="/api/v1", tags=["health"])


@router.get("/health", response_model=HealthDTO)
async def health() -> HealthDTO:
    return HealthDTO(status="ok", app_version=config.APP_VERSION)

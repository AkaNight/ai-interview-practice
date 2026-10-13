from uuid import UUID

from fastapi import APIRouter, Query, Request

from app.services.usage_service import UsageSettings

router = APIRouter(prefix="/api/usage", tags=["usage"])


@router.get("")
def summary(request: Request, limit: int = Query(30, ge=1, le=100), offset: int = Query(0, ge=0), session_id: UUID | None = None):
    return request.app.state.usage.summary(limit, offset, str(session_id) if session_id else None)


@router.post("/settings")
def settings(payload: UsageSettings, request: Request):
    with request.app.state.config_lock:
        return request.app.state.usage.save(payload)

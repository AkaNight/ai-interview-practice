from fastapi import APIRouter, Query, Request

router = APIRouter(prefix="/api/preparations", tags=["preparations"])


@router.get("")
def list_preparations(request: Request, limit: int = Query(default=50, ge=1, le=100), offset: int = Query(default=0, ge=0)):
    with request.app.state.config_lock:
        service = request.app.state.interviews
        return service.preparations.list(demo=service.settings.ai_mode == "demo", limit=limit, offset=offset)


@router.get("/{identifier}")
def get(identifier: str, request: Request):
    return request.app.state.interviews.preparations.get(identifier)


@router.delete("/{identifier}")
def remove(identifier: str, request: Request):
    with request.app.state.config_lock:
        return request.app.state.interviews.preparations.remove(identifier)

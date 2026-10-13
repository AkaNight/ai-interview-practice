from fastapi import APIRouter, Request

from app.schemas import UpdateAPISettings

router = APIRouter(prefix="/api/settings", tags=["settings"])


@router.get("")
def get_settings(request: Request):
    with request.app.state.config_lock:
        return request.app.state.api_settings.public()


@router.post("")
def save_settings(payload: UpdateAPISettings, request: Request):
    return request.app.state.api_settings.save(payload)


@router.post("/test")
def test_settings(payload: UpdateAPISettings, request: Request):
    return request.app.state.api_settings.test(payload)

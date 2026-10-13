from fastapi import APIRouter, Query, Request

from app.schemas import ContinueTraining, CreateInterview, CreatePractice, SubmitAnswer

router = APIRouter(prefix="/api/interviews", tags=["interviews"])


@router.get("")
def history(request: Request, limit: int = Query(default=20, ge=1, le=100), offset: int = Query(default=0, ge=0)):
    return request.app.state.interviews.history(limit, offset)


@router.post("", status_code=201)
def create(payload: CreateInterview, request: Request):
    with request.app.state.config_lock:
        return request.app.state.interviews.create(payload)


@router.post("/{identifier}/practice", status_code=201)
def practice(identifier: str, payload: CreatePractice, request: Request):
    with request.app.state.config_lock:
        return request.app.state.interviews.create_practice(identifier, payload)


@router.post("/{identifier}/continue")
def continue_training(identifier: str, payload: ContinueTraining, request: Request):
    with request.app.state.config_lock:
        return request.app.state.interviews.continue_training(identifier, payload)


@router.post("/{identifier}/repeat")
def repeat_training(identifier: str, payload: ContinueTraining, request: Request):
    with request.app.state.config_lock:
        return request.app.state.interviews.continue_training(identifier, payload, repeat=True)


@router.get("/{identifier}")
def get(identifier: str, request: Request):
    return request.app.state.interviews.get(identifier)


@router.delete("/{identifier}")
def remove(identifier: str, request: Request):
    with request.app.state.config_lock:
        return request.app.state.interviews.remove(identifier)


@router.post("/{identifier}/report/upgrade")
def upgrade_report(identifier: str, request: Request):
    with request.app.state.config_lock:
        return request.app.state.interviews.upgrade_report(identifier)


@router.post("/{identifier}/prepare")
def prepare(identifier: str, request: Request):
    with request.app.state.config_lock:
        return request.app.state.interviews.prepare(identifier)


@router.post("/{identifier}/start")
def start(identifier: str, request: Request):
    with request.app.state.config_lock:
        return request.app.state.interviews.start(identifier)


@router.post("/{identifier}/turn")
def turn(identifier: str, payload: SubmitAnswer, request: Request):
    with request.app.state.config_lock:
        return request.app.state.interviews.submit(identifier, payload)


@router.post("/{identifier}/finish")
def finish(identifier: str, request: Request):
    with request.app.state.config_lock:
        return request.app.state.interviews.finish(identifier)


@router.get("/{identifier}/report")
def report(identifier: str, request: Request):
    return request.app.state.interviews.get_report(identifier)

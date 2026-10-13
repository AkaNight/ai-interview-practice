from pathlib import Path

from fastapi import APIRouter, File, Request, UploadFile

from app.models import Resume, new_id
from app.services.errors import ServiceError
from app.services.resume_service import inspect_resume

router = APIRouter(prefix="/api/resumes", tags=["resumes"])


@router.post("", status_code=201)
def upload(request: Request, file: UploadFile = File(...)):
    settings = request.app.state.settings
    # Use our own path; the user-supplied name is display metadata only.
    filename = (file.filename or "resume").replace("\\", "/").split("/")[-1]
    if len(filename) > 200:
        raise ServiceError("文件名过长，请缩短后上传。", 400)
    content = file.file.read(settings.max_upload_mb * 1024 * 1024 + 1)
    if len(content) > settings.max_upload_mb * 1024 * 1024:
        raise ServiceError(f"简历不得超过 {settings.max_upload_mb} MB。", 413)
    inspect_resume(filename, content)
    identifier = new_id()
    directory = settings.data_dir / "resumes"
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / (identifier + Path(filename).suffix.lower())
    path.write_bytes(content)
    try:
        with request.app.state.session_factory() as db:
            db.add(Resume(id=identifier, filename=filename, file_path=str(path)))
            db.commit()
    except Exception:
        path.unlink(missing_ok=True)
        raise
    return {"id": identifier, "filename": filename, "parsed": False}


@router.post("/{identifier}/parse")
def parse(identifier: str, request: Request):
    service = request.app.state.interviews
    with request.app.state.config_lock, service.locks["resume:" + identifier], request.app.state.session_factory() as db:
        resume = service.require(db, Resume, identifier)
        if resume.parsed_json is None:
            path = Path(resume.file_path)
            text = inspect_resume(resume.filename, path.read_bytes())
            resume.parsed_json = request.app.state.ai.parse_resume(path, text).model_dump()
            db.commit()
        return {"id": resume.id, "parsed_resume": resume.parsed_json}

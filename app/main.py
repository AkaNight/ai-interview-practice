from contextlib import asynccontextmanager
from threading import RLock
from uuid import UUID

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.exceptions import RequestValidationError

from app.api import audio, interviews, preparations, resumes, usage as usage_api, settings as settings_api
from app.config import ROOT, Settings
from app.version import VERSION
from app.database import create_database
from app.services.errors import ServiceError
from app.services.interview_service import InterviewService
from app.services.settings_service import SettingsService, build_ai
from app.services.voice_service import VoiceService, VoiceSettingsService
from app.services.usage_service import LABELS, UsageService, usage_context


def create_app(settings=None, ai=None):
    settings = settings or Settings.from_env()
    engine, session_factory = create_database(settings.data_dir)
    usage = UsageService(session_factory)
    ai = ai or build_ai(settings, usage)

    @asynccontextmanager
    async def lifespan(_):
        yield
        application.state.ai.close()
        application.state.voice.close()
        engine.dispose()

    application = FastAPI(title="AI 模拟面试 · 文字与语音", version=VERSION, lifespan=lifespan)
    application.state.settings = settings
    application.state.engine = engine
    application.state.session_factory = session_factory
    application.state.ai = ai
    application.state.usage = usage
    application.state.interviews = InterviewService(session_factory, ai, settings)
    application.state.config_lock = RLock()
    application.state.api_settings = SettingsService(application, settings)
    application.state.voice_settings = VoiceSettingsService(application, settings)
    application.state.voice = VoiceService(application.state.voice_settings.active, usage=usage)

    @application.exception_handler(ServiceError)
    async def service_error(_, exc):
        return JSONResponse(status_code=exc.status_code, content={"error": exc.message})

    @application.exception_handler(RequestValidationError)
    async def validation_error(_, exc):
        return JSONResponse(status_code=422, content={"error": "输入不合法，请检查设置、公司、岗位、题号和回答内容。"})

    @application.middleware("http")
    async def local_requests(request: Request, call_next):
        # Refuse cross-site browser writes to this unauthenticated local server.
        origin = request.headers.get("origin")
        if request.method not in {"GET", "HEAD", "OPTIONS"} and origin and origin != str(request.base_url).rstrip("/"):
            return JSONResponse(status_code=403, content={"error": "请从本地程序页面提交。"})
        parts = request.url.path.strip("/").split("/")
        context = {"operation": LABELS.get(parts[-1], "API 请求")}
        if len(parts) >= 3 and parts[:2] == ["api", "interviews"]:
            try:
                context["session_id"] = str(UUID(parts[2]))
            except ValueError:
                pass
        token = usage_context.set(context)
        try:
            response = await call_next(request)
        finally:
            usage_context.reset(token)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Cache-Control"] = "no-store"
        return response

    @application.get("/api/health")
    def health():
        active = application.state.settings
        return {"status": "ok", "version": VERSION, "phase": 3, "ai_mode": active.ai_mode,
                "ai_ready": active.ai_mode == "demo" or bool(active.api_key),
                "voice_ready": bool(application.state.voice_settings.active.api_key),
                "main_model": active.main_model, "max_upload_mb": active.max_upload_mb}

    @application.get("/")
    def index():
        return FileResponse(ROOT / "app" / "static" / "index.html")

    application.include_router(resumes.router)
    application.include_router(interviews.router)
    application.include_router(preparations.router)
    application.include_router(settings_api.router)
    application.include_router(audio.router)
    application.include_router(usage_api.router)
    application.mount("/static", StaticFiles(directory=ROOT / "app" / "static"), name="static")
    return application


app = create_app()

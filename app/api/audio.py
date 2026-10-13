import math
from pathlib import Path
from uuid import UUID

from fastapi import APIRouter, File, Form, Request, UploadFile
from fastapi.responses import FileResponse

from app.models import InterviewSession, new_id
from app.schemas import UpdateVoiceSettings
from app.services.errors import ServiceError
from app.services.voice_service import VoiceService
from app.services.usage_service import usage_context

router = APIRouter(tags=["voice"])
MIMES = {".webm": "audio/webm", ".wav": "audio/wav", ".mp3": "audio/mpeg", ".m4a": "audio/mp4", ".ogg": "audio/ogg", ".mp4": "audio/mp4"}


@router.get("/api/voice-settings")
def get_settings(request: Request):
    with request.app.state.config_lock:
        return request.app.state.voice_settings.public()


@router.post("/api/voice-settings")
def save_settings(payload: UpdateVoiceSettings, request: Request):
    return request.app.state.voice_settings.save(payload)


@router.post("/api/voice-settings/test")
def test_settings(payload: UpdateVoiceSettings, request: Request):
    with request.app.state.config_lock:
        settings = request.app.state.voice_settings.candidate(payload)
    service = VoiceService(settings, usage=request.app.state.usage)
    try:
        return service.test()
    finally:
        service.close()


def current_turn(request, db, identifier, turn_index):
    service = request.app.state.interviews
    session = service.require(db, InterviewSession, identifier)
    turns = service.turns(db, identifier)
    if session.status != "active" or not turns or turns[-1].turn_index != turn_index or turns[-1].answer is not None:
        raise ServiceError("当前题目已提交或面试已结束，不能录音或转写。", 409)
    return turns[-1]


def transcribe_saved(request, db, turn):
    voice = request.app.state.voice
    turn.transcription_provider = voice.settings.provider
    turn.transcription_model = voice.settings.model
    db.commit()
    token = usage_context.set({**usage_context.get(), "audio_seconds": turn.answer_duration})
    try:
        text = voice.transcribe(turn.audio_path, turn.audio_mime)
    finally:
        usage_context.reset(token)
    turn.answer_transcript_raw = text
    db.commit()
    return {"text": text, "duration": turn.answer_duration, "turn_index": turn.turn_index}


@router.post("/api/interviews/{identifier}/transcribe")
def transcribe(identifier: str, request: Request, turn_index: int = Form(...), duration: float = Form(...), file: UploadFile = File(...), recording_id: str | None = Form(None)):
    if recording_id:
        try:
            recording_id = str(UUID(recording_id))
        except ValueError as exc:
            raise ServiceError("录音标识无效，请重新录制。", 400) from exc
    if not math.isfinite(duration) or duration < 0.3 or duration > 1800:
        raise ServiceError("录音时长需在 0.3 秒至 30 分钟之间。", 400)
    extension = Path(file.filename or "").suffix.lower()
    if extension not in MIMES:
        raise ServiceError("请上传 WebM、WAV、MP3、M4A、MP4 或 OGG 音频。", 400)
    content = file.file.read(25 * 1024 * 1024 + 1)
    if len(content) > 25 * 1024 * 1024:
        raise ServiceError("每次录音不得超过 25 MB，请缩短回答。", 413)
    if len(content) < 128:
        raise ServiceError("录音为空或过短，请重新录制。", 400)
    signatures = {
        ".webm": content.startswith(b"\x1aE\xdf\xa3"),
        ".wav": content.startswith(b"RIFF") and content[8:12] == b"WAVE",
        ".ogg": content.startswith(b"OggS"),
        ".mp3": content.startswith(b"ID3") or (content[0] == 0xff and content[1] & 0xe0 == 0xe0),
        ".m4a": content[4:8] == b"ftyp", ".mp4": content[4:8] == b"ftyp",
    }
    if not signatures[extension]:
        raise ServiceError("音频内容与文件格式不符，请重新录制或上传有效音频。", 400)
    service = request.app.state.interviews
    with request.app.state.config_lock, service.locks[identifier], request.app.state.session_factory() as db:
        turn = current_turn(request, db, identifier, turn_index)
        if recording_id and turn.recording_id == recording_id:
            if turn.answer_transcript_raw is not None:
                return {"text": turn.answer_transcript_raw, "duration": turn.answer_duration, "turn_index": turn.turn_index}
            return transcribe_saved(request, db, turn)
        directory = request.app.state.settings.data_dir / "interviews" / identifier / "audio"
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / (f"turn_{turn_index:03d}_{new_id()}" + extension)
        path.write_bytes(content)
        turn.audio_path, turn.audio_mime = str(path), MIMES[extension]
        turn.answer_duration, turn.answer_transcript_raw = duration, None
        turn.recording_id = recording_id or new_id()
        db.commit()  # Preserve audio before the external API call.
        return transcribe_saved(request, db, turn)


@router.post("/api/interviews/{identifier}/transcribe/retry")
def retry(identifier: str, request: Request, turn_index: int = Form(...)):
    service = request.app.state.interviews
    with request.app.state.config_lock, service.locks[identifier], request.app.state.session_factory() as db:
        turn = current_turn(request, db, identifier, turn_index)
        if not turn.audio_path:
            raise ServiceError("本题尚无已保存录音。", 409)
        if turn.answer_transcript_raw is not None:
            return {"text": turn.answer_transcript_raw, "duration": turn.answer_duration, "turn_index": turn.turn_index}
        return transcribe_saved(request, db, turn)


@router.get("/api/interviews/{identifier}/audio/{turn_index}")
def get_audio(identifier: str, turn_index: int, request: Request):
    service = request.app.state.interviews
    with request.app.state.session_factory() as db:
        service.require(db, InterviewSession, identifier)
        turn = next((turn for turn in service.turns(db, identifier) if turn.turn_index == turn_index), None)
        if not turn or not turn.audio_path or not Path(turn.audio_path).is_file():
            raise ServiceError("找不到本题录音。", 404)
        return FileResponse(turn.audio_path, media_type=turn.audio_mime)

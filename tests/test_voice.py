import io
import json
import sqlite3
import wave
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import inspect

from app.database import create_database
from app.models import InterviewSession
from app.services.errors import ServiceError
from app.services.voice_service import VoiceService, VoiceSettings
from tests.helpers import start_session


def wav_bytes():
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as output:
        output.setnchannels(1); output.setsampwidth(2); output.setframerate(16000)
        output.writeframes(b"\0\0" * 16000)
    return buffer.getvalue()


class FakeVoice:
    settings = VoiceSettings(api_key="test-only")
    def __init__(self):
        self.calls = 0
        self.fail = False
    def transcribe(self, path, mime):
        self.calls += 1
        if self.fail:
            raise ServiceError("模拟转写失败")
        return "嗯，我主要负责 PCAP 数据分析，然后验证 Suricata 规则。"
    def close(self):
        pass


@pytest.fixture
def voice(client):
    client.app.state.voice.close()
    fake = FakeVoice()
    client.app.state.voice = fake
    return fake


def upload(client, identifier, recording_id=None, **overrides):
    data = {"turn_index": 1, "duration": 1.0, "recording_id": recording_id or str(uuid4())} | overrides
    return client.post(f"/api/interviews/{identifier}/transcribe", data=data, files={"file": ("answer.wav", wav_bytes(), "audio/wav")})


def test_voice_loop_raw_edited_and_audio_preserved(client, voice):
    identifier, _ = start_session(client)
    response = upload(client, identifier)
    assert response.status_code == 200
    raw = response.json()["text"]
    assert raw.startswith("嗯")
    session = client.get(f"/api/interviews/{identifier}").json()
    assert session["turns"][0]["answer"] is None  # Transcription alone never submits an answer.
    assert session["turns"][0]["transcript_raw"] == raw
    assert session["turns"][0]["answer_duration"] == 1.0
    audio = client.get(session["turns"][0]["audio_url"])
    assert audio.status_code == 200 and audio.content == wav_bytes()
    edited = "我负责 PCAP 数据分析和 Suricata 规则验证。"
    answered = client.post(f"/api/interviews/{identifier}/turn", json={"turn_index": 1, "answer": edited}).json()
    assert answered["turns"][0]["answer"] == edited
    assert answered["turns"][0]["transcript_raw"] == raw
    assert len(answered["turns"]) == 2


def test_failed_transcription_keeps_audio_and_retry_recovers(client, voice):
    identifier, _ = start_session(client)
    voice.fail = True
    assert upload(client, identifier).status_code == 502
    session = client.get(f"/api/interviews/{identifier}").json()
    assert session["audio_needs_transcription"]
    assert client.get(session["turns"][0]["audio_url"]).status_code == 200
    voice.fail = False
    result = client.post(f"/api/interviews/{identifier}/transcribe/retry", data={"turn_index": 1})
    assert result.status_code == 200
    assert not client.get(f"/api/interviews/{identifier}").json()["audio_needs_transcription"]


def test_same_recording_retry_does_not_duplicate_or_retranscribe(client, voice, settings):
    identifier, _ = start_session(client)
    recording_id = str(uuid4())
    first = upload(client, identifier, recording_id)
    second = upload(client, identifier, recording_id)
    assert second.json() == first.json() and voice.calls == 1
    assert len(list((settings.data_dir / "interviews" / identifier / "audio").iterdir())) == 1


def test_new_recording_replaces_failed_attempt(client, voice):
    identifier, _ = start_session(client)
    voice.fail = True
    assert upload(client, identifier).status_code == 502
    voice.fail = False
    assert upload(client, identifier).status_code == 200
    assert voice.calls == 2


@pytest.mark.parametrize("duration", [0, -1, "nan", "inf", 1801])
def test_invalid_duration_rejected_before_api_call(client, voice, duration):
    identifier, _ = start_session(client)
    assert upload(client, identifier, duration=duration).status_code == 400
    assert voice.calls == 0


def test_invalid_audio_and_size_rejected(client, voice):
    identifier, _ = start_session(client)
    path = f"/api/interviews/{identifier}/transcribe"
    for filename, content, status in [("answer.exe", b"x" * 256, 400), ("answer.wav", b"", 400), ("answer.wav", b"x" * 256, 400), ("answer.wav", b"x" * (25 * 1024 * 1024 + 1), 413)]:
        response = client.post(path, data={"turn_index": 1, "duration": 1}, files={"file": (filename, content)})
        assert response.status_code == status
    assert voice.calls == 0


def test_audio_for_answered_or_completed_turn_rejected(client, voice):
    identifier, _ = start_session(client)
    client.post(f"/api/interviews/{identifier}/turn", json={"turn_index": 1, "answer": "文字回答"})
    assert upload(client, identifier).status_code == 409
    client.post(f"/api/interviews/{identifier}/finish")
    assert upload(client, identifier, turn_index=2).status_code == 409


def test_voice_settings_no_key_echo_and_no_interview_config_change(client):
    before = client.get("/api/settings").json()
    response = client.post("/api/voice-settings", json={"provider": "siliconflow", "api_key": "private-voice-key"})
    assert response.status_code == 200 and "private-voice-key" not in response.text
    assert "private-voice-key" not in client.get("/api/voice-settings").text
    assert client.get("/api/settings").json() == before
    assert client.get("/api/health").json()["voice_ready"]
    assert client.post("/api/voice-settings", json={"clear_key": True}).json()["ready"] is False


def test_compatible_voice_does_not_reuse_siliconflow_key(client):
    client.post("/api/voice-settings", json={"api_key": "siliconflow-only"})
    result = client.post("/api/voice-settings", json={"provider": "compatible", "base_url": "https://voice.example/v1", "model": "asr"})
    assert not result.json()["ready"]


def test_siliconflow_wrong_host_rejected(client):
    response = client.post("/api/voice-settings", json={"provider": "siliconflow", "base_url": "https://wrong.example/v1", "api_key": "secret"})
    assert response.status_code == 400 and "secret" not in response.text


def test_real_client_contract_keeps_transcript_verbatim(tmp_path):
    requests = []
    raw = "嗯，我，我主要负责 PCAP，然后验证规则。"
    def handler(request):
        requests.append(request)
        return httpx.Response(200, json={"text": raw})
    service = VoiceService(VoiceSettings(api_key="fake"), httpx.Client(transport=httpx.MockTransport(handler)))
    path = tmp_path / "answer.wav"; path.write_bytes(wav_bytes())
    try:
        assert service.transcribe(path, "audio/wav") == raw
        assert requests[0].url.path == "/v1/audio/transcriptions"
        assert b"Qwen/Qwen3-ASR-1.7B" in requests[0].content
        assert requests[0].headers["Authorization"] == "Bearer fake"
    finally:
        service.close()


@pytest.mark.parametrize("status", [401, 403, 402, 429, 503])
def test_upstream_error_does_not_echo_secret(status):
    service = VoiceService(VoiceSettings(api_key="hidden"), httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(status, json={"error": "hidden"}))))
    try:
        with pytest.raises(ServiceError) as error:
            service.request("GET", "/models")
        assert "hidden" not in error.value.message
    finally:
        service.close()


def test_phase1_database_migration_preserves_existing_data(tmp_path):
    database_path = tmp_path / "interviews.sqlite3"
    with sqlite3.connect(database_path) as connection:
        connection.execute("CREATE TABLE interview_turns (id TEXT PRIMARY KEY, session_id TEXT, turn_index INTEGER, question TEXT, answer TEXT, topic TEXT, phase TEXT, action TEXT, followup_depth INTEGER, decision_note TEXT, created_at DATETIME, answered_at DATETIME)")
        connection.execute("INSERT INTO interview_turns (id, question, answer) VALUES ('old', 'old question', 'old answer')")
    engine, factory = create_database(tmp_path)
    assert "audio_path" in {column["name"] for column in inspect(engine).get_columns("interview_turns")}
    with sqlite3.connect(database_path) as connection:
        assert connection.execute("SELECT answer FROM interview_turns WHERE id='old'").fetchone()[0] == "old answer"
    engine.dispose()


@pytest.mark.parametrize("body", [b"not JSON", b"[]"])
def test_invalid_voice_response_is_friendly(body):
    service = VoiceService(VoiceSettings(api_key="fake"), httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(200, content=body))))
    try:
        with pytest.raises(ServiceError, match="无效结果"):
            service.request("GET", "/models")
    finally:
        service.close()

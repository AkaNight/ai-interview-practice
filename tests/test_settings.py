import json
from dataclasses import replace

import httpx
import pytest
from fastapi.testclient import TestClient
from openai import OpenAI

from app.config import Settings
from app.main import create_app
from app.schemas import Research
from app.services.compatible_service import CompatibleService
from app.services.errors import ServiceError
from tests.helpers import create_session, sample_docx, start_session


def test_save_and_reload_settings_keep_keys_on_backend(client, settings):
    key = "test-secret-never-echo"
    saved = client.post("/api/settings", json={"provider": "deepseek", "api_key": key})
    assert saved.status_code == 200
    assert key not in saved.text
    assert saved.json()["providers"]["deepseek"]["key_configured"]
    assert client.get("/api/health").json()["ai_mode"] == "deepseek"
    assert key not in client.get("/api/settings").text
    path = settings.data_dir / "api_settings.json"
    assert json.loads(path.read_text())["providers"]["deepseek"]["api_key"] == key
    # Loading without reading .env tests the persisted local configuration.
    config = json.loads(path.read_text())
    deepseek = config["providers"]["deepseek"]
    with TestClient(create_app(replace(settings, ai_mode="deepseek", api_key=key, main_model=deepseek["main_model"], base_url=deepseek["base_url"]))) as restarted:
        assert restarted.get("/api/settings").json()["providers"]["deepseek"]["key_configured"]
        assert key not in restarted.get("/api/settings").text


def test_blank_retains_key_and_clear_deletes(client, settings):
    client.post("/api/settings", json={"provider": "deepseek", "api_key": "fake-secret"})
    result = client.post("/api/settings", json={"provider": "deepseek", "api_key": ""})
    assert result.json()["providers"]["deepseek"]["key_configured"]
    result = client.post("/api/settings", json={"provider": "deepseek", "clear_key": True})
    assert not result.json()["providers"]["deepseek"]["key_configured"]
    assert "fake-secret" not in (settings.data_dir / "api_settings.json").read_text()


def test_switch_provider_never_reuses_another_providers_key(client):
    client.post("/api/settings", json={"provider": "deepseek", "api_key": "deepseek-only"})
    result = client.post("/api/settings", json={"provider": "openai"})
    assert not result.json()["providers"]["openai"]["key_configured"]
    assert client.app.state.settings.api_key == ""


def test_changing_custom_endpoint_requires_new_key(client):
    client.post("/api/settings", json={"provider": "compatible", "base_url": "https://original.example/v1", "main_model": "model", "api_key": "original-secret"})
    response = client.post("/api/settings", json={"provider": "compatible", "base_url": "https://another.example/v1", "main_model": "model"})
    assert response.status_code == 400
    assert "original-secret" not in response.text
    assert client.app.state.settings.base_url == "https://original.example/v1"


@pytest.mark.parametrize("provider,url", [
    ("deepseek", "https://wrong.example/v1"), ("openai", "https://api.openai-hub.net/v1"),
    ("compatible", "http://remote.example/v1"), ("compatible", "https://user:password@host.example/v1"),
    ("compatible", "https://host.example/v1?api_key=sensitive"), ("compatible", "https://host.example:bad/v1"),
])
def test_bad_settings_urls_rejected(client, provider, url):
    response = client.post("/api/settings", json={"provider": provider, "base_url": url, "main_model": "model"})
    assert response.status_code == 400
    assert "sensitive" not in response.text and "password" not in response.text


def test_test_connection_draft_does_not_save_or_switch(client, monkeypatch, settings):
    from app.services import settings_service
    class FakeClient:
        def close(self):
            pass
    class AI(FakeClient):
        def __init__(self):
            from types import SimpleNamespace
            self.client = SimpleNamespace(models=SimpleNamespace(list=lambda: SimpleNamespace(data=[SimpleNamespace(id="test-model")])),
                chat=SimpleNamespace(completions=SimpleNamespace(create=lambda **_: SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content="OK"))]))))
        def _call(self, callback):
            return callback()
    monkeypatch.setattr(settings_service, "build_ai", lambda _, usage=None: AI())
    response = client.post("/api/settings/test", json={"provider": "compatible", "base_url": "https://test.example/v1", "main_model": "test-model", "api_key": "secret"})
    assert response.status_code == 200
    assert client.get("/api/health").json()["ai_mode"] == "demo"
    assert not (settings.data_dir / "api_settings.json").exists()


def test_provider_changes_block_existing_session(client):
    identifier, _ = start_session(client)
    client.post("/api/settings", json={"provider": "deepseek", "api_key": "fake-secret"})
    assert client.post(f"/api/interviews/{identifier}/finish").status_code == 409


def test_response_compatible_provider_uses_responses(client):
    result = client.post("/api/settings", json={"provider": "compatible", "base_url": "https://proxy.example/v1", "main_model": "gpt-4.1", "api_format": "responses"})
    assert result.status_code == 200
    assert client.app.state.settings.resolved_api_format == "responses"
    from app.services.openai_service import OpenAIService
    assert type(client.app.state.ai) is OpenAIService


def test_deepseek_contract_and_independent_research(tmp_path):
    requests = []
    value = Research(company_summary="摘要资料", position_summary="岗位摘要", interview_process=[], common_question_types=[], technical_topics=[], company_specific_topics=[], behavioral_topics=[], uncertainties=[], sources=[])
    def handler(request):
        requests.append(json.loads(request.content))
        return httpx.Response(200, json={"id": "test", "object": "chat.completion", "created": 1, "model": "deepseek-flash", "choices": [{"index": 0, "finish_reason": "stop", "message": {"role": "assistant", "content": value.model_dump_json()}}]})
    class FakeSearch:
        def research(self, context):
            return {"results": [{"title": "来源", "url": "https://company.example/jobs", "snippet": "岗位资料"}], "limitations": ["仅摘要，待核实"]}
    settings = Settings(data_dir=tmp_path, ai_mode="deepseek", api_key="fake-secret", main_model="deepseek-flash")
    service = CompatibleService(settings, search=FakeSearch())
    service.client.close()
    service.client = OpenAI(api_key="fake-secret", base_url="https://api.deepseek.com", http_client=httpx.Client(transport=httpx.MockTransport(handler)))
    try:
        result = service.research_interview({"company": "公司", "position": "岗位", "interview_type": "综合模拟"})
        assert result.sources[0].url == "https://company.example/jobs"
        assert "仅摘要，待核实" in result.uncertainties
        assert requests[0]["response_format"] == {"type": "json_object"}
        assert requests[0]["thinking"] == {"type": "disabled"}
        assert "JSON" in requests[0]["messages"][0]["content"]
        assert "tools" not in requests[0]
    finally:
        service.close()


def test_search_failure_does_not_use_model_memory(tmp_path):
    class FailingSearch:
        def research(self, context):
            raise ServiceError("搜索失败")
    service = CompatibleService(Settings(data_dir=tmp_path, ai_mode="deepseek", api_key="fake-secret"), FailingSearch())
    try:
        with pytest.raises(ServiceError, match="搜索失败"):
            service.research_interview({})
    finally:
        service.close()


def test_settings_validation_does_not_echo_secret(client):
    response = client.post("/api/settings", json={"provider": "invalid", "api_key": "secret-never-echo"})
    assert response.status_code == 422 and "secret-never-echo" not in response.text


def test_config_repr_hides_key(tmp_path):
    assert "hidden-key" not in repr(Settings(data_dir=tmp_path, api_key="hidden-key"))

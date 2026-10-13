import json
from datetime import datetime, timedelta, timezone

import httpx
import pytest
from openai import OpenAI
from sqlalchemy import select

from app.database import create_database
from app.models import UsageEvent
from app.services.openai_service import OpenAIService
from app.services.usage_service import MeteredTransport, UsageService, UsageSettings, usage_context
from tests.helpers import start_session
from tests.test_openai_service import api_response
from app.schemas import Question


@pytest.fixture
def usage(tmp_path):
    engine, factory = create_database(tmp_path)
    yield UsageService(factory)
    engine.dispose()


def metered(usage, handler, kind="ai", model="test-model", base="https://test.example/v1"):
    return httpx.Client(transport=MeteredTransport(usage, kind, "compatible", base, model, httpx.MockTransport(handler)))


def set_prices(usage, **kwargs):
    payload = {"prices": [dict(kind="ai", provider="compatible", base_url="https://test.example/v1", model="test-model",
                              input_per_million=10, output_per_million=20)], **kwargs}
    usage.save(UsageSettings(**payload))


def test_responses_usage_recorded_before_schema_parse_and_retries(usage, settings):
    set_prices(usage)
    calls = []
    question = Question(action="new_topic", question="请介绍项目。", topic="project", phase="projects", followup_depth=0, decision_note="开始")
    def handler(request):
        calls.append(request)
        body = api_response("invalid JSON" if len(calls) == 1 else question.model_dump_json())
        body["usage"] = {"input_tokens": 100, "output_tokens": 50, "total_tokens": 150}
        return httpx.Response(200, json=body)
    service = OpenAIService(settings)
    service.client = OpenAI(api_key="test-only", base_url="https://test.example/v1", max_retries=0, http_client=metered(usage, handler))
    try:
        assert service.generate_question({}, lambda _: None).question == question.question
    finally:
        service.close()
    summary = usage.summary()
    assert summary["monthly"]["requests"] == 2
    assert summary["monthly"]["total_tokens"] == 300
    assert summary["monthly"]["estimated_yuan"] == pytest.approx(.004)


def test_sdk_network_retry_is_separate_event_and_no_sensitive_payloads(usage):
    calls = []
    def handler(request):
        calls.append(request)
        if len(calls) == 1:
            return httpx.Response(429, json={"error": {"message": "PRIVATE KEY OR RESUME", "type": "rate_limit"}})
        return httpx.Response(200, json={"choices": [{"index": 0, "finish_reason": "stop", "message": {"role": "assistant", "content": "PRIVATE ANSWER"}}],
                                         "usage": {"prompt_tokens": 300, "completion_tokens": 100}})
    with OpenAI(api_key="NEVER_SAVE_SECRET", base_url="https://test.example/v1", max_retries=1, http_client=metered(usage, handler)) as sdk:
        sdk.chat.completions.create(model="test-model", messages=[{"role": "user", "content": "PRIVATE RESUME"}])
    result = usage.summary()
    assert result["monthly"]["requests"] == 2 and result["monthly"]["failed"] == 1
    assert result["monthly"]["total_tokens"] == 400 and result["monthly"]["unknown_token_requests"] == 1
    serialized = json.dumps(result)
    assert "PRIVATE" not in serialized and "NEVER_SAVE_SECRET" not in serialized


def test_missing_usage_and_timeout_are_unknown_not_free(usage):
    set_prices(usage)
    with metered(usage, lambda _: httpx.Response(200, json={"text": "OK"})) as client:
        client.post("https://test.example/v1/responses")
    def timeout(request):
        raise httpx.ReadTimeout("sensitive", request=request)
    with metered(usage, timeout) as client, pytest.raises(httpx.ReadTimeout):
        client.post("https://test.example/v1/responses")
    result = usage.summary()
    assert result["monthly"]["requests"] == 2 and result["monthly"]["failed"] == 1
    assert result["monthly"]["unknown_token_requests"] == 2
    assert all(row["estimated_yuan"] is None for row in result["items"])


def test_web_search_tool_fee_remains_partial(usage):
    set_prices(usage)
    with metered(usage, lambda _: httpx.Response(200, json={"usage": {"input_tokens": 1000, "output_tokens": 100}, "output": [{"type": "web_search_call"}]})) as client:
        client.post("https://test.example/v1/responses")
    row = usage.summary()["items"][0]
    assert row["estimated_yuan"] == pytest.approx(.012) and row["partial_estimate"] and row["web_search_calls"] == 1


def test_asr_minutes_and_each_retry_are_counted(usage):
    usage.save(UsageSettings(prices=[dict(kind="voice", provider="compatible", base_url="https://test.example/v1", model="test-model", audio_per_minute=.1)]))
    token = usage_context.set({"audio_seconds": 30, "operation": "语音转写"})
    try:
        with metered(usage, lambda _: httpx.Response(200, json={"text": "private"}), kind="voice") as client:
            for _ in range(2):
                client.post("https://test.example/v1/audio/transcriptions")
    finally:
        usage_context.reset(token)
    result = usage.summary()
    assert result["monthly"]["audio_seconds"] == 60
    assert result["monthly"]["estimated_yuan"] == pytest.approx(.1)
    assert result["monthly"]["unknown_token_requests"] == 0


def test_connection_list_has_no_token_unknown_or_audio_duration(usage):
    with metered(usage, lambda _: httpx.Response(200, json={"data": []})) as client:
        client.get("https://test.example/v1/models")
    result = usage.summary()
    assert result["monthly"]["requests"] == 1 and result["monthly"]["unknown_token_requests"] == 0
    assert result["items"][0]["estimated_yuan"] == 0


def test_pricing_is_snapshot_and_endpoint_specific(usage):
    identifier = usage.begin("ai", "compatible", "https://test.example/v1", "test-model", "/responses")
    set_prices(usage)
    usage.finish(identifier, httpx.Response(200, json={"usage": {"input_tokens": 100, "output_tokens": 100}}))
    assert usage.summary()["items"][0]["estimated_yuan"] is None
    with metered(usage, lambda _: httpx.Response(200, json={"usage": {"input_tokens": 100, "output_tokens": 100}}), base="https://other.example/v1") as client:
        client.post("https://other.example/v1/responses")
    assert usage.summary()["items"][0]["estimated_yuan"] is None


def test_monthly_warnings_pagination_and_restart(usage):
    set_prices(usage, monthly_request_limit=2, monthly_budget_yuan=.003)
    token = usage_context.set({"session_id": "first"})
    try:
        with metered(usage, lambda _: httpx.Response(200, json={"usage": {"input_tokens": 100, "output_tokens": 100}})) as client:
            for _ in range(2):
                client.post("https://test.example/v1/responses")
    finally:
        usage_context.reset(token)
    old = usage.begin("ai", "compatible", "https://test.example/v1", "test-model", "/responses")
    with usage.session_factory() as db:
        db.get(UsageEvent, old).created_at = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=65)
        db.commit()
    restarted = UsageService(usage.session_factory)
    result = restarted.summary(limit=1, offset=1, session_id="first")
    assert result["total"] == 2 and len(result["items"]) == 1
    assert result["monthly"]["requests"] == 2 and len(result["warnings"]) == 2
    assert restarted.summary(session_id="other")["items"] == []
    assert restarted.summary(session_id="other")["monthly"]["requests"] == 2


def test_api_test_settings_usage_context_no_real_network(client, monkeypatch):
    def handler(request):
        if request.method == "GET":
            return httpx.Response(200, json={"data": [{"id": "test-model"}]})
        body = api_response("OK")
        body["usage"] = {"input_tokens": 10, "output_tokens": 2}
        return httpx.Response(200, json=body)
    monkeypatch.setattr(httpx, "HTTPTransport", lambda: httpx.MockTransport(handler))
    response = client.post("/api/settings/test", json={"provider": "compatible", "api_format": "responses", "base_url": "https://test.example/v1", "main_model": "test-model", "api_key": "fake-secret"})
    assert response.status_code == 200
    result = client.get("/api/usage").json()
    assert result["monthly"]["requests"] == 2 and result["monthly"]["total_tokens"] == 12
    assert all(row["operation"] == "测试连接" for row in result["items"])
    assert "fake-secret" not in json.dumps(result)


def test_demo_cache_and_history_create_no_usage(client):
    identifier, _ = start_session(client)
    client.get(f"/api/interviews/{identifier}")
    client.get("/api/interviews")
    client.post(f"/api/interviews/{identifier}/finish")
    client.get(f"/api/interviews/{identifier}/report")
    assert client.get("/api/usage").json()["monthly"]["requests"] == 0


@pytest.mark.parametrize("payload", [{"monthly_request_limit": 0}, {"monthly_budget_yuan": -1}, {"prices": [{"kind": "ai", "provider": "compatible", "base_url": "https://host.example/v1?key=secret", "model": "x"}]}, {"api_key": "secret"}])
def test_policy_invalid_inputs_do_not_echo_secrets(client, payload):
    response = client.post("/api/usage/settings", json=payload)
    assert response.status_code in {400, 422} and "secret" not in response.text


def test_pending_requests_persist_across_crash_and_unknown_cost_warns(usage):
    usage.save(UsageSettings(monthly_budget_yuan=1))
    usage.begin("ai", "compatible", "https://test.example/v1", "test-model", "/responses")
    result = UsageService(usage.session_factory).summary()
    assert result["monthly"]["pending"] == 1
    assert len(result["warnings"]) == 1 and result["items"][0]["estimated_yuan"] is None

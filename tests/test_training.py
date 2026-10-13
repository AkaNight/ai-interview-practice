from datetime import timedelta
from dataclasses import replace
from unittest.mock import Mock
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.main import create_app
from app.models import InterviewReport, InterviewSession, now
from app.services.errors import ServiceError
from tests.helpers import create_session, start_session


def completed(client):
    identifier, _ = start_session(client)
    client.post(f"/api/interviews/{identifier}/turn", json={"turn_index": 1, "answer": "我负责 PCAP 数据清洗和 Suricata 规则验证。"})
    result = client.post(f"/api/interviews/{identifier}/finish")
    assert result.status_code == 200
    return identifier


def training(client):
    source = completed(client)
    created = client.post(f"/api/interviews/{source}/practice", json={"request_id": str(uuid4())})
    assert created.status_code == 201, created.text
    identifier = created.json()["id"]
    assert client.post(f"/api/interviews/{identifier}/prepare").status_code == 200
    assert client.post(f"/api/interviews/{identifier}/start").status_code == 200
    return identifier


def submit(client, identifier, index=1, answer="我负责 Suricata 规则验证，并检查误报。"):
    return client.post(f"/api/interviews/{identifier}/turn", json={"turn_index": index, "answer": answer})


def test_one_click_practice_reuses_data_and_new_context_without_search(client, ai, monkeypatch):
    source = completed(client)
    original = client.get(f"/api/interviews/{source}").json()
    search = Mock(side_effect=AssertionError("No paid search needed"))
    strategy = Mock(wraps=ai.generate_strategy)
    monkeypatch.setattr(ai, "research_interview", search)
    monkeypatch.setattr(ai, "parse_resume", Mock(side_effect=AssertionError("No reparse needed")))
    monkeypatch.setattr(ai, "generate_strategy", strategy)
    request_id = str(uuid4())
    created = client.post(f"/api/interviews/{source}/practice", json={"request_id": request_id}).json()
    identifier = created["id"]
    assert created["mode"] == "training" and created["status"] == "researched"
    assert created["practice_from"] == source and created["resume_id"] == original["resume_id"]
    assert not created["turns"] and not created["report"]
    assert created["training_plan"] == original["report"]["next_practice"]
    assert client.post(f"/api/interviews/{source}/practice", json={"request_id": request_id}).json()["id"] == identifier
    assert client.post(f"/api/interviews/{identifier}/prepare").status_code == 200
    assert strategy.call_count == 1 and strategy.call_args.args[0]["mode"] == "training"
    started = client.post(f"/api/interviews/{identifier}/start").json()
    assert started["training_plan"]["focus"] in started["turns"][0]["question"]
    assert not started["turns"][0]["question"].startswith("请先做一下自我介绍")
    assert client.get(f"/api/interviews/{source}").json() == original
    assert search.call_count == 0


def test_feedback_pause_continue_and_repeat_are_cached(client, ai, monkeypatch):
    identifier = training(client)
    step = Mock(wraps=ai.generate_training_step)
    monkeypatch.setattr(ai, "generate_training_step", step)
    answered = submit(client, identifier)
    assert answered.status_code == 200
    paused = answered.json()
    assert paused["training_paused"] and not paused["needs_next_question"]
    assert len(paused["turns"]) == 1 and paused["turns"][0]["feedback"]
    assert submit(client, identifier).json() == paused and step.call_count == 1
    monkeypatch.setattr(ai, "generate_question", Mock(side_effect=AssertionError("Continue is local")))
    next_result = client.post(f"/api/interviews/{identifier}/continue", json={"turn_index": 1}).json()
    assert len(next_result["turns"]) == 2 and not next_result["training_paused"]
    assert client.post(f"/api/interviews/{identifier}/continue", json={"turn_index": 1}).json() == next_result
    assert step.call_count == 1


def test_repeat_saves_separate_attempt_and_original_is_immutable(client, ai, monkeypatch):
    identifier = training(client)
    paused = submit(client, identifier).json()
    monkeypatch.setattr(ai, "generate_question", Mock(side_effect=AssertionError("Repeat is local")))
    repeated = client.post(f"/api/interviews/{identifier}/repeat", json={"turn_index": 1}).json()
    assert repeated["turns"][0] == paused["turns"][0]
    assert repeated["turns"][1]["question"] == repeated["turns"][0]["question"]
    assert repeated["turns"][1]["repeat_of"] == 1
    assert repeated["turns"][1]["answer"] is None and repeated["turns"][1]["feedback"] is None
    assert client.post(f"/api/interviews/{identifier}/repeat", json={"turn_index": 1}).json() == repeated


def test_training_step_failure_preserves_answer_and_can_retry(client, ai, monkeypatch):
    identifier = training(client)
    original = ai.generate_training_step
    monkeypatch.setattr(ai, "generate_training_step", Mock(side_effect=ServiceError("模拟讲评失败")))
    assert submit(client, identifier).status_code == 502
    saved = client.get(f"/api/interviews/{identifier}").json()
    assert saved["turns"][0]["answer"] and saved["needs_next_question"] and not saved["training_paused"]
    assert client.post(f"/api/interviews/{identifier}/continue", json={"turn_index": 1}).status_code == 409
    monkeypatch.setattr(ai, "generate_training_step", original)
    assert submit(client, identifier).json()["training_paused"]


def test_pause_time_excluded_and_pause_survives_restart(client, settings):
    identifier = training(client)
    submit(client, identifier)
    with client.app.state.session_factory() as db:
        session = db.get(InterviewSession, identifier)
        session.started_at = now() - timedelta(minutes=12)
        session.state_json = {**session.state_json, "training_pause_started_at": (now() - timedelta(minutes=11)).isoformat()}
        db.commit()
        context = client.app.state.interviews.context(db, session)
        assert 58 <= context["state"]["elapsed_seconds"] <= 62
        assert not context["constraints"]["must_end"]
    with TestClient(create_app(settings)) as restarted:
        assert restarted.get(f"/api/interviews/{identifier}").json()["training_paused"]
        assert restarted.post(f"/api/interviews/{identifier}/continue", json={"turn_index": 1}).status_code == 200
    with client.app.state.session_factory() as db:
        session = db.get(InterviewSession, identifier)
        assert session.state_json["training_paused_seconds"] >= 660


def test_simulation_never_gets_feedback_or_training_controls(client, ai, monkeypatch):
    identifier, _ = start_session(client)
    monkeypatch.setattr(ai, "generate_training_step", Mock(side_effect=AssertionError("Simulation cannot coach")))
    value = submit(client, identifier).json()
    assert value["mode"] == "simulation" and not value["training_paused"]
    assert len(value["turns"]) == 2 and all(turn["feedback"] is None for turn in value["turns"])
    assert client.post(f"/api/interviews/{identifier}/continue", json={"turn_index": 1}).status_code == 409


def test_manual_training_needs_focus_and_source_practice_needs_answered_report(client):
    source = create_session(client)
    resume_id = client.get(f"/api/interviews/{source}").json()["resume_id"]
    payload = {"resume_id": resume_id, "company": "测试公司", "position": "测试岗位", "mode": "training"}
    assert client.post("/api/interviews", json=payload).status_code == 422
    response = client.post("/api/interviews", json=payload | {"training_focus": "  TLS握手  "})
    assert response.status_code == 201 and response.json()["training_plan"]["focus"] == "TLS握手"
    assert client.post(f"/api/interviews/{source}/practice", json={"request_id": str(uuid4())}).status_code == 409


def test_training_end_limit_generates_feedback_before_explicit_report(client):
    identifier = training(client)
    with client.app.state.session_factory() as db:
        session = db.get(InterviewSession, identifier)
        session.started_at = now() - timedelta(hours=1)
        db.commit()
    paused = submit(client, identifier).json()
    assert paused["training_paused"] and paused["training_will_end"] and paused["report"] is None
    assert client.post(f"/api/interviews/{identifier}/repeat", json={"turn_index": 1}).status_code == 409
    result = client.post(f"/api/interviews/{identifier}/continue", json={"turn_index": 1}).json()
    assert result["status"] == "completed" and result["report"]


def test_source_delete_keeps_training_resume_and_research(client):
    source = completed(client)
    value = client.post(f"/api/interviews/{source}/practice", json={"request_id": str(uuid4())}).json()
    assert client.delete(f"/api/interviews/{source}").json()["resume_deleted"] is False
    assert client.post(f"/api/interviews/{value['id']}/prepare").status_code == 200


def test_real_practice_uses_current_model_and_legacy_plan_without_ai_call(client, ai, settings, monkeypatch):
    source = completed(client)
    with client.app.state.session_factory() as db:
        session = db.get(InterviewSession, source)
        session.provider_mode = "openai"
        report = db.scalar(select(InterviewReport).where(InterviewReport.session_id == source))
        report.report_json = {**report.report_json, "next_practice": None}
        db.commit()
    service = client.app.state.interviews
    service.settings = replace(settings, ai_mode="compatible", main_model="new-model", base_url="https://unit.test/v1")
    monkeypatch.setattr(ai, "generate_report", Mock(side_effect=AssertionError("No paid plan regeneration")))
    created = client.post(f"/api/interviews/{source}/practice", json={"request_id": str(uuid4())})
    assert created.status_code == 201 and created.json()["provider_mode"] == "compatible"
    assert created.json()["training_plan"]["focus"]
    with client.app.state.session_factory() as db:
        new = db.get(InterviewSession, created.json()["id"])
        assert new.state_json["provider_config"]["model"] == "new-model"


def test_voluntary_closing_can_still_repeat_before_program_limit(client):
    identifier = training(client)
    submit(client, identifier)
    with client.app.state.session_factory() as db:
        session = db.get(InterviewSession, identifier)
        pending = {**session.state_json["pending_question"], "action": "end", "question": "", "phase": "closing"}
        session.state_json = {**session.state_json, "pending_question": pending}
        db.commit()
    view = client.get(f"/api/interviews/{identifier}").json()
    assert view["training_will_end"] and view["training_can_repeat"]
    assert client.post(f"/api/interviews/{identifier}/repeat", json={"turn_index": 1}).status_code == 200

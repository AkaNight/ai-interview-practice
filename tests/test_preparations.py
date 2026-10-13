from dataclasses import replace
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.main import create_app
from app.models import InterviewPreparation, InterviewSession, Resume
from app.services.errors import ServiceError
from tests.helpers import create_session, sample_docx, start_session


def preparation_id(client, identifier):
    return client.get(f"/api/interviews/{identifier}").json()["preparation_id"]


def reuse(client, source_id, **changes):
    source = client.get(f"/api/interviews/{source_id}").json()
    payload = {"resume_id": source["resume_id"], "company": source["company"], "position": source["position"],
               "interview_type": source["interview_type"], "preparation_id": source["preparation_id"]} | changes
    return client.post("/api/interviews", json=payload)


def test_fresh_preparation_saves_library_and_sources_without_private_settings(client):
    identifier, _ = start_session(client)
    pid = preparation_id(client, identifier)
    result = client.get("/api/preparations").json()
    assert result["total"] == 1 and result["items"][0]["id"] == pid
    detail = client.get(f"/api/preparations/{pid}").json()
    assert detail["researched_at"] and detail["research"]["uncertainties"]
    assert detail["settings"] == {"company": "中国银行", "position": "信息科技岗", "interview_type": "半结构化面试", "duration_minutes": 20, "notes": "", "mode": "simulation", "training_focus": ""}
    assert detail["resume"]["filename"] == "resume.docx"
    assert "api_key" not in str(detail) and "strategy" not in detail
    assert client.get("/api/preparations?limit=0").status_code == 422


def test_reuse_skips_search_regenerates_strategy_for_current_resume(client, ai, monkeypatch):
    identifier, _ = start_session(client)
    upload = client.post("/api/resumes", files={"file": ("new.docx", sample_docx(["技能：Java、SQL", "项目：交易系统"]))}).json()
    client.post(f"/api/resumes/{upload['id']}/parse")
    monkeypatch.setattr(ai, "research_interview", Mock(side_effect=AssertionError("Must skip search")))
    strategy = Mock(wraps=ai.generate_strategy)
    monkeypatch.setattr(ai, "generate_strategy", strategy)
    created = reuse(client, identifier, resume_id=upload["id"], notes="新的补充说明", duration_minutes=10)
    assert created.status_code == 201, created.text
    result = created.json()
    assert result["research_origin"] == "reused" and result["status"] == "researched"
    assert not result["turns"]
    prepared = client.post(f"/api/interviews/{result['id']}/prepare")
    assert prepared.status_code == 200
    assert strategy.call_count == 1
    context = strategy.call_args.args[0]
    assert context["resume"]["skills"] == ["SQL", "Java"]
    assert context["notes"] == "新的补充说明" and context["turns"] == []
    assert client.get("/api/preparations").json()["total"] == 1
    assert client.post(f"/api/interviews/{result['id']}/start").status_code == 200


def test_library_is_saved_before_strategy_failure_and_retry_does_not_duplicate(client, ai, monkeypatch):
    identifier = create_session(client)
    original = ai.generate_strategy
    monkeypatch.setattr(ai, "generate_strategy", Mock(side_effect=ServiceError("策略失败")))
    assert client.post(f"/api/interviews/{identifier}/prepare").status_code == 502
    assert client.get("/api/preparations").json()["total"] == 1
    monkeypatch.setattr(ai, "generate_strategy", original)
    monkeypatch.setattr(ai, "research_interview", Mock(side_effect=AssertionError("cached")))
    assert client.post(f"/api/interviews/{identifier}/prepare").status_code == 200
    assert client.get("/api/preparations").json()["total"] == 1


@pytest.mark.parametrize("changes", [{"company": "另一家公司"}, {"position": "另一岗位"}, {"interview_type": "HR面"}])
def test_reuse_rejects_different_research_scope(client, changes):
    identifier, _ = start_session(client)
    assert reuse(client, identifier, **changes).status_code == 409
    assert client.get("/api/interviews").json()["total"] == 1


def test_real_research_can_cross_models_but_demo_cannot_be_used_as_real(client, settings):
    identifier, _ = start_session(client)
    pid = preparation_id(client, identifier)
    service = client.app.state.interviews
    service.settings = replace(settings, ai_mode="compatible", main_model="different-model", base_url="https://unit.test/v1")
    assert reuse(client, identifier).status_code == 409
    assert client.get("/api/preparations").json()["total"] == 0
    with client.app.state.session_factory() as db:
        cached = db.get(InterviewPreparation, pid)
        cached.provider_mode = "openai"
        db.commit()
    created = reuse(client, identifier)
    assert created.status_code == 201
    assert created.json()["provider_mode"] == "compatible"
    assert client.get("/api/preparations").json()["total"] == 1
    with client.app.state.session_factory() as db:
        session = db.get(InterviewSession, created.json()["id"])
        assert session.state_json["provider_config"]["model"] == "different-model"


def test_new_research_when_no_cache_selected_creates_another_library_version(client, ai, monkeypatch):
    first, _ = start_session(client)
    search = Mock(wraps=ai.research_interview)
    monkeypatch.setattr(ai, "research_interview", search)
    new = reuse(client, first, preparation_id=None).json()
    assert new["status"] == "created"
    assert client.post(f"/api/interviews/{new['id']}/prepare").status_code == 200
    assert search.call_count == 1 and client.get("/api/preparations").json()["total"] == 2


def test_deleting_original_session_keeps_independent_library_but_not_resume(client):
    identifier, _ = start_session(client)
    pid = preparation_id(client, identifier)
    assert client.delete(f"/api/interviews/{identifier}").status_code == 200
    detail = client.get(f"/api/preparations/{pid}").json()
    assert detail["research"] and detail["resume"] is None
    assert client.get("/api/preparations").json()["total"] == 1


def test_delete_library_preserves_sessions_and_does_not_resurrect_on_restart(client, settings):
    identifier, _ = start_session(client)
    pid = preparation_id(client, identifier)
    second = reuse(client, identifier).json()["id"]
    assert client.delete(f"/api/preparations/{pid}").status_code == 200
    assert client.get(f"/api/preparations/{pid}").status_code == 404
    assert client.get(f"/api/interviews/{identifier}").status_code == 200
    assert client.post(f"/api/interviews/{second}/prepare").status_code == 200
    with TestClient(create_app(settings)) as restarted:
        assert restarted.get("/api/preparations").json()["total"] == 0
        assert reuse(restarted, identifier).status_code == 404


def test_old_research_backfills_once_with_unknown_date(client, settings):
    identifier, _ = start_session(client)
    pid = preparation_id(client, identifier)
    with client.app.state.session_factory() as db:
        cached = db.get(InterviewPreparation, pid)
        db.delete(cached)
        session = db.get(InterviewSession, identifier)
        session.state_json = {"provider_config": session.state_json["provider_config"]}
        db.commit()
    with TestClient(create_app(settings)) as restarted:
        result = restarted.get("/api/preparations").json()
        assert result["total"] == 1 and result["items"][0]["researched_at"] is None
    with TestClient(create_app(settings)) as restarted:
        assert restarted.get("/api/preparations").json()["total"] == 1

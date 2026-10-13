from datetime import timedelta
from unittest.mock import Mock
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.models import InterviewSession, now
from app.schemas import Score
from app.services.errors import ServiceError
from app.services.group_service import round_context, validate_round, validate_scenario
from app.services.report_service import validate_report
from tests.helpers import create_session
from tests.test_reports_history import sample_report


def create_group(client, size=5, duration=5):
    source = create_session(client)
    resume = client.get(f"/api/interviews/{source}").json()["resume_id"]
    created = client.post("/api/interviews", json={"resume_id": resume, "company": "测试公司", "position": "技术岗位",
        "mode": "group", "group_size": size, "group_topic": "资源优先级", "duration_minutes": duration})
    assert created.status_code == 201, created.text
    return created.json()["id"]


def start_group(client, **kwargs):
    identifier = create_group(client, **kwargs)
    assert client.post(f"/api/interviews/{identifier}/prepare").status_code == 200
    result = client.post(f"/api/interviews/{identifier}/start")
    assert result.status_code == 200, result.text
    return identifier, result.json()


def say(client, identifier, index=1, answer="我建议优先改善数据质量，再治理误报，并明确验证标准。"):
    return client.post(f"/api/interviews/{identifier}/turn", json={"turn_index": index, "answer": answer})


@pytest.mark.parametrize("size", [3, 5, 6])
def test_group_roster_case_and_grounded_responses(client, size):
    identifier, opening = start_group(client, size=size)
    assert opening["mode"] == "group" and opening["group_scenario"]["fictional"]
    assert len(opening["group_scenario"]["teammates"]) == size - 1
    assert len(opening["turns"][0]["group_round"]["messages"]) == size - 1
    response = say(client, identifier)
    assert response.status_code == 200
    value = response.json()
    messages = value["turns"][-1]["group_round"]["messages"]
    assert len(messages) == 2 and value["group_stage"] == "discussion"
    assert any(message["reply_to_quote"] for message in messages)
    for message in messages:
        if message["reply_to_quote"]:
            assert message["reply_to_quote"] in value["turns"][0]["answer"]
    assert say(client, identifier).json() == value


def test_group_rotates_speakers_and_closes_after_user_summary(client):
    identifier, value = start_group(client)
    first = say(client, identifier).json()
    assert [m["member_id"] for m in first["turns"][-1]["group_round"]["messages"]] == ["member_1", "member_2"]
    second = say(client, identifier, 2, "回应成员B，我同意比较短期风险，但数据质量仍是基础。").json()
    assert [m["member_id"] for m in second["turns"][-1]["group_round"]["messages"]] == ["member_3", "member_4"]
    summary = say(client, identifier, 3, "我们按影响、成本和依赖排序，先列出各项验证标准。").json()
    assert summary["group_stage"] == "summary"
    ended = say(client, identifier, 4, "总结：先改善数据质量再治理误报，培训延期；分歧在于两者先后，建议小范围验证。").json()
    assert ended["status"] == "completed" and ended["group_closing"]["stage"] == "closing"
    stats = ended["report"]["group_statistics"]
    assert stats["user_contributions"] == 4 and stats["user_summary_submitted"]
    assert sum(member["contributions"] for member in stats["teammates"]) == 12
    assert len(ended["report"]["answer_reviews"]) == 4
    assert client.get(f"/api/interviews/{identifier}/report").json() == ended["report"]


def test_scenario_failure_preserves_research_and_retries_without_search(client, ai, monkeypatch):
    identifier = create_group(client)
    original = ai.generate_group_scenario
    monkeypatch.setattr(ai, "generate_group_scenario", Mock(side_effect=ServiceError("案例失败")))
    assert client.post(f"/api/interviews/{identifier}/prepare").status_code == 502
    assert client.get(f"/api/interviews/{identifier}").json()["status"] == "researched"
    monkeypatch.setattr(ai, "generate_group_scenario", original)
    monkeypatch.setattr(ai, "research_interview", Mock(side_effect=AssertionError("Do not search again")))
    assert client.post(f"/api/interviews/{identifier}/prepare").json()["status"] == "prepared"


def test_peer_failure_preserves_speech_and_exact_retry_does_not_duplicate(client, ai, monkeypatch):
    identifier, _ = start_group(client)
    original = ai.generate_group_round
    monkeypatch.setattr(ai, "generate_group_round", Mock(side_effect=ServiceError("队友回应失败")))
    assert say(client, identifier).status_code == 502
    saved = client.get(f"/api/interviews/{identifier}").json()
    assert saved["needs_next_question"] and saved["turns"][0]["answer"]
    monkeypatch.setattr(ai, "generate_group_round", original)
    spy = Mock(wraps=original)
    monkeypatch.setattr(ai, "generate_group_round", spy)
    value = say(client, identifier).json()
    assert say(client, identifier).json() == value and spy.call_count == 1


def test_group_restarts_and_case_repractice_skips_search_and_scenario(client, ai, settings, monkeypatch):
    identifier, _ = start_group(client)
    say(client, identifier)
    ended = client.post(f"/api/interviews/{identifier}/finish").json()
    with TestClient(create_app(settings)) as restarted:
        assert restarted.get(f"/api/interviews/{identifier}").json() == ended
    monkeypatch.setattr(ai, "research_interview", Mock(side_effect=AssertionError("cached")))
    monkeypatch.setattr(ai, "generate_group_scenario", Mock(side_effect=AssertionError("cached")))
    request_id = str(uuid4())
    new = client.post(f"/api/interviews/{identifier}/practice", json={"request_id": request_id}).json()
    assert new["mode"] == "group" and new["status"] == "prepared" and not new["turns"]
    assert new["group_scenario"] == ended["group_scenario"]
    assert new["group_practice_targets"]
    assert client.post(f"/api/interviews/{identifier}/practice", json={"request_id": request_id}).json()["id"] == new["id"]
    assert client.post(f"/api/interviews/{new['id']}/start").status_code == 200
    assert client.get(f"/api/interviews/{identifier}").json() == ended


def test_timeout_closes_without_scoring_unsubmitted_summary(client):
    identifier, _ = start_group(client)
    with client.app.state.session_factory() as db:
        session = db.get(InterviewSession, identifier)
        session.started_at = now() - timedelta(hours=1)
        db.commit()
    ended = say(client, identifier).json()
    assert ended["status"] == "completed" and ended["report"]["group_statistics"]["user_contributions"] == 1
    assert not ended["report"]["group_statistics"]["user_summary_submitted"]


def test_group_report_scores_only_user_evidence_and_observed_dimensions():
    result = sample_report()
    result.answer_reviews[0].issues = []
    answer = "我建议先确定共同标准。"
    context = {"mode": "group", "turns": [{"turn_index": 1, "answer": answer, "followup_depth": 0, "group_round": {"stage": "opening"}}]}
    result.scores = [Score(dimension="论证清晰度", score=6, evidence="AI自己的观点")]
    with pytest.raises(ValueError, match="用户实际answer"): validate_report(result, context)
    result.scores[0].evidence = answer
    validate_report(result, context)
    result.scores[0].dimension = "总结能力"
    with pytest.raises(ValueError, match="summary"): validate_report(result, context)
    result.scores[0].dimension = "简历熟悉度"
    with pytest.raises(ValueError, match="群面只能"): validate_report(result, context)


def test_group_validation_rejects_invented_members_and_old_speech_quotes(client, ai):
    identifier, _ = start_group(client)
    with client.app.state.session_factory() as db:
        context = client.app.state.interviews.context(db, db.get(InterviewSession, identifier))
    scenario = ai.generate_group_scenario(context, lambda _: None)
    scenario.fictional = False
    with pytest.raises(ValueError, match="虚构"): validate_scenario(scenario, context)
    context["turns"][0]["answer"] = "新的观点"
    context["state"]["turn_count"] = 1
    enriched = round_context(context)
    value = ai.generate_group_round(enriched, lambda _: None)
    value.messages[0].reply_to_quote = "旧的观点"
    with pytest.raises(ValueError, match="最新answer"): validate_round(value, enriched)
    value.messages[0].reply_to_quote = "新的观点"
    value.messages[0].member_id = "member_5"
    with pytest.raises(ValueError, match="其他成员"): validate_round(value, enriched)


def test_group_voice_uses_final_speech_for_peer_reply_and_retains_recording(client):
    from tests.test_voice import FakeVoice, upload, wav_bytes
    identifier, _ = start_group(client)
    client.app.state.voice.close()
    client.app.state.voice = FakeVoice()
    transcribed = upload(client, identifier)
    assert transcribed.status_code == 200
    final = "我建议先统一目标，再按依赖和风险安排资源。"
    value = say(client, identifier, answer=final).json()
    turn = value["turns"][0]
    assert turn["answer"] == final and turn["transcript_raw"] == transcribed.json()["text"]
    assert client.get(turn["audio_url"]).content == wav_bytes()
    assert value["turns"][1]["group_round"]["messages"][0]["reply_to_quote"] in final


def test_group_size_out_of_range_is_rejected_before_ai_calls(client):
    source = create_session(client)
    resume = client.get(f"/api/interviews/{source}").json()["resume_id"]
    for size in (2, 7):
        assert client.post("/api/interviews", json={"resume_id": resume, "company": "测试", "position": "测试", "mode": "group", "group_size": size}).status_code == 422

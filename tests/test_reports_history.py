from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from sqlalchemy import select

from app.models import InterviewReport, InterviewSession, InterviewTurn, Resume
from app.schemas import AnswerReview, Report, ReviewPoint, Score
from app.services.errors import ServiceError
from app.services.report_service import oral_analysis, validate_report
from tests.helpers import create_session, start_session


def answer(client, identifier, text="我负责数据清洗，并验证规则误报。"):
    result = client.post(f"/api/interviews/{identifier}/turn", json={"turn_index": 1, "answer": text})
    assert result.status_code == 200, result.text


def test_oral_statistics_use_raw_not_edited_and_exclude_unsubmitted():
    def turn(index, raw=None, duration=None, final="最终文字", audio=None):
        return SimpleNamespace(turn_index=index, answer=final, answer_transcript_raw=raw,
                               answer_duration=duration, audio_path=audio)
    result = oral_analysis([
        turn(1, "嗯嗯，我我负责 PCAP，然后验证。", 30, audio="a.wav"),
        turn(2, final="嗯，文字输入，不是语音"),
        turn(3, "呃，那个", 20, final=None, audio="b.wav"),
        turn(4, "其实验证过", None, audio="c.wav"),
    ])
    assert result["voice_answers"] == 2 and result["text_answers"] == 1
    assert result["timed_answers"] == 1 and result["total_duration_seconds"] == 30
    assert result["average_duration_seconds"] == 30
    assert result["filler_counts"] == {"嗯": 2, "呃": 0, "那个": 0, "就是": 0, "然后": 1, "其实": 1}
    assert result["turns"][0]["possible_repetitions"] == ["我我"]
    assert result["turns"][1]["filler_total"] is None
    assert result["turns"][2]["units_per_minute"] is None
    assert len(result["turns"]) == 3


def sample_report():
    return Report(overall_score=None, summary="样本不足", strengths=[], improvements=[], scores=[], limitations="仅供练习",
        answer_reviews=[AnswerReview(turn_index=1, summary="职责清晰", strengths=[], risks=[],
            issues=[ReviewPoint(observation="缺少验证标准", evidence="验证规则", suggestion="补充实际验证方式")],
            suggested_structure=["职责", "方法", "验证结果"], timing_advice="没有录音时长，无法判断")])


def test_duration_extrema_and_filler_rate_ignore_unknown_duration():
    turns = [SimpleNamespace(turn_index=index, answer="已提交", audio_path="audio.wav",
                             answer_transcript_raw=raw, answer_duration=duration)
             for index, raw, duration in [(1, "嗯嗯", 10), (2, "然后", 40), (3, "呃呃呃", None)]]
    result = oral_analysis(turns)
    assert result["total_duration_seconds"] == 50 and result["average_duration_seconds"] == 25
    assert result["longest_answer"] == 2 and result["longest_duration_seconds"] == 40
    assert result["shortest_answer"] == 1 and result["shortest_duration_seconds"] == 10
    assert result["fillers_per_minute"] == 3.6  # Only the 3 fillers in timed recordings.
    assert result["turns"][0]["fillers_per_minute"] == 12
    assert result["turns"][2]["fillers_per_minute"] is None


@pytest.mark.parametrize("bad", ["missing", "duplicate", "unanswered", "made_up"])
def test_review_rejects_missing_duplicate_unanswered_and_fabricated_evidence(bad):
    result = sample_report()
    context = {"turns": [{"turn_index": 1, "answer": "我负责验证规则。"}, {"turn_index": 2, "answer": None}]}
    validate_report(result, context)
    if bad == "missing": result.answer_reviews = []
    if bad == "duplicate": result.answer_reviews *= 2
    if bad == "unanswered": result.answer_reviews[0].turn_index = 2
    if bad == "made_up": result.answer_reviews[0].issues[0].evidence = "准确率达到99%"
    with pytest.raises(ValueError): validate_report(result, context)


def test_detailed_report_covers_answered_turns_and_is_cached(client, ai, monkeypatch):
    identifier, _ = start_session(client)
    answer(client, identifier)
    spy = Mock(wraps=ai.generate_report)
    monkeypatch.setattr(ai, "generate_report", spy)
    report = client.post(f"/api/interviews/{identifier}/finish").json()["report"]
    assert report["version"] == 3 and [r["turn_index"] for r in report["answer_reviews"]] == [1]
    assert report["oral_analysis"]["text_answers"] == 1
    assert report["oral_analysis"]["voice_answers"] == 0
    client.post(f"/api/interviews/{identifier}/report/upgrade")
    client.post(f"/api/interviews/{identifier}/finish")
    assert spy.call_count == 1


def test_unanswered_followup_cannot_be_scored():
    result = sample_report()
    result.scores = [Score(dimension="追问应对", score=6, evidence="我负责验证规则。")]
    context = {"turns": [{"turn_index": 1, "answer": "我负责验证规则。", "followup_depth": 0},
                         {"turn_index": 2, "answer": None, "followup_depth": 1}]}
    with pytest.raises(ValueError, match="没有已作答"): validate_report(result, context)
    context["turns"][0]["followup_depth"] = 1
    validate_report(result, context)


def test_knowledge_gaps_need_submitted_answer_evidence():
    from app.schemas import KnowledgeGap, TurnEvidence
    result = sample_report()
    gap = KnowledgeGap(topic="验证方式", priority="high", reason="未说明标准",
                       evidence=[TurnEvidence(turn_index=1, quote="验证规则")], review_suggestions=["复习误报率定义"])
    result.knowledge_gaps = [gap]
    context = {"turns": [{"turn_index": 1, "answer": "我负责验证规则。"}]}
    validate_report(result, context)
    gap.evidence[0].turn_index = 2
    with pytest.raises(ValueError, match="知识薄弱点"): validate_report(result, context)


def test_voice_stats_persist_and_do_not_use_edited_fillers(client):
    identifier, _ = start_session(client)
    with client.app.state.session_factory() as db:
        turn = db.scalar(select(InterviewTurn).where(InterviewTurn.session_id == identifier))
        turn.audio_path = "stats-only.wav"
        turn.answer_transcript_raw = "嗯，我负责清洗，然后验证规则。"
        turn.answer_duration = 60
        db.commit()
    answer(client, identifier, "我负责清洗并验证规则。")
    report = client.post(f"/api/interviews/{identifier}/finish").json()["report"]
    assert report["oral_analysis"]["filler_counts"]["嗯"] == 1
    assert report["oral_analysis"]["filler_counts"]["然后"] == 1
    assert client.get(f"/api/interviews/{identifier}/report").json() == report


def test_old_report_upgrade_retains_old_on_failure(client, ai, monkeypatch):
    identifier, _ = start_session(client)
    answer(client, identifier)
    client.post(f"/api/interviews/{identifier}/finish")
    with client.app.state.session_factory() as db:
        stored = db.scalar(select(InterviewReport).where(InterviewReport.session_id == identifier))
        old = {k: v for k, v in stored.report_json.items() if k not in {"version", "oral_analysis", "answer_reviews", "priority_practice"}}
        stored.report_json = old
        db.commit()
    original = ai.generate_report
    spy = Mock(side_effect=ServiceError("模拟失败"))
    monkeypatch.setattr(ai, "generate_report", spy)
    assert client.get(f"/api/interviews/{identifier}/report").json()["summary"] == old["summary"]
    assert spy.call_count == 0  # Reading old reports never calls AI.
    assert client.post(f"/api/interviews/{identifier}/report/upgrade").status_code == 502
    assert client.get(f"/api/interviews/{identifier}").json()["report"]["summary"] == old["summary"]
    monkeypatch.setattr(ai, "generate_report", original)
    assert client.post(f"/api/interviews/{identifier}/report/upgrade").json()["report"]["version"] == 3


def test_history_pagination_status_and_read_with_different_provider(client):
    older, _ = start_session(client)
    answer(client, older)
    client.post(f"/api/interviews/{older}/finish")
    newer = create_session(client)
    first = client.get("/api/interviews?limit=1").json()
    assert first["total"] == 2 and first["items"][0]["id"] == newer
    second = client.get("/api/interviews?limit=1&offset=1").json()["items"][0]
    assert second["id"] == older and second["answered_count"] == 1 and second["has_report"]
    assert second["created_at"].endswith("+00:00")
    client.post("/api/settings", json={"provider": "deepseek"})
    assert client.get(f"/api/interviews/{older}").status_code == 200
    assert client.get("/api/interviews").json()["total"] == 2
    assert client.get("/api/interviews?limit=0").status_code == 422
    assert client.get("/api/interviews?offset=-1").status_code == 422


def test_delete_cleans_all_recordings_resume_cache_and_rows(client, settings):
    identifier, _ = start_session(client)
    answer(client, identifier)
    client.post(f"/api/interviews/{identifier}/finish")
    root = settings.data_dir / "interviews" / identifier
    (root / "audio").mkdir(parents=True)
    for name in ("old-attempt.wav", "latest.wav"): (root / "audio" / name).write_bytes(b"test")
    with client.app.state.session_factory() as db:
        session = db.get(InterviewSession, identifier)
        resume_id = session.resume_id
        resume_path = Path(db.get(Resume, resume_id).file_path)
    sentinel = settings.data_dir / "voice_settings.json"
    sentinel.write_text("{}", encoding="utf-8")
    other = create_session(client)
    result = client.delete(f"/api/interviews/{identifier}")
    assert result.json() == {"deleted": True, "resume_deleted": True}
    assert not root.exists() and not resume_path.exists() and sentinel.exists()
    assert client.get(f"/api/interviews/{identifier}").status_code == 404
    assert client.get(f"/api/interviews/{identifier}/audio/1").status_code == 404
    assert client.get(f"/api/interviews/{other}").status_code == 200
    with client.app.state.session_factory() as db:
        assert db.get(Resume, resume_id) is None
        assert not db.scalar(select(InterviewTurn).where(InterviewTurn.session_id == identifier))
        assert not db.scalar(select(InterviewReport).where(InterviewReport.session_id == identifier))


def test_delete_keeps_shared_resume_until_last_session(client):
    first = create_session(client)
    resume_id = client.get(f"/api/interviews/{first}").json()["resume_id"]
    second = client.post("/api/interviews", json={"resume_id": resume_id, "company": "另一公司", "position": "开发"}).json()["id"]
    assert client.delete(f"/api/interviews/{first}").json()["resume_deleted"] is False
    assert client.post(f"/api/resumes/{resume_id}/parse").status_code == 200
    assert client.delete(f"/api/interviews/{second}").json()["resume_deleted"] is True
    assert client.post(f"/api/resumes/{resume_id}/parse").status_code == 404


def test_delete_rejects_outside_file_before_any_cleanup(client, settings, tmp_path):
    identifier, _ = start_session(client)
    outside = tmp_path / "outside.wav"
    outside.write_bytes(b"keep")
    directory = settings.data_dir / "interviews" / identifier
    directory.mkdir(parents=True)
    own = directory / "own.wav"; own.write_bytes(b"keep")
    with client.app.state.session_factory() as db:
        turn = db.scalar(select(InterviewTurn).where(InterviewTurn.session_id == identifier))
        turn.audio_path = str(outside)
        db.commit()
    assert client.delete(f"/api/interviews/{identifier}").status_code == 409
    assert outside.exists() and own.exists()
    assert client.get(f"/api/interviews/{identifier}").status_code == 200


def test_delete_file_failure_keeps_record_for_retry(client, settings, monkeypatch):
    identifier = create_session(client)
    directory = settings.data_dir / "interviews" / identifier
    directory.mkdir(parents=True)
    import app.services.interview_service as module
    original = module.shutil.rmtree
    monkeypatch.setattr(module.shutil, "rmtree", Mock(side_effect=PermissionError("in use")))
    assert client.delete(f"/api/interviews/{identifier}").status_code == 409
    assert client.get(f"/api/interviews/{identifier}").status_code == 200
    monkeypatch.setattr(module.shutil, "rmtree", original)
    assert client.delete(f"/api/interviews/{identifier}").status_code == 200

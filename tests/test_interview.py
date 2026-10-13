from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.config import Settings
from app.main import create_app
from app.models import InterviewSession, InterviewTurn, Resume, now
from app.services.errors import ServiceError
from tests.helpers import create_session, sample_docx, sample_pdf, start_session


def submit(client, identifier, turn_index, answer):
    return client.post(f"/api/interviews/{identifier}/turn", json={"turn_index": turn_index, "answer": answer})


def test_text_loop_and_persistence(client, settings):
    identifier, session = start_session(client)
    assert session["turns"][0]["question"].startswith("请先")
    answer = "我研究加密流量分析，使用 Suricata 规则，负责 PCAP 分析。"
    response = submit(client, identifier, 1, answer)
    assert response.status_code == 200
    session = response.json()
    assert "Suricata" in session["turns"][-1]["question"]
    assert session["turns"][-1]["action"] == "follow_up"
    assert "decision_note" not in response.text
    finished = client.post(f"/api/interviews/{identifier}/finish").json()
    assert finished["status"] == "completed"
    assert finished["report"]["overall_score"] is None
    assert "1 次" in finished["report"]["summary"]
    assert client.get(f"/api/interviews/{identifier}/report").status_code == 200
    # A new application/database connection recovers the same full record.
    with TestClient(create_app(settings)) as restarted:
        assert restarted.get(f"/api/interviews/{identifier}").json() == finished


def test_answer_changes_followup(client):
    first_id, _ = start_session(client)
    second_id, _ = start_session(client)
    first = submit(client, first_id, 1, "我们通过 Suricata 规则进行检测。").json()
    second = submit(client, second_id, 1, "我们在实验中使用包长和 IAT 特征。").json()
    assert "Suricata" in first["turns"][-1]["question"]
    assert "IAT" in second["turns"][-1]["question"]
    assert first["turns"][-1]["question"] != second["turns"][-1]["question"]


def test_depth_cap_and_program_turn_limit(client):
    identifier, session = start_session(client, duration=10)
    while session["status"] == "active":
        session = submit(client, identifier, session["current_turn_index"], "我们使用 Suricata、包长和 IAT 做检测。").json()
        assert len(session["turns"]) <= 7
    turns = session["turns"]
    assert session["report"]
    assert max(turn["followup_depth"] for turn in turns) <= 3
    for previous, current in zip(turns, turns[1:]):
        if previous["followup_depth"] == 3:
            assert current["topic"] != previous["topic"]
            assert current["followup_depth"] == 0


def test_api_failure_saves_answer_and_retry_is_idempotent(client, ai, monkeypatch):
    identifier, _ = start_session(client)
    original = ai.generate_question
    monkeypatch.setattr(ai, "generate_question", lambda *_: (_ for _ in ()).throw(ServiceError("模拟 API 失败")))
    payload = "我负责 Suricata 规则验证。"
    assert submit(client, identifier, 1, payload).status_code == 502
    saved = client.get(f"/api/interviews/{identifier}").json()
    assert saved["needs_next_question"] is True
    assert saved["turns"][0]["answer"] == payload
    monkeypatch.setattr(ai, "generate_question", original)
    success = submit(client, identifier, 1, payload)
    assert success.status_code == 200
    assert len(success.json()["turns"]) == 2
    assert submit(client, identifier, 1, payload).json() == success.json()
    assert submit(client, identifier, 1, "不同回答").status_code == 409


def test_research_persists_when_strategy_fails(client, ai, monkeypatch):
    identifier = create_session(client)
    original = ai.generate_strategy
    monkeypatch.setattr(ai, "generate_strategy", lambda *_: (_ for _ in ()).throw(ServiceError("策略失败")))
    assert client.post(f"/api/interviews/{identifier}/prepare").status_code == 502
    with client.app.state.session_factory() as db:
        session = db.get(InterviewSession, identifier)
        assert session.status == "researched" and session.research_json
    monkeypatch.setattr(ai, "generate_strategy", original)
    monkeypatch.setattr(ai, "research_interview", lambda *_: pytest.fail("should reuse saved research"))
    assert client.post(f"/api/interviews/{identifier}/prepare").json()["status"] == "prepared"


def test_report_failure_preserves_completion_and_can_retry(client, ai, monkeypatch):
    identifier, _ = start_session(client)
    assert submit(client, identifier, 1, "我负责 PCAP 特征提取。").status_code == 200
    original = ai.generate_report
    monkeypatch.setattr(ai, "generate_report", lambda *_: (_ for _ in ()).throw(ServiceError("报告失败")))
    assert client.post(f"/api/interviews/{identifier}/finish").status_code == 502
    saved = client.get(f"/api/interviews/{identifier}").json()
    assert saved["status"] == "completed" and saved["report"] is None
    assert saved["turns"][0]["answer"]
    monkeypatch.setattr(ai, "generate_report", original)
    finish = client.post(f"/api/interviews/{identifier}/finish").json()
    assert finish["report"]
    assert client.post(f"/api/interviews/{identifier}/finish").json() == finish


def test_elapsed_limit_ends_without_asking_model(client, ai, monkeypatch):
    identifier, _ = start_session(client)
    with client.app.state.session_factory() as db:
        session = db.get(InterviewSession, identifier)
        session.started_at = now() - timedelta(minutes=23)
        db.commit()
    monkeypatch.setattr(ai, "generate_question", lambda *_: pytest.fail("hard time limit must bypass model"))
    result = submit(client, identifier, 1, "我负责 PCAP 分析。").json()
    assert result["status"] == "completed" and result["report"]
    assert len(result["turns"]) == 1


def test_no_answer_cannot_receive_fake_score(client):
    identifier, _ = start_session(client)
    report = client.post(f"/api/interviews/{identifier}/finish").json()["report"]
    assert report["overall_score"] is None and report["scores"] == []


def test_invalid_states_and_answers(client):
    identifier = create_session(client)
    assert client.post(f"/api/interviews/{identifier}/start").status_code == 409
    assert client.post(f"/api/interviews/{identifier}/finish").status_code == 409
    assert client.get("/api/interviews/missing").status_code == 404
    client.post(f"/api/interviews/{identifier}/prepare")
    first = client.post(f"/api/interviews/{identifier}/start").json()
    assert client.post(f"/api/interviews/{identifier}/start").json() == first
    assert submit(client, identifier, 1, "  ").status_code == 422
    assert submit(client, identifier, 99, "有效回答").status_code == 409
    with client.app.state.session_factory() as db:
        assert len(list(db.scalars(select(InterviewTurn)))) == 1


@pytest.mark.parametrize("filename,content", [("bad.exe", b"hello"), ("bad.doc", b"hello"), ("bad.pdf", b"not a pdf"), ("bad.docx", b"not a docx"), ("empty.pdf", b"")])
def test_invalid_upload(client, filename, content):
    assert client.post("/api/resumes", files={"file": (filename, content)}).status_code == 400
    with client.app.state.session_factory() as db:
        assert not list(db.scalars(select(Resume)))


def test_upload_size_limit(client):
    assert client.post("/api/resumes", files={"file": ("large.pdf", b"a" * (10 * 1024 * 1024 + 1))}).status_code == 413


def test_filename_cannot_escape_data_directory(client, settings):
    upload = client.post("/api/resumes", files={"file": ("../../outside.docx", sample_docx())})
    assert upload.status_code == 201
    with client.app.state.session_factory() as db:
        resume = db.get(Resume, upload.json()["id"])
        assert resume.filename == "outside.docx"
        assert __import__("pathlib").Path(resume.file_path).parent == settings.data_dir / "resumes"


def test_no_api_key_is_explicit_and_does_not_fallback(tmp_path):
    with TestClient(create_app(Settings(data_dir=tmp_path, ai_mode="openai"))) as client:
        upload = client.post("/api/resumes", files={"file": ("resume.docx", sample_docx())}).json()
        response = client.post(f"/api/resumes/{upload['id']}/parse")
        assert response.status_code == 503
        assert "OPENAI_API_KEY" in response.json()["error"]
        assert client.get("/api/health").json()["ai_ready"] is False


def test_provider_mode_cannot_be_mixed(client, settings):
    identifier, _ = start_session(client)
    with TestClient(create_app(Settings(data_dir=settings.data_dir, ai_mode="openai"))) as live:
        assert live.get(f"/api/interviews/{identifier}").status_code == 200
        assert live.post(f"/api/interviews/{identifier}/finish").status_code == 409


def test_cross_site_write_rejected(client):
    assert client.post("/api/resumes", files={"file": ("resume.docx", sample_docx())}, headers={"Origin": "https://example.com"}).status_code == 403


def test_pdf_upload_and_parse(client):
    upload = client.post("/api/resumes", files={"file": ("resume.pdf", sample_pdf())})
    assert upload.status_code == 201
    parsed = client.post(f"/api/resumes/{upload.json()['id']}/parse")
    assert parsed.status_code == 200
    assert "Suricata" in parsed.json()["parsed_resume"]["skills"]


def test_encrypted_pdf_rejected(client):
    response = client.post("/api/resumes", files={"file": ("locked.pdf", sample_pdf(encrypted=True))})
    assert response.status_code == 400
    assert "密码" in response.json()["error"]


def test_demo_empty_scan_is_explicit(client):
    upload = client.post("/api/resumes", files={"file": ("scan.pdf", sample_pdf(blank=True))})
    assert upload.status_code == 201
    parsed = client.post(f"/api/resumes/{upload.json()['id']}/parse")
    assert parsed.status_code == 400
    assert "扫描" in parsed.json()["error"]


def test_concurrent_double_submit_creates_one_next_question(client):
    from concurrent.futures import ThreadPoolExecutor
    identifier, _ = start_session(client)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: submit(client, identifier, 1, "我负责 Suricata 规则。"), range(2)))
    assert all(response.status_code == 200 for response in results)
    assert results[0].json() == results[1].json()
    assert len(client.get(f"/api/interviews/{identifier}").json()["turns"]) == 2

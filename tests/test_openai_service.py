import json

import httpx
import pytest
from openai import OpenAI

from app.config import Settings
from app.schemas import Question, Research
from app.services.errors import ServiceError
from app.services.interview_service import InterviewService
from app.services.openai_service import OpenAIService


def api_response(text, extra_output=None, annotations=None):
    return {
        "id": "resp_test", "object": "response", "created_at": 1,
        "model": "test-model", "status": "completed", "error": None,
        "output": (extra_output or []) + [{"type": "message", "id": "msg_test", "role": "assistant", "status": "completed", "content": [
            {"type": "output_text", "text": text, "annotations": annotations or []},
        ]}],
    }


@pytest.fixture
def build_service(tmp_path):
    services = []
    def build(handler):
        service = OpenAIService(Settings(data_dir=tmp_path, main_model="test-model"))
        service.client = OpenAI(api_key="test-only", max_retries=0, http_client=httpx.Client(transport=httpx.MockTransport(handler)))
        services.append(service)
        return service
    yield build
    for service in services:
        service.close()


def test_sdk_structured_request_and_invalid_json_retry(build_service):
    requests = []
    value = Question(action="new_topic", question="请做自我介绍。", topic="self_intro", phase="self_intro", followup_depth=0, decision_note="开场")
    def handler(request):
        requests.append(json.loads(request.content))
        return httpx.Response(200, json=api_response("invalid JSON" if len(requests) == 1 else value.model_dump_json()))
    service = build_service(handler)
    result = service.generate_question({"turns": []}, lambda _: None)
    assert result.question == value.question and len(requests) == 2
    assert requests[0]["text"]["format"]["type"] == "json_schema"
    assert requests[0]["text"]["format"]["strict"] is True
    assert requests[0]["store"] is False
    assert requests[0]["model"] == "test-model"


def test_twice_invalid_json_returns_friendly_error(build_service):
    count = 0
    def handler(request):
        nonlocal count
        count += 1
        return httpx.Response(200, json=api_response("not JSON"))
    with pytest.raises(ServiceError, match="自动重试"):
        build_service(handler).generate_question({}, lambda _: None)
    assert count == 2


def test_report_retries_fabricated_quote_with_feedback(build_service):
    from tests.test_reports_history import sample_report
    requests = []
    def handler(request):
        requests.append(json.loads(request.content))
        result = sample_report()
        if len(requests) == 1:
            result.answer_reviews[0].issues[0].evidence = "编造引用"
        return httpx.Response(200, json=api_response(result.model_dump_json()))
    report = build_service(handler).generate_report({"turns": [{"turn_index": 1, "answer": "我负责验证规则。"}]})
    assert len(requests) == 2
    assert "逐字摘录" in requests[1]["instructions"]
    assert report.answer_reviews[0].issues[0].evidence == "验证规则"


def test_training_feedback_and_next_question_use_one_mocked_request(build_service):
    from tests.test_reports_history import sample_report
    from app.schemas import TrainingStep
    from app.services.report_service import validate_answer_review
    requests = []
    question = Question(action="follow_up", question="请说明验证标准。", topic="validation", phase="projects", followup_depth=1, decision_note="专项追问")
    step = TrainingStep(feedback=sample_report().answer_reviews[0], next_question=question)
    def handler(request):
        requests.append(json.loads(request.content))
        return httpx.Response(200, json=api_response(step.model_dump_json()))
    result = build_service(handler).generate_training_step({"mode": "training", "turns": [{"turn_index": 1, "answer": "我负责验证规则。"}]},
        lambda value: validate_answer_review(value.feedback, {1: "我负责验证规则。"}))
    assert len(requests) == 1 and result.feedback.turn_index == 1 and result.next_question.question
    assert requests[0]["text"]["format"]["name"] == "TrainingStep"


def test_training_rejects_feedback_for_previous_turn_and_retries_locally(build_service):
    from tests.test_reports_history import sample_report
    from app.schemas import TrainingStep
    from app.services.report_service import validate_answer_review
    requests = []
    question = Question(action="follow_up", question="请说明验证标准。", topic="validation", phase="projects", followup_depth=1, decision_note="专项追问")
    def handler(request):
        requests.append(json.loads(request.content))
        feedback = sample_report().answer_reviews[0]
        feedback.turn_index = 1 if len(requests) == 1 else 2
        return httpx.Response(200, json=api_response(TrainingStep(feedback=feedback, next_question=question).model_dump_json()))
    result = build_service(handler).generate_training_step({"mode": "training", "turns": [{"turn_index": 2, "answer": "我负责验证规则。"}]},
        lambda value: validate_answer_review(value.feedback, {2: "我负责验证规则。"}))
    assert result.feedback.turn_index == 2 and len(requests) == 2
    assert "讲评题号" in requests[1]["instructions"]


def test_group_multi_member_round_is_one_mocked_request(build_service):
    from app.schemas import GroupMessage, GroupRound
    from app.services.group_service import validate_round
    requests = []
    value = GroupRound(stage="discussion", messages=[
        GroupMessage(member_id="member_1", speech="先比较风险再排序。", reply_to_quote="我建议先比较风险"),
        GroupMessage(member_id="member_2", speech="也要考虑实施成本。", reply_to_quote=None)],
        user_prompt="请回应不同的选择标准。", discussion_summary="标准仍有分歧。")
    context = {"group_stage_target": "discussion", "group_speakers": ["member_1", "member_2"],
               "turns": [{"answer": "我建议先比较风险，再评估成本。"}]}
    def handler(request):
        requests.append(json.loads(request.content))
        return httpx.Response(200, json=api_response(value.model_dump_json()))
    result = build_service(handler).generate_group_round(context, lambda output: validate_round(output, context))
    assert len(requests) == 1 and len(result.messages) == 2
    assert requests[0]["text"]["format"]["name"] == "GroupRound"


def test_search_is_required_and_sources_come_from_api(build_service):
    requests = []
    research = Research(company_summary="公司资料", position_summary="岗位资料", interview_process=[], common_question_types=[], technical_topics=[], company_specific_topics=[], behavioral_topics=[], uncertainties=[], sources=[])
    def handler(request):
        payload = json.loads(request.content); requests.append(payload)
        if "tools" in payload:
            return httpx.Response(200, json=api_response("研究结果 [1]", extra_output=[{
                "type": "web_search_call", "id": "ws_test", "status": "completed",
                "action": {"type": "search", "query": "中国银行 信息科技岗", "sources": [{"type": "url", "url": "https://www.boc.cn/"}]},
            }], annotations=[{"type": "url_citation", "start_index": 5, "end_index": 8, "url": "https://www.boc.cn/", "title": "中国银行官网"}]))
        return httpx.Response(200, json=api_response(research.model_dump_json()))
    service = build_service(handler)
    result = service.research_interview({"company": "中国银行", "position": "信息科技岗", "interview_type": "半结构化面试", "resume": "private resume"})
    assert requests[0]["tool_choice"] == "required"
    assert requests[0]["tools"] == [{"type": "web_search"}]
    assert "private resume" not in requests[0]["input"]
    assert result.sources[0].title == "中国银行官网"
    assert result.sources[0].url == "https://www.boc.cn/"


def test_missing_search_sources_not_silently_accepted(build_service):
    service = build_service(lambda request: httpx.Response(200, json=api_response("无引用资料")))
    with pytest.raises(ServiceError, match="来源"):
        service.research_interview({"company": "公司", "position": "岗位", "interview_type": "技术面"})


def test_file_input_uses_current_sdk_contract(build_service, tmp_path):
    from app.services.demo_service import DemoService
    requests = []
    parsed = DemoService().parse_resume(None, "博士\n项目：测试项目\n技能：Python")
    def handler(request):
        requests.append(json.loads(request.content))
        return httpx.Response(200, json=api_response(parsed.model_dump_json()))
    path = tmp_path / "resume.pdf"; path.write_bytes(b"%PDF-sample")
    build_service(handler).parse_resume(path, "")
    part = requests[0]["input"][0]["content"][0]
    assert part["type"] == "input_file" and part["filename"] == "resume.pdf"
    assert part["file_data"].startswith("data:application/pdf;base64,")
    assert requests[0]["store"] is False


def test_api_error_is_sanitized(build_service):
    service = build_service(lambda request: httpx.Response(401, json={"error": {"message": "sensitive upstream value", "type": "authentication_error"}}))
    with pytest.raises(ServiceError) as raised:
        service.generate_question({}, lambda _: None)
    assert raised.value.status_code == 503
    assert "sensitive" not in raised.value.message


def test_invalid_decision_retry_includes_constraint_feedback(build_service):
    requests = []
    context = {"turns": [{"question": "请介绍自己", "topic": "self_intro", "followup_depth": 0}], "constraints": {"must_end": False, "must_switch_topic": False}}
    wrong = Question(action="follow_up", question="请说明 PCAP 分析方法。", topic="pcap", phase="projects", followup_depth=1, decision_note="核实")
    correct = wrong.model_copy(update={"topic": "self_intro"})
    def handler(request):
        requests.append(json.loads(request.content))
        value = wrong if len(requests) == 1 else correct
        return httpx.Response(200, json=api_response(value.model_dump_json()))
    service = build_service(handler)
    result = service.generate_question(context, lambda value: InterviewService.validate_question(value, context))
    assert result.topic == "self_intro"
    assert len(requests) == 2
    assert "上一次校验失败" in requests[1]["instructions"]
    assert "必须逐字保留" in requests[1]["instructions"]


@pytest.mark.parametrize("changes", [
    {"action": "follow_up", "followup_depth": 3},
    {"action": "new_topic", "topic": "project", "followup_depth": 0},
    {"action": "new_topic", "followup_depth": 1},
    {"question": "已有问题"},
])
def test_invalid_question_constraints_rejected(changes):
    context = {"turns": [{"topic": "project", "question": "已有问题", "followup_depth": 3}], "constraints": {"must_end": False, "must_switch_topic": True}}
    values = {"action": "new_topic", "question": "新问题", "topic": "technical", "phase": "technical", "followup_depth": 0, "decision_note": "切换"}
    with pytest.raises(ValueError):
        InterviewService.validate_question(Question(**(values | changes)), context)

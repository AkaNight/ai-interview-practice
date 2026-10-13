import base64
import json
import httpx
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

from openai import APIError, APITimeoutError, AuthenticationError, OpenAI, RateLimitError
from pydantic import ValidationError

from app.prompts import group, interview_research, interview_strategy, interviewer, report, resume_parser, training
from app.schemas import GroupRound, GroupScenario, ParsedResume, Question, Report, Research, Source, Strategy, TrainingStep
from app.services.errors import ServiceError
from app.services.report_service import validate_report


class OpenAIService:
    def __init__(self, settings, usage=None):
        self.settings = settings
        from app.services.usage_service import MeteredTransport
        http_client = httpx.Client(transport=MeteredTransport(usage, "ai", settings.ai_mode,
            settings.resolved_base_url, settings.main_model)) if usage and settings.api_key else None
        self.client = OpenAI(
            api_key=settings.api_key, timeout=settings.api_timeout_seconds, max_retries=1,
            base_url=settings.resolved_base_url,
            http_client=http_client,
        ) if settings.api_key else None

    def close(self):
        if self.client:
            self.client.close()

    def require_client(self):
        if not self.client:
            raise ServiceError("尚未配置 API Key（OPENAI_API_KEY / DEEPSEEK_API_KEY），请打开本地 API 设置填写，或选择离线演示。", 503)

    def _call(self, callback):
        self.require_client()
        try:
            return callback()
        except AuthenticationError as exc:
            raise ServiceError("API 身份验证失败，请检查 API 设置中的密钥。", 503) from exc
        except RateLimitError as exc:
            raise ServiceError("API 额度不足或请求过快，请检查额度后重试。", 503) from exc
        except APITimeoutError as exc:
            raise ServiceError("AI 请求超时，已保存的面试数据仍在，请重试。", 504) from exc
        except APIError as exc:
            raise ServiceError("AI API 请求失败，请检查网络、MAIN_MODEL 及模型对所需功能的支持后重试。", 502) from exc

    def _structured(self, schema, prompt, data=None, content=None, validate=None):
        user_content = content if content is not None else json.dumps(data, ensure_ascii=False)
        retry_hint = ""
        for attempt in range(2):
            try:
                response = self._call(lambda: self.client.responses.parse(
                    model=self.settings.main_model,
                    instructions=prompt + retry_hint,
                    input=[{"role": "user", "content": user_content}],
                    text_format=schema,
                    store=False,
                ))
                result = response.output_parsed
                if result is None or response.status != "completed":
                    raise ValueError("no completed structured output")
                result = schema.model_validate(result)
                if validate:
                    validate(result)
                return result
            except (ValidationError, ValueError) as exc:
                if attempt:
                    raise ServiceError("AI 未返回有效的结构化结果，自动重试后仍失败。已保存数据未丢失，请重试。")
                # Never echo candidate text from Pydantic validation errors.
                reason = "JSON 字段或类型不符合指定结构。" if isinstance(exc, ValidationError) else str(exc)[:180]
                retry_hint = "\n上一次校验失败：" + reason + "。请重新生成，严格遵守结构和约束。"
        raise AssertionError("unreachable")

    def parse_resume(self, path, text):
        suffix = Path(path).suffix.lower()
        mime = "application/pdf" if suffix == ".pdf" else "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        content = [
            {"type": "input_file", "filename": Path(path).name,
             "file_data": f"data:{mime};base64,{base64.b64encode(Path(path).read_bytes()).decode()}"},
            {"type": "input_text", "text": "提取简历结构与需要核实的点。"},
        ]
        return self._structured(ParsedResume, resume_parser.PROMPT, content=content)

    def research_interview(self, context):
        # No resume or conversation is sent to the web search step.
        search_context = {key: context[key] for key in ("company", "position", "interview_type")}
        search_context["research_date_utc"] = datetime.now(timezone.utc).date().isoformat()
        response = self._call(lambda: self.client.responses.create(
            model=self.settings.main_model,
            instructions=interview_research.SEARCH_PROMPT,
            input=json.dumps(search_context, ensure_ascii=False),
            tools=[{"type": "web_search"}],
            tool_choice="required",
            include=["web_search_call.action.sources"],
            store=False,
        ))
        sources = {}
        searched = False
        for item in response.model_dump().get("output", []):
            if item.get("type") == "web_search_call":
                searched = True
                for source in (item.get("action") or {}).get("sources", []):
                    self._add_source(sources, source)
            for content in item.get("content", []):
                for annotation in content.get("annotations", []):
                    if annotation.get("type") == "url_citation":
                        self._add_source(sources, annotation)
        if response.status != "completed" or not searched or not sources or not response.output_text:
            raise ServiceError("联网研究没有返回可核实的来源，请重试；不会用演示资料替代真实搜索。")
        result = self._structured(Research, interview_research.STRUCTURE_PROMPT, {
            "research_text": response.output_text, "verified_sources": list(sources.values()),
        })
        # Only the API's citations/tool sources may be persisted as sources.
        result.sources = [Source(**source) for source in sources.values()]
        return result

    @staticmethod
    def _add_source(sources, source):
        url = source.get("url", "")
        if urlparse(url).scheme in {"http", "https"}:
            sources[url] = {"url": url, "title": source.get("title") or urlparse(url).netloc}

    def generate_strategy(self, context):
        return self._structured(Strategy, interview_strategy.PROMPT, context)

    def generate_question(self, context, validate):
        prompt = interviewer.PROMPT
        if context.get("turns"):
            previous = context["turns"][-1]
            prompt += f"\n如选择 follow_up/deepen/clarify：topic 必须为 {json.dumps(previous['topic'], ensure_ascii=False)}，followup_depth 必须为 {previous['followup_depth'] + 1}。若该深度超过3，请换话题或结束。"
        return self._structured(Question, prompt, context, validate=validate)

    def generate_report(self, context):
        return self._structured(Report, report.PROMPT, context, validate=lambda result: validate_report(result, context))

    def generate_training_step(self, context, validate):
        return self._structured(TrainingStep, training.PROMPT, context, validate=validate)

    def generate_group_scenario(self, context, validate):
        return self._structured(GroupScenario, group.SCENARIO_PROMPT, context, validate=validate)

    def generate_group_round(self, context, validate):
        return self._structured(GroupRound, group.ROUND_PROMPT, context, validate=validate)

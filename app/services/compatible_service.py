import json

from pydantic import ValidationError

from app.prompts import interview_research, resume_parser
from app.schemas import ParsedResume, Research, Source
from app.services.errors import ServiceError
from app.services.openai_service import OpenAIService
from app.services.search_service import SearchService


class CompatibleService(OpenAIService):
    """DeepSeek and user-configured Chat Completions compatible providers."""

    def __init__(self, settings, search=None, usage=None):
        super().__init__(settings, usage=usage)
        self.search = search or SearchService()

    def _structured(self, schema, prompt, data=None, content=None, validate=None):
        schema_json = json.dumps(schema.model_json_schema(), ensure_ascii=False)
        data_json = json.dumps(data, ensure_ascii=False)
        retry_hint = ""
        for attempt in range(2):
            try:
                instructions = prompt + "\n只输出一个 JSON 对象，字段和类型必须符合下列 JSON Schema；缺失信息使用空值。\n" + schema_json
                if attempt:
                    instructions += retry_hint
                kwargs = {"extra_body": {"thinking": {"type": "disabled"}}} if self.settings.ai_mode == "deepseek" else {}
                response = self._call(lambda: self.client.chat.completions.create(
                    model=self.settings.main_model,
                    messages=[{"role": "system", "content": instructions}, {"role": "user", "content": data_json}],
                    response_format={"type": "json_object"}, max_tokens=6000, **kwargs,
                ))
                if not response.choices or response.choices[0].finish_reason != "stop":
                    raise ValueError("incomplete generation")
                result = schema.model_validate_json(response.choices[0].message.content or "")
                if validate:
                    validate(result)
                return result
            except (ValidationError, ValueError) as exc:
                if attempt:
                    raise ServiceError("AI 返回的数据结构或面试决策无效，重试一次后仍失败；已保存的数据仍可恢复。")
                reason = "JSON 字段或类型不符合指定结构。" if isinstance(exc, ValidationError) else str(exc)[:180]
                retry_hint = "\n上次校验失败：" + reason + "。请重新输出符合结构和约束的 JSON。"
        raise AssertionError("unreachable")

    def parse_resume(self, path, text):
        if not text.strip():
            raise ServiceError("当前接口需要可提取文字的 PDF/DOCX。扫描 PDF 请先做文字识别，或切换到支持文件输入的 OpenAI 模式。", 400)
        return self._structured(ParsedResume, resume_parser.PROMPT, {"resume_text": text})

    def research_interview(self, context):
        self.require_client()
        research = self.search.research(context)
        result = self._structured(Research, interview_research.STRUCTURE_PROMPT + "\n输入仅为搜索摘要，不视作全文。必须保留 limitations 的限制。", {
            "company": context["company"], "position": context["position"], **research,
        })
        result.sources = [Source(title=row["title"], url=row["url"]) for row in research["results"]]
        result.uncertainties = list(dict.fromkeys(result.uncertainties + research["limitations"]))
        return result

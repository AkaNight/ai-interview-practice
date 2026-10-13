"""Persist metadata at the HTTP boundary, including SDK retries; never log bodies."""
from contextvars import ContextVar
from datetime import datetime, timedelta, timezone

import httpx
from pydantic import Field, field_validator, model_validator
from sqlalchemy import func, select

from app.models import UsageEvent, UsagePolicy, new_id
from app.schemas import StrictModel
from app.services.settings_service import SettingsService


usage_context = ContextVar("usage_context", default={})
LOCAL_ZONE = timezone(timedelta(hours=8))
LABELS = {"parse": "解析简历", "prepare": "准备资料和策略", "start": "开始面试",
          "turn": "回答与下一轮", "finish": "结束与报告", "upgrade": "升级报告",
          "continue": "继续练习", "repeat": "重答练习", "transcribe": "语音转写",
          "retry": "重试语音转写", "test": "测试连接"}


class UsagePrice(StrictModel):
    kind: str = Field(pattern="^(ai|voice)$")
    provider: str = Field(min_length=1, max_length=50)
    base_url: str = Field(max_length=500)
    model: str = Field(min_length=1, max_length=150)
    input_per_million: float | None = Field(default=None, ge=0, le=1000000, allow_inf_nan=False)
    output_per_million: float | None = Field(default=None, ge=0, le=1000000, allow_inf_nan=False)
    audio_per_minute: float | None = Field(default=None, ge=0, le=10000, allow_inf_nan=False)

    @field_validator("base_url")
    @classmethod
    def safe_url(cls, value):
        return SettingsService.validate_url("compatible", value)

    @field_validator("provider", "model")
    @classmethod
    def nonblank(cls, value):
        if not value.strip():
            raise ValueError("empty value")
        return value.strip()


class UsageSettings(StrictModel):
    monthly_request_limit: int | None = Field(default=None, ge=1, le=10000000)
    monthly_budget_yuan: float | None = Field(default=None, gt=0, le=10000000, allow_inf_nan=False)
    prices: list[UsagePrice] = Field(default_factory=list, max_length=40)

    @model_validator(mode="after")
    def unique_prices(self):
        keys = [(row.kind, row.provider, row.base_url, row.model) for row in self.prices]
        if len(set(keys)) != len(keys):
            raise ValueError("duplicate model price")
        return self


def integer(value):
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


class UsageService:
    def __init__(self, session_factory):
        self.session_factory = session_factory
        with session_factory() as db:
            if not db.get(UsagePolicy, 1):
                db.add(UsagePolicy(id=1, settings_json=UsageSettings().model_dump()))
                db.commit()

    def settings(self):
        with self.session_factory() as db:
            return db.get(UsagePolicy, 1).settings_json

    def save(self, payload):
        # Prices apply to subsequent calls; previous estimates remain immutable.
        with self.session_factory() as db:
            db.get(UsagePolicy, 1).settings_json = payload.model_dump()
            db.commit()
        return self.settings()

    def begin(self, kind, provider, base_url, model, path):
        context = usage_context.get()
        details = {"kind": kind, "provider": provider, "base_url": base_url,
                   "model": model, "operation": context.get("operation", "API 请求"),
                   "session_id": context.get("session_id"), "status": "pending",
                   "input_tokens": None, "output_tokens": None, "total_tokens": None,
                   "audio_seconds": context.get("audio_seconds") if kind == "voice" else None,
                   "estimated_yuan": None, "partial_estimate": True,
                   "connection_check": path.rstrip("/").endswith("/models")}
        identifier = new_id()
        with self.session_factory() as db:
            details["price"] = next((row for row in db.get(UsagePolicy, 1).settings_json["prices"]
                                     if all(row[key] == details[key] for key in ("kind", "provider", "base_url", "model"))), None)
            db.add(UsageEvent(id=identifier, details=details))
            db.commit()
        return identifier

    def finish(self, identifier, response=None):
        with self.session_factory() as db:
            event = db.get(UsageEvent, identifier)
            details = dict(event.details)
            details["status"] = "success" if response is not None and response.is_success else "failed"
            details["http_status"] = response.status_code if response is not None else None
            payload = {}
            if response is not None:
                try:
                    value = response.json()
                    payload = value if isinstance(value, dict) else {}
                except ValueError:
                    pass
            usage = payload.get("usage") or {}
            if not isinstance(usage, dict):
                usage = {}
            details["input_tokens"] = integer(usage.get("input_tokens", usage.get("prompt_tokens")))
            details["output_tokens"] = integer(usage.get("output_tokens", usage.get("completion_tokens")))
            details["total_tokens"] = integer(usage.get("total_tokens"))
            if details["total_tokens"] is None and all(details[key] is not None for key in ("input_tokens", "output_tokens")):
                details["total_tokens"] = details["input_tokens"] + details["output_tokens"]
            output = payload.get("output")
            details["web_search_calls"] = sum(isinstance(item, dict) and item.get("type") == "web_search_call"
                                              for item in (output if isinstance(output, list) else []))
            price = details.get("price")
            if details["connection_check"]:
                details.update(estimated_yuan=0, partial_estimate=False)
            elif price and details["status"] == "success":
                if details["kind"] == "voice" and details["audio_seconds"] is not None and price["audio_per_minute"] is not None:
                    details.update(estimated_yuan=details["audio_seconds"] / 60 * price["audio_per_minute"], partial_estimate=False)
                elif details["kind"] == "ai":
                    amounts = [details[token] * price[rate] / 1000000
                               for token, rate in (("input_tokens", "input_per_million"), ("output_tokens", "output_per_million"))
                               if details[token] is not None and price[rate] is not None]
                    if amounts:
                        details["estimated_yuan"] = sum(amounts)
                        details["partial_estimate"] = len(amounts) != 2 or bool(details["web_search_calls"])
            event.details = details
            db.commit()

    def summary(self, limit=30, offset=0, session_id=None):
        current = datetime.now(LOCAL_ZONE)
        start = current.replace(day=1, hour=0, minute=0, second=0, microsecond=0).astimezone(timezone.utc).replace(tzinfo=None)
        with self.session_factory() as db:
            policy = db.get(UsagePolicy, 1)
            # Monthly totals are independent of pagination and the selected interview.
            month = db.scalars(select(UsageEvent).where(UsageEvent.created_at >= start)).all()
            query = select(UsageEvent)
            if session_id:
                query = query.where(UsageEvent.details["session_id"].as_string() == session_id)
            total = db.scalar(select(func.count()).select_from(query.subquery()))
            events = db.scalars(query.order_by(UsageEvent.created_at.desc(), UsageEvent.id.desc()).offset(offset).limit(limit)).all()
            rows = [item.details for item in month]
            amount = sum(row["estimated_yuan"] or 0 for row in rows)
            warnings = []
            config = policy.settings_json
            request_limit, budget = config["monthly_request_limit"], config["monthly_budget_yuan"]
            if request_limit and len(rows) >= request_limit * .8:
                warnings.append(f"本月已发出 {len(rows)} 次请求，{'已达到' if len(rows) >= request_limit else '接近'}提醒值 {request_limit} 次。")
            if budget and amount >= budget * .8:
                warnings.append(f"本月已知部分估算 ¥{amount:.4f}，{'已达到' if amount >= budget else '接近'}预算提醒 ¥{budget:g}。")
            if budget and any(row["partial_estimate"] for row in rows):
                warnings.append("部分请求费用未知或未计入工具费，实际花费可能更高，请同时查看服务商账单。")
            return {"since": policy.created_at.replace(tzinfo=timezone.utc).isoformat(),
                    "month": current.strftime("%Y-%m"), "timezone": "Asia/Shanghai", "settings": config,
                    "monthly": {"requests": len(rows), "failed": sum(row["status"] == "failed" for row in rows),
                                "pending": sum(row["status"] == "pending" for row in rows),
                                "ai_requests": sum(row["kind"] == "ai" for row in rows),
                                "voice_requests": sum(row["kind"] == "voice" for row in rows),
                                "input_tokens": sum(row["input_tokens"] or 0 for row in rows),
                                "output_tokens": sum(row["output_tokens"] or 0 for row in rows),
                                "total_tokens": sum(row["total_tokens"] or 0 for row in rows),
                                "unknown_token_requests": sum(row["kind"] == "ai" and not row["connection_check"] and row["total_tokens"] is None for row in rows),
                                "audio_seconds": sum(row["audio_seconds"] or 0 for row in rows if row["kind"] == "voice" and not row["connection_check"]),
                                "estimated_yuan": amount, "incomplete_cost_requests": sum(row["partial_estimate"] for row in rows)},
                    "warnings": warnings, "total": total,
                    "items": [{"id": row.id, "created_at": row.created_at.replace(tzinfo=timezone.utc).isoformat(), **row.details} for row in events]}


class MeteredTransport(httpx.BaseTransport):
    def __init__(self, usage, kind, provider, base_url, model, transport=None):
        self.usage, self.kind, self.provider = usage, kind, provider
        self.base_url, self.model = base_url, model
        self.transport = transport or httpx.HTTPTransport()

    def handle_request(self, request):
        identifier = self.usage.begin(self.kind, self.provider, self.base_url, self.model, request.url.path)
        try:
            response = self.transport.handle_request(request)
            # API operations here are non-streaming. Count even JSON/schema parse failures.
            response.read()
        except Exception:
            self.usage.finish(identifier)
            raise
        self.usage.finish(identifier, response)
        return response

    def close(self):
        self.transport.close()

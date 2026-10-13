import json
import os
from dataclasses import replace
from pathlib import Path
from urllib.parse import urlparse
from uuid import uuid4

from app.config import PROVIDER_DEFAULTS
from app.services.compatible_service import CompatibleService
from app.services.demo_service import DemoService
from app.services.errors import ServiceError
from app.services.openai_service import OpenAIService


def build_ai(settings, usage=None):
    if settings.ai_mode == "demo":
        return DemoService()
    if settings.resolved_api_format == "chat":
        return CompatibleService(settings, usage=usage)
    return OpenAIService(settings, usage=usage)


class SettingsService:
    def __init__(self, application, settings):
        self.application = application
        self.base_settings = settings
        self.path = settings.data_dir / "api_settings.json"
        if self.path.exists():
            self.config = json.loads(self.path.read_text(encoding="utf-8"))
        else:
            self.config = {"active_provider": settings.ai_mode, "providers": {settings.ai_mode: {
                "base_url": settings.resolved_base_url, "main_model": settings.main_model,
                "api_key": settings.api_key,
                "api_format": settings.api_format,
            }}}

    def public(self):
        active = self.application.state.settings
        providers = {}
        for name, defaults in PROVIDER_DEFAULTS.items():
            values = self.config["providers"].get(name, defaults)
            providers[name] = {"base_url": values["base_url"], "main_model": values["main_model"],
                               "key_configured": bool(values.get("api_key")),
                               "api_format": values.get("api_format", "auto")}
        return {"provider": active.ai_mode, "providers": providers}

    @staticmethod
    def validate_url(provider, value):
        url = value.strip().rstrip("/")
        parsed = urlparse(url)
        port = parsed.port  # Validate ports for custom providers as well.
        local_http = parsed.scheme == "http" and parsed.hostname in {"127.0.0.1", "localhost", "::1"}
        if not parsed.hostname or (parsed.scheme != "https" and not local_http) or parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ServiceError("API 地址需为 HTTPS；本机服务可使用 HTTP。地址不能包含密钥、用户名或查询参数。", 400)
        official_hosts = {"deepseek": "api.deepseek.com", "openai": "api.openai.com"}
        if provider in official_hosts and (parsed.hostname != official_hosts[provider] or parsed.scheme != "https" or port not in {None, 443}):
            raise ServiceError("所选厂商请使用官方 API 地址；第三方代理请选“其他兼容接口”并单独配置密钥。", 400)
        return url

    def candidate(self, payload):
        provider = payload.provider
        defaults = PROVIDER_DEFAULTS[provider]
        previous = self.config["providers"].get(provider, {})
        if provider == "demo":
            return replace(self.base_settings, ai_mode="demo", api_key="", main_model="", base_url="")
        raw_url = payload.base_url or previous.get("base_url") or defaults["base_url"]
        try:
            base_url = self.validate_url(provider, raw_url)
        except ValueError as exc:
            raise ServiceError("API 地址无效，请检查主机与端口。", 400) from exc
        model = payload.main_model.strip() or previous.get("main_model") or defaults["main_model"]
        if not model:
            raise ServiceError("请填写模型名称。", 400)
        key = "" if payload.clear_key else payload.api_key.strip()
        if not key and not payload.clear_key:
            if previous.get("base_url", base_url) != base_url and previous.get("api_key"):
                raise ServiceError("API 地址已改变，请重新填写该地址的密钥，避免把原密钥发往其他服务。", 400)
            key = previous.get("api_key", "")
        return replace(self.base_settings, ai_mode=provider, api_key=key, main_model=model, base_url=base_url,
                       api_format=payload.api_format)

    def save(self, payload):
        with self.application.state.config_lock:
            settings = self.candidate(payload)
            ai = build_ai(settings, self.application.state.usage)
            providers = {**self.config["providers"], settings.ai_mode: {
                "base_url": settings.resolved_base_url, "main_model": settings.main_model, "api_key": settings.api_key,
                "api_format": settings.api_format,
            }}
            config = {"active_provider": settings.ai_mode, "providers": providers}
            temporary = self.path.with_name(f"api_settings.{uuid4().hex}.tmp")
            try:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                temporary.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")
                os.replace(temporary, self.path)
            except OSError as exc:
                ai.close()
                temporary.unlink(missing_ok=True)
                raise ServiceError("本地 API 设置保存失败，请检查数据目录的写入权限。", 500) from exc
            old_ai = self.application.state.ai
            self.application.state.settings = settings
            self.application.state.ai = ai
            self.application.state.interviews.settings = settings
            self.application.state.interviews.ai = ai
            self.config = config
            old_ai.close()
            return self.public()

    def test(self, payload):
        # Test drafts without persisting them or switching the current provider.
        with self.application.state.config_lock:
            settings = self.candidate(payload)
        if settings.ai_mode == "demo":
            return {"ok": True, "message": "离线演示可用，没有发出 API 请求。"}
        ai = build_ai(settings, self.application.state.usage)
        try:
            models = ai._call(lambda: ai.client.models.list())
            ids = [model.id for model in models.data]
            if settings.main_model not in ids:
                raise ServiceError("接口已连接，但填写的模型不在可用列表中，请检查模型名。", 400)
            if settings.resolved_api_format == "responses":
                response = ai._call(lambda: ai.client.responses.create(
                    model=settings.main_model, input="Reply only with OK.", max_output_tokens=256, store=False,
                ))
                has_text = bool(response.output_text)
            else:
                response = ai._call(lambda: ai.client.chat.completions.create(
                    model=settings.main_model, messages=[{"role": "user", "content": "Reply only with OK."}],
                    max_tokens=16, **({"extra_body": {"thinking": {"type": "disabled"}}} if settings.ai_mode == "deepseek" else {}),
                ))
                has_text = bool(response.choices and response.choices[0].message.content)
            if not has_text:
                raise ServiceError("已连接接口，但模型未返回文字，请检查所选模型。")
            return {"ok": True, "message": "模型已成功返回文字。联网研究和完整面试需单独验证。", "models": ids}
        finally:
            ai.close()

import json
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path
from uuid import uuid4

import httpx

from app.services.errors import ServiceError
from app.services.settings_service import SettingsService


VOICE_DEFAULTS = {
    "siliconflow": {"base_url": "https://api.siliconflow.cn/v1", "model": "Qwen/Qwen3-ASR-1.7B"},
    "compatible": {"base_url": "", "model": ""},
}


@dataclass(frozen=True)
class VoiceSettings:
    provider: str = "siliconflow"
    base_url: str = "https://api.siliconflow.cn/v1"
    model: str = "Qwen/Qwen3-ASR-1.7B"
    api_key: str = field(default="", repr=False)


class VoiceService:
    def __init__(self, settings, client=None, usage=None):
        self.settings = settings
        from app.services.usage_service import MeteredTransport
        transport = MeteredTransport(usage, "voice", settings.provider, settings.base_url, settings.model) if usage else None
        self.client = client or httpx.Client(timeout=90, transport=transport)

    def close(self):
        self.client.close()

    def request(self, method, path, **kwargs):
        if not self.settings.api_key:
            raise ServiceError("请先在语音转写设置中配置 API Key。", 503)
        try:
            response = self.client.request(method, self.settings.base_url + path,
                headers={"Authorization": "Bearer " + self.settings.api_key}, **kwargs)
        except httpx.TimeoutException as exc:
            raise ServiceError("语音转写超时，录音已保存在本机，可以重试。", 504) from exc
        except httpx.HTTPError as exc:
            raise ServiceError("无法连接语音服务，请检查网络和接口地址，录音仍在本机。", 502) from exc
        if response.status_code in {401, 403}:
            raise ServiceError("语音 API 身份验证或权限检查失败，请检查密钥、模型权限及平台认证状态。", 503)
        if response.status_code in {402, 429}:
            raise ServiceError("语音服务额度不足或请求过快，请检查平台额度后重试。", 503)
        if not response.is_success:
            raise ServiceError("语音服务未能处理录音，请检查模型名称、音频格式或稍后重试。", 502)
        try:
            result = response.json()
            if not isinstance(result, dict):
                raise ValueError("expected object")
            return result
        except ValueError as exc:
            raise ServiceError("语音服务返回了无效结果，请重试。", 502) from exc

    def transcribe(self, path, mime):
        with Path(path).open("rb") as file:
            result = self.request("POST", "/audio/transcriptions", data={"model": self.settings.model},
                                  files={"file": (Path(path).name, file, mime)})
        text = result.get("text")
        if not isinstance(text, str) or not text.strip():
            raise ServiceError("没有识别到有效语音。录音已保存，可重试、重新录制或直接打字。", 422)
        if len(text) > 15000:
            raise ServiceError("转写超过本题文字限制，请缩短回答后重新录制。", 422)
        return text  # Never polish/remove fillers before the user confirms it.

    def test(self):
        result = self.request("GET", "/models")
        ids = [model.get("id") for model in result.get("data", [])]
        if self.settings.model not in ids:
            raise ServiceError("语音接口已连接，但当前模型不在可用列表中。", 400)
        return {"ok": True, "message": "密钥和模型列表检查通过；实际转写请录制一段语音验证。"}


class VoiceSettingsService:
    def __init__(self, application, settings):
        self.application = application
        self.path = settings.data_dir / "voice_settings.json"
        if self.path.exists():
            config = json.loads(self.path.read_text(encoding="utf-8"))
            # Accept the initial single-provider private config.
            if "providers" not in config:
                config = {"provider": config["provider"], "providers": {config["provider"]: config}}
            self.config = config
        else:
            self.config = {"provider": "siliconflow", "providers": {}}
        provider = self.config["provider"]
        values = self.config["providers"].get(provider, VOICE_DEFAULTS[provider])
        self.active = VoiceSettings(provider=provider, base_url=values["base_url"], model=values["model"], api_key=values.get("api_key", ""))

    def public(self):
        providers = {}
        for provider, defaults in VOICE_DEFAULTS.items():
            values = self.config["providers"].get(provider, defaults)
            providers[provider] = {"base_url": values["base_url"], "model": values["model"], "key_configured": bool(values.get("api_key"))}
        return {"provider": self.active.provider, "providers": providers, "ready": bool(self.active.api_key)}

    def candidate(self, payload):
        previous = self.config["providers"].get(payload.provider, {})
        defaults = VOICE_DEFAULTS[payload.provider]
        raw_url = payload.base_url.strip() or previous.get("base_url") or defaults["base_url"]
        try:
            url = SettingsService.validate_url("compatible", raw_url)
        except ValueError as exc:
            raise ServiceError("语音 API 地址或端口无效。", 400) from exc
        if payload.provider == "siliconflow" and url != "https://api.siliconflow.cn/v1":
            raise ServiceError("硅基流动请使用官方地址；其他服务请选择兼容接口并单独填写密钥。", 400)
        model = payload.model.strip() or previous.get("model") or defaults["model"]
        if not model:
            raise ServiceError("请填写语音识别模型名称。", 400)
        key = "" if payload.clear_key else payload.api_key.strip()
        if not key and not payload.clear_key:
            if previous.get("base_url", url) != url and previous.get("api_key"):
                raise ServiceError("语音 API 地址已改变，请重新填写该地址的密钥。", 400)
            key = previous.get("api_key", "")
        return VoiceSettings(payload.provider, url, model, key)

    def save(self, payload):
        with self.application.state.config_lock:
            active = self.candidate(payload)
            config = {"provider": active.provider, "providers": {**self.config["providers"], active.provider: asdict(active)}}
            temporary = self.path.with_name(f"voice_settings.{uuid4().hex}.tmp")
            try:
                temporary.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")
                os.replace(temporary, self.path)
            except OSError as exc:
                temporary.unlink(missing_ok=True)
                raise ServiceError("语音设置保存失败，请检查数据目录权限。", 500) from exc
            old = self.application.state.voice
            self.application.state.voice = VoiceService(active, usage=self.application.state.usage)
            self.active, self.config = active, config
            old.close()
            return self.public()

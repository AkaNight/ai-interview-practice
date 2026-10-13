import os
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
# Keep user data outside the single-file EXE's temporary extraction directory.
RUNTIME_ROOT = Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else ROOT
PROVIDER_DEFAULTS = {
    "demo": {"base_url": "", "main_model": ""},
    "openai": {"base_url": "https://api.openai.com/v1", "main_model": "gpt-4.1"},
    "deepseek": {"base_url": "https://api.deepseek.com", "main_model": "deepseek-flash"},
    "compatible": {"base_url": "", "main_model": ""},
}


@dataclass(frozen=True)
class Settings:
    data_dir: Path
    ai_mode: str = "openai"
    api_key: str = field(default="", repr=False)
    main_model: str = "gpt-4.1"
    api_timeout_seconds: float = 90
    max_upload_mb: int = 10
    base_url: str = ""
    api_format: str = "auto"

    @property
    def resolved_base_url(self):
        return self.base_url or PROVIDER_DEFAULTS[self.ai_mode]["base_url"]

    @property
    def resolved_api_format(self):
        if self.ai_mode == "openai":
            return "responses"
        if self.ai_mode == "deepseek":
            return "chat"
        return "chat" if self.api_format == "auto" else self.api_format

    @classmethod
    def from_env(cls, load_local=True):
        load_dotenv(RUNTIME_ROOT / ".env", override=False)
        data_dir = Path(os.getenv("DATA_DIR", "data"))
        settings = cls(
            data_dir=(data_dir if data_dir.is_absolute() else RUNTIME_ROOT / data_dir),
            ai_mode=os.getenv("AI_MODE", "demo").lower(),
            api_key=os.getenv("DEEPSEEK_API_KEY", "") if os.getenv("AI_MODE") == "deepseek" else os.getenv("OPENAI_API_KEY", ""),
            main_model=os.getenv("MAIN_MODEL", "gpt-4.1"),
            api_timeout_seconds=float(os.getenv("API_TIMEOUT_SECONDS", "90")),
            max_upload_mb=int(os.getenv("MAX_UPLOAD_MB", "10")),
            base_url=os.getenv("API_BASE_URL", ""),
            api_format=os.getenv("API_FORMAT", "auto"),
        )
        if settings.ai_mode not in PROVIDER_DEFAULTS:
            raise ValueError("Unsupported AI_MODE")
        if load_local:
            from dataclasses import replace
            path = settings.data_dir / "api_settings.json"
            if path.exists():
                config = json.loads(path.read_text(encoding="utf-8"))
                provider = config["active_provider"]
                values = config["providers"].get(provider, PROVIDER_DEFAULTS[provider])
                settings = replace(settings, ai_mode=provider, api_key=values.get("api_key", ""),
                                   main_model=values["main_model"], base_url=values["base_url"],
                                   api_format=values.get("api_format", "auto"))
        return settings

import json
import socket
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

from app import config
from app.config import Settings
import run


def test_runtime_data_and_env_use_persistent_executable_directory(tmp_path, monkeypatch):
    persistent = tmp_path / "程序目录"
    persistent.mkdir()
    monkeypatch.setattr(config, "RUNTIME_ROOT", persistent)
    for name in ("AI_MODE", "DATA_DIR", "OPENAI_API_KEY", "DEEPSEEK_API_KEY", "MAIN_MODEL", "API_BASE_URL", "API_FORMAT"):
        monkeypatch.delenv(name, raising=False)
    settings = Settings.from_env(load_local=False)
    assert settings.ai_mode == "demo" and settings.api_key == ""
    assert settings.data_dir == persistent / "data"
    (persistent / ".env").write_text("AI_MODE=deepseek\nMAIN_MODEL=local-model\nDATA_DIR=stored\n", encoding="utf-8")
    settings = Settings.from_env(load_local=False)
    assert settings.ai_mode == "deepseek" and settings.main_model == "local-model"
    assert settings.data_dir == persistent / "stored"


@pytest.mark.parametrize("health,expected", [
    ({"application": "ai-interview-practice"}, 0),
    ({"status": "ok", "version": "0.1.0", "ai_mode": "demo", "main_model": ""}, 0),
    ({"status": "ok", "service": "another-app"}, 1),
])
def test_existing_instance_opens_page_without_starting_second_server(health, expected, monkeypatch):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(health).encode())
        def log_message(self, *_):
            pass
    server = HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    opened = []
    port = server.server_port
    monkeypatch.setattr("sys.argv", ["run.py", "--port", str(port)])
    monkeypatch.setattr(run.webbrowser, "open", opened.append)
    try:
        assert run.main() == expected
        assert opened == ([f"http://127.0.0.1:{port}"] if expected == 0 else [])
        monkeypatch.setattr("sys.argv", ["run.py", "--port", str(port), "--no-browser"])
        opened.clear()
        assert run.main() == expected and not opened
    finally:
        server.shutdown()
        server.server_close()
        thread.join()

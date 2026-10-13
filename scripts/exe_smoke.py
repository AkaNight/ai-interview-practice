"""Isolated EXE acceptance: demo plus local mock APIs, never paid providers."""
import argparse
import io
import json
import os
import shutil
import socket
import subprocess
import sys
import threading
import time
import wave
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from uuid import uuid4

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app.services.demo_service import DemoService


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--exe", type=Path, default=ROOT / "dist/windows/AI-Interview-Practice.exe")
    parser.add_argument("--port", type=int, default=8005)
    args = parser.parse_args()
    target = ROOT / "data" / "exe_verification" / ("中文 空格 " + uuid4().hex[:8])
    target.mkdir(parents=True)
    executable = target / "AI-Interview-Practice.exe"
    shutil.copy2(args.exe.resolve(), executable)
    environment = os.environ.copy()
    for key in ("AI_MODE", "DATA_DIR", "OPENAI_API_KEY", "DEEPSEEK_API_KEY", "MAIN_MODEL", "API_BASE_URL", "API_FORMAT"):
        environment.pop(key, None)
    flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
    command = [str(executable), "--no-browser", "--port", str(args.port)]
    processes = []
    base = f"http://127.0.0.1:{args.port}"
    parsed = DemoService().parse_resume(ROOT / "examples/sample_resume.docx", "虚构验收样本\n博士\n项目：Suricata规则\n技能：Python、Suricata、PCAP").model_dump()

    class MockAPI(BaseHTTPRequestHandler):
        def reply(self, value):
            body = json.dumps(value).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        def do_GET(self):
            self.reply({"data": [{"id": "mock-model"}, {"id": "mock-asr"}]})
        def do_POST(self):
            data = self.rfile.read(int(self.headers.get("Content-Length", 0)))
            if self.path.endswith("/audio/transcriptions"):
                self.reply({"text": "嗯，我负责 Suricata 规则验证。"})
                return
            payload = json.loads(data)
            if self.path.endswith("/responses"):
                schema = ((payload.get("text") or {}).get("format") or {}).get("name")
                text = json.dumps(parsed, ensure_ascii=False) if schema == "ParsedResume" else "OK"
                self.reply({"id": "resp_mock", "object": "response", "created_at": 1, "model": "mock-model",
                            "status": "completed", "error": None, "usage": {"input_tokens": 10, "output_tokens": 5, "total_tokens": 15},
                            "output": [{"type": "message", "id": "msg_mock", "role": "assistant", "status": "completed",
                                        "content": [{"type": "output_text", "text": text, "annotations": []}]}]})
            else:
                text = json.dumps(parsed, ensure_ascii=False) if payload.get("response_format") else "OK"
                self.reply({"id": "chat_mock", "object": "chat.completion", "model": "mock-model", "created": 1,
                            "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
                            "choices": [{"index": 0, "finish_reason": "stop", "message": {"role": "assistant", "content": text}}]})
        def log_message(self, *_):
            pass

    mock = ThreadingHTTPServer(("127.0.0.1", 0), MockAPI)
    thread = threading.Thread(target=mock.serve_forever, daemon=True)
    thread.start()
    mock_url = f"http://127.0.0.1:{mock.server_port}/v1"
    log = (target / "exe.log").open("w", encoding="utf-8")

    def launch():
        process = subprocess.Popen(command, cwd=ROOT, env=environment, stdout=log, stderr=log, creationflags=flags)
        processes.append(process)
        for _ in range(240):
            if process.poll() is not None:
                raise RuntimeError("EXE exited before startup; inspect its isolated exe.log")
            try:
                health = httpx.get(base + "/api/health", timeout=1).json()
                if health.get("version") == "0.1.1":
                    return process
            except (httpx.HTTPError, ValueError):
                pass
            time.sleep(.25)
        raise RuntimeError("EXE startup timeout")

    def stop(process):
        if process.poll() is None:
            if sys.platform == "win32":
                subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
            else:
                process.terminate()
            process.wait(timeout=15)
        for _ in range(40):
            try:
                httpx.get(base + "/api/health", timeout=.3)
            except httpx.HTTPError:
                return
            time.sleep(.1)
        raise RuntimeError("EXE did not stop")

    with httpx.Client(base_url=base, timeout=60) as client:
        def call(method, path, **kwargs):
            response = client.request(method, path, **kwargs)
            response.raise_for_status()
            return response.json()
        def upload():
            return call("POST", "/api/resumes", files={"file": ("fictional.docx", (ROOT / "examples/sample_resume.docx").read_bytes())})["id"]
        try:
            first = launch()
            health = call("GET", "/api/health")
            assert health["ai_mode"] == "demo" and not health["voice_ready"]
            assert call("GET", "/api/interviews")["total"] == 0
            assert (target / "data/interviews.sqlite3").exists()
            assert client.get("/").status_code == 200
            assert "use strict" in client.get("/static/app.js").text
            resume = upload()
            assert "Suricata" in json.dumps(call("POST", f"/api/resumes/{resume}/parse"))
            session = call("POST", "/api/interviews", json={"resume_id": resume, "company": "EXE验收公司", "position": "技术岗位", "interview_type": "技术面"})["id"]
            call("POST", f"/api/interviews/{session}/prepare")
            call("POST", f"/api/interviews/{session}/start")
            call("POST", "/api/usage/settings", json={"monthly_request_limit": 5})
            stop(first)
            second = launch()
            assert call("GET", f"/api/interviews/{session}")["status"] == "active"
            assert call("GET", "/api/usage")["settings"]["monthly_request_limit"] == 5
            duplicate = subprocess.run(command, cwd=ROOT, env=environment, capture_output=True, creationflags=flags, timeout=60)
            assert duplicate.returncode == 0 and b"already running" in duplicate.stdout
            # Exercise frozen SDK and schema modules only against our localhost mock.
            for api_format in ("responses", "chat"):
                settings = {"provider": "compatible", "base_url": mock_url, "main_model": "mock-model", "api_key": "local-fixture-key", "api_format": api_format}
                assert call("POST", "/api/settings/test", json=settings)["ok"]
                call("POST", "/api/settings", json=settings)
                identifier = upload()
                assert "Suricata" in json.dumps(call("POST", f"/api/resumes/{identifier}/parse"))
            call("POST", "/api/settings", json={"provider": "demo"})
            call("POST", "/api/voice-settings", json={"provider": "compatible", "base_url": mock_url, "model": "mock-asr", "api_key": "local-fixture-key"})
            assert call("POST", "/api/voice-settings/test", json={"provider": "compatible", "base_url": mock_url, "model": "mock-asr"})["ok"]
            audio = io.BytesIO()
            with wave.open(audio, "wb") as wav:
                wav.setnchannels(1); wav.setsampwidth(2); wav.setframerate(16000); wav.writeframes(b"\0\0" * 16000)
            transcript = call("POST", f"/api/interviews/{session}/transcribe", data={"turn_index": 1, "duration": 1}, files={"file": ("test.wav", audio.getvalue(), "audio/wav")})["text"]
            state = call("POST", f"/api/interviews/{session}/turn", json={"turn_index": 1, "answer": transcript})
            assert "Suricata" in state["turns"][-1]["question"]
            state = call("POST", f"/api/interviews/{session}/finish")
            assert state["report"] and state["status"] == "completed"
            assert call("GET", "/api/usage")["monthly"]["requests"] == 8
            # Leave the fixture in demo mode for browser acceptance; ASR key is local and fake.
            call("POST", "/api/voice-settings", json={"provider": "compatible", "base_url": mock_url, "model": "mock-asr", "clear_key": True})
            result = {"ok": True, "exe": str(executable), "checks": ["Chinese path", "different cwd", "default demo", "static assets", "resume", "restart persistence", "duplicate launch", "mock Responses", "mock Chat", "mock ASR", "dynamic followup", "report", "usage"], "paidCalls": 0}
            (ROOT / "data/exe_verification/latest.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
            print(json.dumps(result, ensure_ascii=False))
        finally:
            for process in processes:
                if process.poll() is None:
                    stop(process)
            mock.shutdown(); mock.server_close(); thread.join(); log.close()


if __name__ == "__main__":
    main()

"""Explicit paid Phase 1 check using a fictional resume, never run by pytest."""
import argparse
import json
from pathlib import Path

import httpx


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--allow-paid", action="store_true", help="Only use after explicit authorization for paid API testing")
    args = parser.parse_args()
    if not args.allow_paid:
        parser.error("真实 API 验收默认关闭。仅在用户明确授权付费测试后使用 --allow-paid；日常验证请运行本地模拟测试。")
    base_url = args.base_url
    with httpx.Client(timeout=180) as client:
        def call(method, path, **kwargs):
            response = client.request(method, base_url + path, **kwargs)
            if not response.is_success:
                # The app returns sanitized errors and never keys.
                raise RuntimeError(f"{path}: HTTP {response.status_code}: {response.json().get('error', 'request failed')}")
            return response.json()
        health = call("GET", "/api/health")
        if health["ai_mode"] == "demo" or not health["ai_ready"]:
            raise RuntimeError("Configure a real API provider before running this explicit live check")
        uploaded = call("POST", "/api/resumes", files={"file": ("sample_resume.docx", Path("examples/sample_resume.docx").read_bytes())})
        parsed = call("POST", f"/api/resumes/{uploaded['id']}/parse")
        assert "Suricata" in json.dumps(parsed, ensure_ascii=False)
        print("Resume parsed", flush=True)
        session = call("POST", "/api/interviews", json={"resume_id": uploaded["id"], "company": "中国银行", "position": "信息科技岗", "interview_type": "半结构化面试", "duration_minutes": 20})
        identifier = session["id"]
        print("Session created:", identifier, flush=True)
        prepared = call("POST", f"/api/interviews/{identifier}/prepare")
        assert prepared["status"] == "prepared"
        print("Research and strategy completed", flush=True)
        started = call("POST", f"/api/interviews/{identifier}/start")
        assert len(started["turns"]) == 1
        print("First question generated", flush=True)
        answer = "这是虚构验收样本。我博士期间研究加密流量分析，项目中我主要负责 PCAP 数据清洗、包长和 IAT 特征提取，并使用 Suricata 验证检测规则。模型架构由其他成员负责，我负责数据和规则验证，尚未在真实银行环境部署。"
        replied = call("POST", f"/api/interviews/{identifier}/turn", json={"turn_index": 1, "answer": answer})
        assert replied["turns"][0]["answer"] == answer
        assert len(replied["turns"]) == 2, "Expected an actual follow-up after first answer"
        print("Follow-up:", replied["turns"][-1]["question"], flush=True)
        finished = call("POST", f"/api/interviews/{identifier}/finish")
        assert finished["status"] == "completed" and finished["report"]
        assert call("GET", f"/api/interviews/{identifier}") == finished
        result = {"result": "passed", "provider": health["ai_mode"], "model": health["main_model"], "session_id": identifier,
                  "followup": replied["turns"][-1]["question"], "report_generated": True}
        Path("data/live_verification.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(result, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()

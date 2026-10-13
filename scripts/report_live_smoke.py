"""Explicit paid check: upgrade one known fictional acceptance session, never delete it."""
import argparse
import json
from pathlib import Path

import httpx


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("session_id", help="ID of a completed fictional acceptance interview")
    parser.add_argument("--url", default="http://127.0.0.1:8000")
    parser.add_argument("--allow-paid", action="store_true", help="Only use after explicit authorization for paid API testing")
    args = parser.parse_args()
    if not args.allow_paid:
        parser.error("真实 API 报告验收默认关闭。仅在用户明确授权付费测试后使用 --allow-paid。")
    with httpx.Client(base_url=args.url, timeout=180) as client:
        session = client.get(f"/api/interviews/{args.session_id}")
        session.raise_for_status()
        assert session.json()["status"] == "completed"
        upgraded = client.post(f"/api/interviews/{args.session_id}/report/upgrade")
        if upgraded.is_error:
            raise RuntimeError(upgraded.json().get("error", "Report upgrade failed"))
        value = upgraded.json()
        report = value["report"]
        answered = {turn["turn_index"]: turn["answer"] for turn in value["turns"] if turn["answer"] is not None}
        assert report["version"] == 3
        assert {review["turn_index"] for review in report["answer_reviews"]} == set(answered)
        points = 0
        for review in report["answer_reviews"]:
            for point in review["strengths"] + review["issues"] + review["risks"]:
                assert point["evidence"] in answered[review["turn_index"]]
                points += 1
        for gap in report["knowledge_gaps"]:
            for evidence in gap["evidence"]:
                assert evidence["quote"] in answered[evidence["turn_index"]]
        assert report["next_practice"] and report["next_practice"]["exercises"]
        if not any(turn["answer"] is not None and turn["followup_depth"] > 0 for turn in value["turns"]):
            assert not any("追问" in score["dimension"] for score in report["scores"])
        assert client.get(f"/api/interviews/{args.session_id}/report").json() == report
        result = {"result": "passed", "session_id": args.session_id, "report_version": 3,
                  "reviews": len(report["answer_reviews"]), "verified_quotes": points,
                  "voice_answers": report["oral_analysis"]["voice_answers"],
                  "priority_practice": len(report["priority_practice"]), "knowledge_gaps": len(report["knowledge_gaps"]),
                  "next_practice": True, "unanswered_followup_unscored": True}
        output = Path("data/report_live_verification.json")
        output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()

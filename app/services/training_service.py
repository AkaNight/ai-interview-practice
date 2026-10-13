from pydantic import ValidationError

from app.schemas import NextPractice


def practice_plan(report, source):
    """Keep prior feedback small; old reports can be reused without another AI call."""
    raw = report.get("next_practice")
    try:
        plan = NextPractice.model_validate(raw).model_dump() if raw else None
    except ValidationError:
        plan = None
    if not plan:
        priorities = report.get("priority_practice") or report.get("improvements") or []
        previous = source.state_json.get("training_plan") or {}
        plan = {"mode": "专项练习", "focus": previous.get("focus") or "个人职责、回答结构与验证方法",
                "duration_minutes": 15,
                "exercises": priorities[:3] or ["用真实经历说明个人职责", "说明方法选择和实际验证结果"]}
    plan["mode"] = plan["mode"][:200]
    plan["focus"] = plan["focus"].strip()[:500] or "个人职责与回答结构"
    plan["exercises"] = [item[:500] for item in plan["exercises"][:5]] or [plan["focus"]]
    return plan


def practice_basis(report):
    gaps = [{"topic": gap["topic"][:200], "reason": gap["reason"][:400],
             "review_suggestions": [item[:300] for item in gap.get("review_suggestions", [])[:3]]}
            for gap in report.get("knowledge_gaps", [])[:3]]
    issues = [point for review in report.get("answer_reviews", []) for point in review.get("issues", [])][:3]
    return {"knowledge_gaps": gaps,
            "expression_targets": [{"observation": point["observation"][:300], "suggestion": point["suggestion"][:300]} for point in issues]}

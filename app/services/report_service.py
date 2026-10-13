"""Ground AI feedback in submitted answers; calculate transcript statistics locally."""
import math
import re


FILLERS = ("嗯", "呃", "那个", "就是", "然后", "其实")
REPORT_VERSION = 3
LIMITATIONS = (
    "仅统计已提交回答对应的原始转写，不使用修改后的文字计算口语指标。"
    "识别服务可能省略口头词；那个、就是、然后、其实也可能是正常用词，数量仅供自查。"
    "时长包含录音中的停顿；转写单位/分钟不是声学语速。"
    "不能据此判断音量、语调、情绪或实际停顿，纯文字回答不计算口语指标。"
)


def text_units(text):
    # One Han character or one Latin/numeric word counts as a transcript unit.
    return len(re.findall(r"[\u3400-\u9fff]|[A-Za-z0-9]+(?:['’-][A-Za-z0-9]+)*", text))


def oral_analysis(turns):
    rows = []
    for turn in turns:
        if turn.answer is None:
            continue
        raw = turn.answer_transcript_raw
        voiced = bool(turn.audio_path and raw)
        duration = turn.answer_duration if voiced else None
        if duration is not None and (not math.isfinite(duration) or duration <= 0):
            duration = None
        units = text_units(raw) if voiced else None
        fillers = {word: raw.count(word)
                   for word in FILLERS} if voiced else {}
        rows.append({
            "turn_index": turn.turn_index, "source": "voice" if voiced else "text",
            "answer_units": text_units(turn.answer), "transcript_units": units,
            "duration_seconds": round(duration, 2) if duration is not None else None,
            "units_per_minute": round(units * 60 / duration, 1) if duration else None,
            "filler_counts": fillers, "filler_total": sum(fillers.values()) if voiced else None,
            "fillers_per_minute": round(sum(fillers.values()) * 60 / duration, 1) if duration else None,
            "possible_repetitions": [match.group(0) for match in re.finditer(r"([我你他这那就然])\1+", raw)] if voiced else [],
        })
    voiced = [row for row in rows if row["source"] == "voice"]
    timed = [row for row in voiced if row["duration_seconds"] is not None]
    total = sum(row["duration_seconds"] for row in timed)
    return {
        "voice_answers": len(voiced), "text_answers": len(rows) - len(voiced),
        "timed_answers": len(timed), "total_duration_seconds": round(total, 2),
        "average_duration_seconds": round(total / len(timed), 2) if timed else None,
        "longest_answer": max(timed, key=lambda row: row["duration_seconds"])["turn_index"] if timed else None,
        "longest_duration_seconds": max((row["duration_seconds"] for row in timed), default=None),
        "shortest_answer": min(timed, key=lambda row: row["duration_seconds"])["turn_index"] if timed else None,
        "shortest_duration_seconds": min((row["duration_seconds"] for row in timed), default=None),
        "fillers_per_minute": round(sum(row["filler_total"] for row in timed) * 60 / total, 1) if total else None,
        "filler_counts": {word: sum(row["filler_counts"][word] for row in voiced) for word in FILLERS},
        "turns": rows, "limitations": LIMITATIONS,
    }


def validate_answer_review(review, answered):
    if review.turn_index not in answered:
        raise ValueError("讲评题号必须对应实际已提交的回答")
    for point in review.strengths + review.issues + review.risks:
        if point.evidence not in answered[review.turn_index] or not point.evidence.strip():
            raise ValueError(f"第{review.turn_index}题 evidence 必须逐字摘录该题最终提交的 answer，不能引用问题、简历或编造证据")


def validate_report(result, context):
    answered = {turn["turn_index"]: turn["answer"] for turn in context["turns"] if turn["answer"] is not None}
    indices = [review.turn_index for review in result.answer_reviews]
    if len(indices) != len(answered) or set(indices) != set(answered):
        raise ValueError("answer_reviews 必须恰好覆盖所有已提交回答的 turn_index，不能包含未作答题或重复题号")
    for review in result.answer_reviews:
        validate_answer_review(review, answered)
    for gap in result.knowledge_gaps:
        for evidence in gap.evidence:
            if evidence.turn_index not in answered or not evidence.quote.strip() or evidence.quote not in answered[evidence.turn_index]:
                raise ValueError("知识薄弱点的 evidence 必须引用已作答题号和该题 answer 的连续原文，未考察的知识点不能认定为薄弱")
    if not answered and (result.overall_score is not None or result.scores):
        raise ValueError("没有回答时不能评分")
    if not any(turn["answer"] is not None and turn.get("followup_depth", 0) > 0 for turn in context["turns"]):
        if any("追问" in score.dimension for score in result.scores):
            raise ValueError("没有已作答的追问题，必须省略追问应对评分；未回答的追问不是作答证据")
    if context.get("mode") == "group":
        allowed = {"论证清晰度", "回应与倾听", "协作推进", "分歧处理", "总结能力"}
        has_summary = any(turn["answer"] is not None and (turn.get("group_round") or {}).get("stage") == "summary" for turn in context["turns"])
        for score in result.scores:
            if score.dimension not in allowed:
                raise ValueError("群面只能按指定协作讨论维度评分，不能评价未考察的简历或技术能力")
            if not score.evidence.strip() or not any(score.evidence in answer for answer in answered.values()):
                raise ValueError("群面分项证据必须逐字引用用户实际answer，不能引用AI队友发言")
            if score.dimension == "总结能力" and not has_summary:
                raise ValueError("未提交summary阶段发言，不能评分总结能力")

from collections import Counter

from app.schemas import Question


def validate_scenario(result, context):
    if not result.fictional:
        raise ValueError("案例必须标记为虚构模拟，不能称为真实公司题目")
    expected = {f"member_{index}" for index in range(1, context["group_config"]["size"])}
    identifiers = [member.member_id for member in result.teammates]
    if len(identifiers) != len(expected) or set(identifiers) != expected:
        raise ValueError("队友ID必须恰好为member_1到member_{size-1}，不能缺失或重复")
    if not all(value.strip() for value in [result.title, result.background, result.task, *result.rules]):
        raise ValueError("案例题目、背景、任务和规则不能只有空白")
    if any(member.name != "成员" + chr(64 + int(member.member_id.split("_")[1])) for member in result.teammates):
        raise ValueError("虚构队友名字固定为成员A/B/C/D/E，不能使用真实姓名或指定领导")
    if len({member.name for member in result.teammates}) != len(expected) or len({member.style for member in result.teammates}) < 2:
        raise ValueError("队友名字必须不同，讨论风格至少有两种")


def round_context(context):
    turns = context["turns"]
    if not turns:
        stage = "opening"
    elif context["constraints"]["must_end"] or (turns[-1].get("group_round") or {}).get("stage") == "summary":
        stage = "closing"
    elif context["constraints"]["prepare_closing"]:
        stage = "summary"
    else:
        stage = "discussion"
    members = sorted(member["member_id"] for member in context["group_scenario"]["teammates"])
    if stage == "opening":
        speakers = members
    else:
        start = max(0, context["state"]["turn_count"] - 1) * 2
        speakers = [members[(start + offset) % len(members)] for offset in range(2)]
    return {**context, "group_stage_target": stage, "group_speakers": speakers}


def validate_round(result, context):
    stage = context["group_stage_target"]
    if result.stage != stage:
        raise ValueError("stage必须与group_stage_target一致")
    speakers = [message.member_id for message in result.messages]
    expected = set(context["group_speakers"])
    if len(speakers) != len(set(speakers)) or not set(speakers).issubset(expected):
        raise ValueError("每位指定队友最多发言一次，不能使用其他成员ID")
    if stage != "closing" and set(speakers) != expected:
        raise ValueError("本轮必须让group_speakers指定的队友各发言一次")
    if stage == "closing" and result.user_prompt.strip():
        raise ValueError("closing阶段user_prompt必须为空")
    if stage != "closing" and not result.user_prompt.strip():
        raise ValueError("非closing阶段必须给出用户讨论任务")
    answer = context["turns"][-1]["answer"] if context["turns"] else None
    for message in result.messages:
        if not message.speech.strip():
            raise ValueError("队友发言不能只有空白")
        quote = message.reply_to_quote
        if quote is not None and (not answer or not quote.strip() or quote not in answer):
            raise ValueError("reply_to_quote必须逐字引用用户最新answer的非空连续原文，不能引用旧回答或AI发言")
    if stage in {"discussion", "summary"} and not any(message.reply_to_quote for message in result.messages):
        raise ValueError("本轮至少一位队友必须具体回应用户最新answer，并引用其连续原文")


def round_question(result, turn_index):
    return Question(action="new_topic", question=result.user_prompt, topic=f"group_round_{turn_index}",
                    phase="behavioral", followup_depth=0, decision_note="无领导小组讨论轮次")


def group_statistics(context):
    counts = Counter()
    for turn in context["turns"]:
        for message in (turn.get("group_round") or {}).get("messages", []):
            counts[message["member_id"]] += 1
    for message in (context.get("group_closing") or {}).get("messages", []):
        counts[message["member_id"]] += 1
    return {"user_contributions": sum(turn["answer"] is not None for turn in context["turns"]),
            "user_summary_submitted": any(turn["answer"] is not None and (turn.get("group_round") or {}).get("stage") == "summary" for turn in context["turns"]),
            "teammates": [{"name": member["name"], "contributions": counts[member["member_id"]]}
                          for member in context["group_scenario"]["teammates"]],
            "limitations": "仅统计本地记录的轮次；AI队友发言是模拟文本，不能当作用户发言或真实群面表现。"}

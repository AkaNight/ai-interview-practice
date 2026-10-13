import re

from app.schemas import AnswerReview, BasicInfo, GroupMember, GroupMessage, GroupRound, GroupScenario, NextPractice, ParsedResume, Project, Question, Report, Research, Strategy, TrainingStep
from app.services.errors import ServiceError


class DemoService:
    """Explicit offline demo, never an implicit fallback from real AI failures."""

    def close(self):
        pass

    def parse_resume(self, path, text):
        if not text.strip():
            raise ServiceError("演示模式无法读取扫描图片简历，请使用含文字的 PDF/DOCX，或配置真实 AI 模式。", 400)
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        skills = [word for word in ["Suricata", "PCAP", "TLS", "IAT", "PyTorch", "Python", "SQL", "Java"] if word.lower() in text.lower()]
        projects = [Project(
            name=line[:120], overall_work=line, personal_contribution="未说明，需要在面试中核实",
            technologies=skills, results="未说明",
        ) for line in lines if "项目" in line][:3]
        return ParsedResume(
            basic_info=BasicInfo(name="", summary="离线演示：" + text[:400]),
            education=[line for line in lines if any(word in line for word in ["博士", "硕士", "本科"])],
            skills=skills, projects=projects,
            research=[line for line in lines if "研究" in line],
            publications=[line for line in lines if "论文" in line],
            internships=[line for line in lines if "实习" in line and "无" not in line], awards=[],
            resume_risk_points=["演示模式仅作文字提取；个人职责和成果需核实"],
            likely_interview_topics=skills,
        )

    def research_interview(self, context):
        return Research(
            company_summary=f"离线演示，未联网核实 {context['company']} 的资料。",
            position_summary=f"以 {context['position']} 为主题测试程序流程，岗位职责尚未核实。",
            interview_process=[], common_question_types=["自我介绍", "个人贡献", "岗位动机"],
            technical_topics=[], company_specific_topics=[], behavioral_topics=["协作边界"],
            uncertainties=["所有公司和面试流程信息均待真实联网研究"], sources=[],
        )

    def generate_strategy(self, context):
        if context.get("mode") == "training":
            plan = context["training_plan"]
            return Strategy(primary_focus=[plan["focus"]], resume_focus=plan["exercises"],
                            technical_focus=context["resume"]["skills"], behavioral_focus=[], company_focus=[],
                            potential_resume_risks=[], recommended_flow=["projects", "technical"])
        return Strategy(
            primary_focus=["个人贡献", "岗位匹配"],
            resume_focus=[project["name"] for project in context["resume"]["projects"]],
            technical_focus=context["resume"]["skills"], behavioral_focus=["团队协作"],
            company_focus=["求职动机"],
            potential_resume_risks=context["resume"]["resume_risk_points"],
            recommended_flow=["self_intro", "projects", "technical", "company_and_position"],
        )

    def generate_question(self, context, validate):
        turns = context["turns"]
        constraints = context["constraints"]
        if constraints["must_end"]:
            result = Question(action="end", question="", topic="closing", phase="closing", followup_depth=0, decision_note="达到程序结束条件")
        elif not turns:
            if context.get("mode") == "training":
                focus = context["training_plan"]["focus"]
                result = Question(action="new_topic", question=f"请结合一项真实经历，说明{focus}。", topic="training_focus", phase="projects", followup_depth=0, decision_note="开始专项练习")
            else:
                result = Question(action="new_topic", question="请先做一下自我介绍，重点说明与你应聘岗位相关的经历。", topic="self_intro", phase="self_intro", followup_depth=0, decision_note="了解候选人背景")
        else:
            last = turns[-1]
            answer = last["answer"] or ""
            asked = {turn["question"] for turn in turns}
            followups = []
            if "Suricata" in answer:
                followups.append("你刚才提到 Suricata，你本人是如何设计并验证检测规则的？")
            if "包长" in answer or "IAT" in answer:
                followups.append("你提到包长或 IAT，网络环境变化会如何影响这些特征的稳定性？")
            if "我们" in answer and "我负责" not in answer and "我主要" not in answer:
                followups.append("你刚才讲了团队的工作，其中你本人具体负责哪个部分？")
            if len(answer) < 40:
                followups.append("可以用一个你亲自参与的具体例子说明刚才的回答吗？")
            followups += ["你刚才提到的方法是如何验证有效性的？", "这项工作中你遇到的主要困难是什么？"]
            available = [question for question in followups if question not in asked]
            if available and not constraints["must_switch_topic"]:
                result = Question(action="follow_up", question=available[0], topic=last["topic"], phase=last["phase"], followup_depth=last["followup_depth"] + 1, decision_note="根据当前回答追问")
            else:
                projects = context["resume"]["projects"]
                project_name = projects[0]["name"] if projects else "与你应聘岗位最相关的一项经历"
                candidates = [
                    ("projects", "project_1", f"请介绍{project_name}，重点说明你本人的贡献。"),
                    ("technical", "technical", "请介绍一项你在上述经历中使用的核心技术，以及选择它的原因。"),
                    ("company_and_position", "motivation", f"为什么选择 {context['company']} 的 {context['position']}？"),
                    ("behavioral", "collaboration", "请举例说明你如何与其他成员划分职责并完成协作。"),
                    ("candidate_questions", "candidate_questions", "关于这个岗位，你最希望进一步了解什么？"),
                ]
                if context.get("mode") == "training":
                    candidates = [("projects", f"training_exercise_{index}", f"围绕{exercise}，请说明你准备如何组织回答。")
                                  for index, exercise in enumerate(context["training_plan"]["exercises"])]
                candidate = next((item for item in candidates if item[1] != last["topic"] and item[2] not in asked), None)
                if candidate:
                    phase, topic, question = candidate
                    result = Question(action="new_topic", question=question, topic=topic, phase=phase, followup_depth=0, decision_note="切换关注领域")
                else:
                    result = Question(action="end", question="", topic="closing", phase="closing", followup_depth=0, decision_note="演示流程结束")
        validate(result)
        return result

    def generate_training_step(self, context, validate):
        last = context["turns"][-1]
        feedback = AnswerReview(turn_index=last["turn_index"],
            summary="离线演示讲评：回答已保存，未使用 AI 评价内容。", strengths=[], issues=[], risks=[],
            suggested_structure=["说明本人职责", "说明实际采用的方法", "补充实际验证结果"],
            timing_advice="演示模式不评价时间安排；可参照结束后的录音统计。")
        result = TrainingStep(feedback=feedback, next_question=self.generate_question(context, lambda _: None))
        validate(result)
        return result

    def generate_group_scenario(self, context, validate):
        styles = ["分析型：关注依据与取舍", "质疑型：提出风险与替代方案", "协调型：梳理分歧与共识", "执行型：关注落实步骤", "发散型：提出不同视角"]
        scenario = GroupScenario(title=context["group_config"]["topic"][:160] or "资源优先级讨论（离线演示案例）",
            background="虚构案例：某团队有数据质量改进、规则误报治理、文档培训三项工作，每项需要1人月，但本月只有2人月资源。所有数字与事件均为演示设定。",
            task="讨论应优先推进哪两项，并说明选择依据、风险和执行方案。",
            rules=["所有成员均为平等参与者，没有指定领导", "提出理由并回应不同意见", "最后归纳共同方案及仍有分歧的部分"],
            teammates=[GroupMember(member_id=f"member_{index+1}", name="成员" + chr(65 + index), style=styles[index])
                       for index in range(context["group_config"]["size"] - 1)], fictional=True)
        validate(scenario)
        return scenario

    def generate_group_round(self, context, validate):
        stage = context["group_stage_target"]
        views = {"member_1": "我倾向优先处理数据质量，因为后续判断依赖可靠的数据。",
                 "member_2": "我担心误报直接影响使用体验，建议比较两项工作的短期风险。",
                 "member_3": "我们可以先明确共同判断标准，再讨论优先级的分歧。",
                 "member_4": "建议把负责人、验证方法和阶段性目标一起列出来。",
                 "member_5": "是否能把培训缩成短文档，从而减少对其他工作的影响？"}
        answer = context["turns"][-1]["answer"] if context["turns"] else None
        quote = answer[:60] if answer else None
        messages = [GroupMessage(member_id=identifier,
            speech=("你提到的方案值得进一步比较，" if quote else "") + views[identifier],
            reply_to_quote=quote if index == 0 else None)
            for index, identifier in enumerate(context["group_speakers"])]
        prompts = {"opening": "轮到你，请提出初始选择并说明理由。", "discussion": "轮到你，请回应队友的不同观点，并推进方案。",
                   "summary": "请归纳推荐方案、理由、尚存分歧及执行步骤。", "closing": ""}
        result = GroupRound(stage=stage, messages=messages, user_prompt=prompts[stage],
            discussion_summary="离线演示：已记录各方观点，方案仍需比较；这不代表真实讨论共识。")
        validate(result)
        return result

    def generate_report(self, context):
        answers = [turn["answer"] for turn in context["turns"] if turn["answer"] is not None]
        short = sum(len(re.sub(r"\s", "", answer)) < 40 for answer in answers)
        return Report(
            overall_score=None, summary=f"离线演示报告：已记录 {len(answers)} 次{'个人发言' if context.get('mode') == 'group' else '文字回答'}。",
            strengths=[], improvements=[f"有 {short} 次回答少于40字，可检查是否缺少个人职责或具体例子。"] if short else [],
            scores=[], limitations="此报告仅验证保存和生成流程，没有使用 AI 评价，也没有联网资料。真实面试质量需在配置 API 后验证。",
            answer_reviews=[AnswerReview(
                turn_index=turn["turn_index"], summary="离线演示：仅保存回答，未进行 AI 内容评价。",
                strengths=[], issues=[], risks=[], suggested_structure=[],
                timing_advice="录音时长请参照口语统计；演示模式不评价时间安排。",
            ) for turn in context["turns"] if turn["answer"] is not None],
            priority_practice=[],
            next_practice=NextPractice(mode="专项练习（演示计划）",
                focus="观点依据、回应分歧与协作推进" if context.get("mode") == "group" else (context.get("training_plan") or {}).get("focus") or "个人职责、回答结构与验证方法",
                duration_minutes=10, exercises=["提出观点依据", "回应不同意见", "归纳方案与分歧"] if context.get("mode") == "group" else ["说明本人职责", "说明方法选择", "补充实际验证结果"]) if answers else None,
        )

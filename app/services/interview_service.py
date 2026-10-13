from collections import defaultdict
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
import os
import shutil
from threading import Lock
from uuid import UUID

from sqlalchemy import delete, func, select

from app.models import InterviewPreparation, InterviewReport, InterviewSession, InterviewTurn, Resume, now
from app.schemas import Question, Research
from app.services.errors import ServiceError
from app.services.report_service import REPORT_VERSION, oral_analysis, validate_answer_review, validate_report
from app.services.preparation_service import PreparationService
from app.services.training_service import practice_basis, practice_plan
from app.services.group_service import group_statistics, round_context, round_question, validate_round, validate_scenario


class InterviewService:
    def __init__(self, session_factory, ai, settings):
        self.session_factory = session_factory
        self.ai = ai
        self.settings = settings
        # One local server process. Serialize retries/double-clicks per session.
        self.locks = defaultdict(Lock)
        self.preparations = PreparationService(session_factory)
        self.preparations.backfill()

    @staticmethod
    def require(db, model, identifier):
        item = db.get(model, identifier)
        if not item:
            raise ServiceError("找不到这条记录。", 404)
        return item

    @staticmethod
    def turns(db, identifier):
        return list(db.scalars(select(InterviewTurn).where(
            InterviewTurn.session_id == identifier,
        ).order_by(InterviewTurn.turn_index)))

    @staticmethod
    def report(db, identifier):
        return db.scalar(select(InterviewReport).where(InterviewReport.session_id == identifier))

    def check_provider(self, session):
        if session.provider_mode != self.settings.ai_mode:
            raise ServiceError("该面试使用的服务与当前配置不同，请在 API 设置中切回原服务后继续。", 409)
        profile = session.state_json.get("provider_config")
        if profile and profile != self.provider_config():
            raise ServiceError("本场面试的模型或接口地址与当前配置不同，请恢复原配置或开始新面试。", 409)

    def provider_config(self):
        return {"provider": self.settings.ai_mode, "base_url": self.settings.resolved_base_url,
                "model": "" if self.settings.ai_mode == "demo" else self.settings.main_model,
                "api_format": self.settings.resolved_api_format}

    def create(self, payload):
        with self.session_factory() as db:
            resume = self.require(db, Resume, payload.resume_id)
            if not resume.parsed_json:
                raise ServiceError("请先完成简历解析。", 409)
            preparation = self.preparations.require(db, payload.preparation_id) if payload.preparation_id else None
            research = self.preparations.validate_reuse(preparation, payload, self.settings.ai_mode) if preparation else None
            state = {"provider_config": self.provider_config()}
            state["mode"] = payload.mode
            if payload.mode == "training":
                state["training_plan"] = {"mode": "专项练习", "focus": payload.training_focus,
                                          "duration_minutes": payload.duration_minutes,
                                          "exercises": [payload.training_focus]}
            elif payload.mode == "group":
                state["group_config"] = {"size": payload.group_size, "topic": payload.group_topic}
            if preparation:
                state.update(preparation_id=preparation.id, research_origin="reused",
                             researched_at=self.preparations.timestamp(preparation.researched_at))
            session = InterviewSession(
                resume_id=payload.resume_id, company=payload.company, position=payload.position,
                interview_type=payload.interview_type, notes=payload.notes,
                duration_target=payload.duration_minutes * 60, provider_mode=self.settings.ai_mode,
                state_json=state, research_json=research, status="researched" if preparation else "created",
            )
            db.add(session)
            db.commit()
            return self.view(db, session)

    def create_practice(self, identifier, payload):
        with self.locks[identifier], self.session_factory() as db:
            source = self.require(db, InterviewSession, identifier)
            existing = db.scalar(select(InterviewSession).where(
                InterviewSession.state_json["practice_from"].as_string() == identifier,
                InterviewSession.state_json["practice_request_id"].as_string() == str(payload.request_id)))
            if existing:
                return self.view(db, existing)
            report = self.report(db, identifier)
            if source.status != "completed" or not report or not any(turn.answer is not None for turn in self.turns(db, identifier)):
                raise ServiceError("请先完成一场作答并生成报告，再按本轮报告复练。", 409)
            if (source.provider_mode == "demo") != (self.settings.ai_mode == "demo"):
                raise ServiceError("演示记录只能用于离线复练；真实面试请使用真实记录或从设置页开始专项练习。", 409)
            self.require(db, Resume, source.resume_id)
            if source.research_json is None:
                raise ServiceError("原场次的准备资料缺失，请从面试设置开始新的专项练习。", 409)
            plan = practice_plan(report.report_json, source)
            group = source.state_json.get("mode") == "group"
            session = InterviewSession(
                resume_id=source.resume_id, company=source.company, position=source.position,
                interview_type=source.interview_type, notes=source.notes,
                duration_target=plan["duration_minutes"] * 60, provider_mode=self.settings.ai_mode,
                status="prepared" if group else "researched", research_json=deepcopy(Research.model_validate(source.research_json).model_dump()),
                state_json={"provider_config": self.provider_config(), "mode": "group" if group else "training",
                            "training_plan": plan, "training_basis": practice_basis(report.report_json),
                            "practice_from": identifier, "practice_request_id": str(payload.request_id),
                            "preparation_id": source.state_json.get("preparation_id"),
                            "research_origin": "reused", "researched_at": source.state_json.get("researched_at")},
            )
            if group:
                session.state_json = {**session.state_json, "group_config": deepcopy(source.state_json["group_config"]),
                                      "group_scenario": deepcopy(source.state_json["group_scenario"]), "group_practice_targets": plan}
            db.add(session)
            db.commit()
            return self.view(db, session)

    @staticmethod
    def pause_seconds(session, ended):
        started = session.state_json.get("training_pause_started_at")
        return max(0, (ended - datetime.fromisoformat(started)).total_seconds()) if started else 0

    def resume_training(self, session):
        paused = self.pause_seconds(session, now())
        session.state_json = {**session.state_json,
                              "training_paused_seconds": session.state_json.get("training_paused_seconds", 0) + paused,
                              "training_pause_started_at": None, "pending_question": None}

    def context(self, db, session):
        turns = self.turns(db, session.id)
        elapsed = 0
        if session.started_at:
            started = session.started_at.replace(tzinfo=timezone.utc)
            ended = session.finished_at.replace(tzinfo=timezone.utc) if session.finished_at else now()
            elapsed = max(0, int((ended - started).total_seconds()))
            if session.state_json.get("mode") == "training":
                elapsed = max(0, int(elapsed - session.state_json.get("training_paused_seconds", 0) - self.pause_seconds(session, ended)))
        count = sum(turn.answer is not None for turn in turns)
        mode = session.state_json.get("mode", "simulation")
        max_answers = max(6, min(30, session.duration_target // 120 + 2))
        if mode == "group":
            max_answers = max(4, min(10, session.duration_target // 180 + 2))
        return {
            "resume": self.require(db, Resume, session.resume_id).parsed_json,
            "company": session.company, "position": session.position,
            "interview_type": session.interview_type, "mode": session.state_json.get("mode", "simulation"), "notes": session.notes,
            "training_plan": session.state_json.get("training_plan"),
            "training_basis": session.state_json.get("training_basis"),
            "group_config": session.state_json.get("group_config"),
            "group_scenario": session.state_json.get("group_scenario"),
            "group_closing": session.state_json.get("group_closing"),
            "group_practice_targets": session.state_json.get("group_practice_targets"),
            "research": session.research_json, "strategy": session.strategy_json,
            "state": {**{key: value for key, value in session.state_json.items() if key not in {
                          "training_plan", "training_basis", "group_scenario", "group_closing", "group_practice_targets", "pending_question"}},
                      "elapsed_seconds": elapsed, "turn_count": count,
                      "target_duration_seconds": session.duration_target},
            "turns": [{"turn_index": turn.turn_index, "question": turn.question,
                       "answer": turn.answer, "topic": turn.topic, "phase": turn.phase,
                       "answer_duration": turn.answer_duration,
                       "group_round": turn.group_json,
                       "followup_depth": turn.followup_depth} for turn in turns],
            "constraints": {
                "must_end": elapsed >= session.duration_target * 1.1 or count >= max_answers,
                "prepare_closing": elapsed >= session.duration_target * 0.9 or count >= max_answers - (1 if mode == "group" else 2),
                "must_switch_topic": bool(turns and turns[-1].followup_depth >= 3),
                "max_followup_depth": 3, "max_answers": max_answers,
            },
        }

    def prepare(self, identifier):
        with self.locks[identifier], self.session_factory() as db:
            session = self.require(db, InterviewSession, identifier)
            self.check_provider(session)
            if session.status == "prepared":
                return self.view(db, session)
            if session.status not in {"created", "researched"}:
                raise ServiceError("当前面试已开始或结束，不能重新准备。", 409)
            if session.research_json is None:
                session.research_json = self.ai.research_interview(self.context(db, session)).model_dump()
                session.status = "researched"
                self.preparations.save(db, session, researched_at=now())
                db.commit()
            if session.state_json.get("mode") == "group":
                if not session.state_json.get("group_scenario"):
                    context = self.context(db, session)
                    scenario = self.ai.generate_group_scenario(context, lambda value: validate_scenario(value, context))
                    validate_scenario(scenario, context)
                    session.state_json = {**session.state_json, "group_scenario": scenario.model_dump()}
                session.status = "prepared"
                db.commit()
                return self.view(db, session)
            if session.strategy_json is None:
                session.strategy_json = self.ai.generate_strategy(self.context(db, session)).model_dump()
                session.status = "prepared"
                db.commit()
            return self.view(db, session)

    @staticmethod
    def validate_question(question, context):
        turns = context["turns"]
        if context["constraints"]["must_end"] and question.action != "end":
            raise ValueError("must end")
        if question.action == "end":
            if question.phase != "closing" or question.question.strip():
                raise ValueError("invalid closing")
            if not turns:
                raise ValueError("cannot end before opening")
            return
        if not question.question.strip() or not question.topic.strip() or question.phase == "closing":
            raise ValueError("empty question/topic or closing phase")
        normalized = "".join(question.question.split())
        if any(normalized == "".join(turn["question"].split()) for turn in turns):
            raise ValueError("duplicate question")
        if question.action == "new_topic":
            if question.followup_depth != 0 or (turns and question.topic == turns[-1]["topic"]):
                raise ValueError("new topic must reset depth and change topic")
        elif not turns or context["constraints"]["must_switch_topic"]:
            raise ValueError("must open or switch topic")
        elif question.topic != turns[-1]["topic"] or question.followup_depth != turns[-1]["followup_depth"] + 1:
            raise ValueError("follow_up/deepen/clarify 必须逐字保留最近一题的 topic，并将 followup_depth 加1；改变 topic 时应选 new_topic 且深度为0")

    def next_question(self, db, session):
        context = self.context(db, session)
        if context["constraints"]["must_end"]:
            return Question(action="end", question="", topic="closing", phase="closing", followup_depth=0, decision_note="达到程序结束条件")
        return self.ai.generate_question(context, lambda value: self.validate_question(value, context))

    def save_question(self, db, session, question):
        turns = self.turns(db, session.id)
        topics = {**session.state_json.get("topics", {}), question.topic: "in_progress"}
        if turns and turns[-1].topic != question.topic:
            topics[turns[-1].topic] = "done"
        db.add(InterviewTurn(
            session_id=session.id, turn_index=len(turns) + 1,
            question=question.question.strip(), topic=question.topic, phase=question.phase,
            action=question.action, followup_depth=question.followup_depth,
            decision_note=question.decision_note,
        ))
        session.state_json = {**session.state_json, "phase": question.phase, "current_topic": question.topic,
                              "followup_depth": question.followup_depth, "topics": topics}
        db.commit()

    def next_group_round(self, db, session):
        context = round_context(self.context(db, session))
        result = self.ai.generate_group_round(context, lambda value: validate_round(value, context))
        validate_round(result, context)
        return result

    def save_group_round(self, db, session, result):
        index = len(self.turns(db, session.id)) + 1
        question = round_question(result, index)
        db.add(InterviewTurn(session_id=session.id, turn_index=index, question=question.question,
                             topic=question.topic, phase=question.phase, action=question.action,
                             followup_depth=0, decision_note=question.decision_note, group_json=result.model_dump()))
        session.state_json = {**session.state_json, "group_stage": result.stage, "phase": "behavioral"}
        db.commit()

    def start(self, identifier):
        with self.locks[identifier], self.session_factory() as db:
            session = self.require(db, InterviewSession, identifier)
            self.check_provider(session)
            if session.status == "active":
                return self.view(db, session)
            if session.status != "prepared":
                raise ServiceError("请先完成面试准备。", 409)
            if session.state_json.get("mode") == "group":
                result = self.next_group_round(db, session)
                session.started_at = now()
                session.status = "active"
                self.save_group_round(db, session, result)
                return self.view(db, session)
            question = self.next_question(db, session)
            session.started_at = now()
            session.status = "active"
            self.save_question(db, session, question)
            return self.view(db, session)

    def submit(self, identifier, payload):
        with self.locks[identifier], self.session_factory() as db:
            session = self.require(db, InterviewSession, identifier)
            self.check_provider(session)
            turns = self.turns(db, identifier)
            turn = next((item for item in turns if item.turn_index == payload.turn_index), None)
            if not turn:
                raise ServiceError("题号不正确，请刷新当前面试。", 409)
            if turn.answer is not None:
                if turn.answer != payload.answer:
                    raise ServiceError("这道题的回答已经保存，不能用不同文本覆盖，请刷新查看。", 409)
                if session.status == "completed" or turn.turn_index != turns[-1].turn_index:
                    return self.view(db, session)
                if session.state_json.get("mode") == "training" and turn.feedback_json:
                    return self.view(db, session)
                # Exact retry of saved answer after next-question failure.
            elif session.status != "active" or turn.turn_index != turns[-1].turn_index:
                raise ServiceError("面试当前不接受此回答。", 409)
            else:
                turn.answer = payload.answer
                turn.answered_at = now()
                db.commit()  # Preserve answer even if the API fails below.
            if session.state_json.get("mode") == "group":
                result = self.next_group_round(db, session)
                if result.stage == "closing":
                    session.state_json = {**session.state_json, "group_closing": result.model_dump(), "group_stage": "closing"}
                    self.complete(db, session)
                    self.ensure_report(db, session)
                else:
                    self.save_group_round(db, session, result)
                return self.view(db, session)
            if session.state_json.get("mode") == "training":
                context = self.context(db, session)
                def validate(step):
                    validate_answer_review(step.feedback, {turn.turn_index: turn.answer})
                    self.validate_question(step.next_question, context)
                result = self.ai.generate_training_step(context, validate)
                validate(result)
                turn.feedback_json = result.feedback.model_dump()
                session.state_json = {**session.state_json, "pending_question": result.next_question.model_dump(),
                                      "training_pause_started_at": now().isoformat()}
                db.commit()
                return self.view(db, session)
            question = self.next_question(db, session)
            if question.action == "end":
                self.complete(db, session)
                self.ensure_report(db, session)
            else:
                self.save_question(db, session, question)
            return self.view(db, session)

    def continue_training(self, identifier, payload, repeat=False):
        with self.locks[identifier], self.session_factory() as db:
            session = self.require(db, InterviewSession, identifier)
            self.check_provider(session)
            if session.state_json.get("mode") != "training":
                raise ServiceError("此操作只适用于专项练习。", 409)
            turns = self.turns(db, identifier)
            current = turns[-1] if turns else None
            if current and payload.turn_index < current.turn_index:
                return self.view(db, session)  # Double-click/retry after advancing.
            if session.status == "completed":
                return self.view(db, session)
            if session.status != "active" or not current or payload.turn_index != current.turn_index or not current.feedback_json:
                raise ServiceError("请先提交本题并完成讲评；讲评失败时可重试已保存的回答。", 409)
            pending = session.state_json.get("pending_question")
            if not pending:
                raise ServiceError("下一题未准备完成，请刷新后重试。", 409)
            question = Question.model_validate(pending)
            must_end = self.context(db, session)["constraints"]["must_end"]
            if repeat:
                if must_end:
                    raise ServiceError("已达到本轮练习上限，请结束生成报告后再练一轮。", 409)
                question = Question(action="new_topic", question=current.question,
                    topic=f"repeat_{current.turn_index}_{len(turns) + 1}", phase=current.phase,
                    followup_depth=0, decision_note="用户重答本题")
                session.state_json = {**session.state_json, "repeated_turns": {
                    **session.state_json.get("repeated_turns", {}), str(len(turns) + 1): current.turn_index}}
            elif must_end:
                question = Question(action="end", question="", topic="closing", phase="closing", followup_depth=0, decision_note="达到程序结束条件")
            self.resume_training(session)
            if question.action == "end":
                self.complete(db, session)
                self.ensure_report(db, session)
            else:
                self.save_question(db, session, question)
            return self.view(db, session)

    @staticmethod
    def complete(db, session):
        session.status = "completed"
        session.finished_at = session.finished_at or now()
        session.state_json = {**session.state_json, "phase": "closing"}
        db.commit()

    def ensure_report(self, db, session, upgrade=False):
        existing = self.report(db, session.id)
        if existing and (not upgrade or existing.report_json.get("version", 1) >= REPORT_VERSION):
            return
        context = self.context(db, session)
        context["oral_analysis"] = oral_analysis(self.turns(db, session.id))
        answered = [turn for turn in context["turns"] if turn["answer"] is not None]
        if not answered:
            result = {"overall_score": None, "summary": "本次面试未提交回答，无法评价。",
                      "strengths": [], "improvements": [], "scores": [],
                      "limitations": "没有作答样本；不能评价语音表现。",
                      "answer_reviews": [], "priority_practice": [], "knowledge_gaps": [], "next_practice": None}
        else:
            generated = self.ai.generate_report(context)
            validate_report(generated, context)
            result = generated.model_dump()
        result.update(version=REPORT_VERSION, oral_analysis=context["oral_analysis"])
        if context["mode"] == "group":
            result["group_statistics"] = group_statistics(context)
        if existing:
            existing.report_json = result
        else:
            db.add(InterviewReport(session_id=session.id, report_json=result))
        db.commit()

    def upgrade_report(self, identifier):
        with self.locks[identifier], self.session_factory() as db:
            session = self.require(db, InterviewSession, identifier)
            if session.status != "completed":
                raise ServiceError("请先结束面试，再生成复盘报告。", 409)
            existing = self.report(db, identifier)
            if not existing or existing.report_json.get("version", 1) < REPORT_VERSION:
                self.check_provider(session)
                self.ensure_report(db, session, upgrade=True)
            return self.view(db, session)

    def finish(self, identifier):
        with self.locks[identifier], self.session_factory() as db:
            session = self.require(db, InterviewSession, identifier)
            self.check_provider(session)
            if session.status not in {"active", "completed"}:
                raise ServiceError("面试尚未开始。", 409)
            self.complete(db, session)
            self.ensure_report(db, session)
            return self.view(db, session)

    def get(self, identifier):
        with self.session_factory() as db:
            return self.view(db, self.require(db, InterviewSession, identifier))

    def get_report(self, identifier):
        with self.session_factory() as db:
            self.require(db, InterviewSession, identifier)
            result = self.report(db, identifier)
            if not result:
                raise ServiceError("报告尚未生成，请结束面试或重试生成报告。", 409)
            return self.public_report(result, self.turns(db, identifier))

    @staticmethod
    def public_report(report, turns):
        if not report:
            return None
        return {**report.report_json, "oral_analysis": oral_analysis(turns)}

    @staticmethod
    def timestamp(value):
        return value.replace(tzinfo=timezone.utc).isoformat() if value else None

    def history(self, limit=20, offset=0):
        with self.session_factory() as db:
            rows = db.execute(select(InterviewSession, InterviewReport.report_json,
                select(func.count(InterviewTurn.id)).where(
                    InterviewTurn.session_id == InterviewSession.id, InterviewTurn.answer.is_not(None)
                ).correlate(InterviewSession).scalar_subquery()
            ).outerjoin(InterviewReport, InterviewReport.session_id == InterviewSession.id)
                .order_by(InterviewSession.created_at.desc(), InterviewSession.id.desc())
                .limit(limit).offset(offset)).all()
            total = db.scalar(select(func.count()).select_from(InterviewSession))
            return {"total": total, "limit": limit, "offset": offset, "items": [{
                "id": session.id, "company": session.company, "position": session.position,
                "interview_type": session.interview_type, "status": session.status,
                "mode": session.state_json.get("mode", "simulation"),
                "provider_mode": session.provider_mode, "created_at": self.timestamp(session.created_at),
                "finished_at": self.timestamp(session.finished_at), "answered_count": count,
                "preparation_id": session.state_json.get("preparation_id") if session.state_json.get("preparation_id") and db.get(InterviewPreparation, session.state_json["preparation_id"]) else None,
                "has_report": report is not None, "overall_score": report.get("overall_score") if report else None,
            } for session, report, count in rows]}

    @staticmethod
    def safe_path(path, root):
        path = Path(path)
        resolved = path.resolve()
        if resolved == root or not resolved.is_relative_to(root):
            raise ServiceError("记录的文件位置异常，已停止删除，避免影响其他文件。", 409)
        return path

    def remove(self, identifier):
        with self.locks[identifier], self.session_factory() as db:
            session = self.require(db, InterviewSession, identifier)
            # Validate all targets before any filesystem mutation, including old recording attempts.
            try:
                if str(UUID(session.id)) != session.id:
                    raise ValueError()
            except ValueError:
                raise ServiceError("记录标识异常，无法安全删除。", 409)
            root = self.settings.data_dir.resolve()
            recordings_root = self.safe_path(root / "interviews", root).resolve()
            directory = self.safe_path(recordings_root / session.id, recordings_root)
            for turn in self.turns(db, identifier):
                if turn.audio_path:
                    self.safe_path(turn.audio_path, directory.resolve())
            if directory.exists():
                for parent, dirs, files in os.walk(directory, followlinks=False):
                    for name in dirs + files:
                        self.safe_path(Path(parent) / name, directory.resolve())
            resume = self.require(db, Resume, session.resume_id)
            shared = db.scalar(select(func.count()).select_from(InterviewSession).where(
                InterviewSession.resume_id == resume.id, InterviewSession.id != identifier)) > 0
            resume_path = None
            if not shared:
                resumes_root = self.safe_path(root / "resumes", root).resolve()
                resume_path = self.safe_path(resume.file_path, resumes_root)
                if resume_path.resolve().parent != resumes_root or resume_path.stem != resume.id:
                    raise ServiceError("简历文件位置异常，已停止删除。", 409)
            try:
                if directory.exists():
                    shutil.rmtree(directory)
                if resume_path:
                    resume_path.unlink(missing_ok=True)
            except OSError as exc:
                raise ServiceError("文件清理未完成，记录仍保留；部分文件可能已删除，请关闭录音播放后重试。", 409) from exc
            try:
                db.execute(delete(InterviewReport).where(InterviewReport.session_id == identifier))
                db.execute(delete(InterviewTurn).where(InterviewTurn.session_id == identifier))
                db.delete(session)
                db.flush()
                if not shared:
                    db.delete(resume)
                db.commit()
            except Exception as exc:
                db.rollback()
                raise ServiceError("文件已清理，但记录删除未完成，请重试。", 500) from exc
            return {"deleted": True, "resume_deleted": not shared}

    def view(self, db, session):
        turns = self.turns(db, session.id)
        report = self.report(db, session.id)
        current = turns[-1] if turns and session.status == "active" else None
        training = session.state_json.get("mode") == "training"
        paused = bool(training and current and current.answer is not None and current.feedback_json)
        must_end = self.context(db, session)["constraints"]["must_end"] if paused else False
        resume = self.require(db, Resume, session.resume_id)
        return {
            "id": session.id, "resume_id": session.resume_id,
            "resume": {"id": resume.id, "filename": resume.filename} if resume.parsed_json else None,
            "setup": {"company": session.company, "position": session.position,
                      "interview_type": session.interview_type, "duration_minutes": session.duration_target // 60,
                      "notes": session.notes, "mode": session.state_json.get("mode", "simulation"),
                      "training_focus": (session.state_json.get("training_plan") or {}).get("focus", ""),
                      "group_size": (session.state_json.get("group_config") or {}).get("size", 5),
                      "group_topic": (session.state_json.get("group_config") or {}).get("topic", "")},
            "company": session.company, "position": session.position,
            "interview_type": session.interview_type, "status": session.status,
            "provider_mode": session.provider_mode, "duration_target": session.duration_target,
            "mode": session.state_json.get("mode", "simulation"),
            "training_plan": session.state_json.get("training_plan"), "practice_from": session.state_json.get("practice_from"),
            "group_scenario": session.state_json.get("group_scenario"),
            "group_stage": session.state_json.get("group_stage"),
            "group_closing": session.state_json.get("group_closing"),
            "group_practice_targets": session.state_json.get("group_practice_targets"),
            "training_paused": paused,
            "training_will_end": bool(paused and (must_end or (session.state_json.get("pending_question") or {}).get("action") == "end")),
            "training_can_repeat": paused and not must_end,
            "can_practice": bool(session.status == "completed" and report and any(turn.answer is not None for turn in turns)),
            "preparation_id": session.state_json.get("preparation_id"),
            "research_origin": session.state_json.get("research_origin"),
            "created_at": self.timestamp(session.created_at), "finished_at": self.timestamp(session.finished_at),
            "report": self.public_report(report, turns),
            "needs_next_question": bool(current and current.answer is not None and not paused),
            "current_turn_index": current.turn_index if current else None,
            "audio_needs_transcription": bool(current and current.audio_path and current.answer_transcript_raw is None),
            "turns": [{"turn_index": turn.turn_index, "question": turn.question,
                       "answer": turn.answer, "topic": turn.topic, "action": turn.action,
                       "followup_depth": turn.followup_depth, "transcript_raw": turn.answer_transcript_raw,
                       "answer_duration": turn.answer_duration,
                       "feedback": turn.feedback_json if training else None,
                       "group_round": turn.group_json,
                       "repeat_of": session.state_json.get("repeated_turns", {}).get(str(turn.turn_index)),
                       "audio_url": f"/api/interviews/{session.id}/audio/{turn.turn_index}" if turn.audio_path else None} for turn in turns],
            # Decision notes/strategy stay internal; saved research is available in the preparation library.
        }

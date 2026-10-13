from copy import deepcopy
from datetime import timezone

from pydantic import ValidationError
from sqlalchemy import func, select

from app.models import InterviewPreparation, InterviewSession, Resume, now
from app.schemas import Research
from app.services.errors import ServiceError


class PreparationService:
    def __init__(self, session_factory):
        self.session_factory = session_factory

    @staticmethod
    def timestamp(value):
        return value.replace(tzinfo=timezone.utc).isoformat() if value else None

    @staticmethod
    def require(db, identifier):
        result = db.get(InterviewPreparation, identifier)
        if not result:
            raise ServiceError("这份准备资料已删除或不存在，请重新选择或重新联网准备。", 404)
        return result

    @staticmethod
    def research(preparation):
        try:
            return Research.model_validate(preparation.research_json).model_dump()
        except ValidationError as exc:
            raise ServiceError("保存的准备资料不完整，请选择重新联网准备。", 409) from exc

    @staticmethod
    def normalize(value):
        return " ".join(value.split()).casefold()

    def validate_reuse(self, preparation, payload, provider_mode):
        if any(self.normalize(getattr(preparation, key)) != self.normalize(getattr(payload, key))
               for key in ("company", "position", "interview_type")):
            raise ServiceError("公司、岗位或面试类型已改变，请取消复用资料并重新联网准备。", 409)
        if (preparation.provider_mode == "demo") != (provider_mode == "demo"):
            raise ServiceError("离线演示资料只能用于演示，请为真实面试重新联网准备。", 409)
        return deepcopy(self.research(preparation))

    def save(self, db, session, researched_at=None):
        preparation = InterviewPreparation(
            source_session_id=session.id, company=session.company, position=session.position,
            interview_type=session.interview_type, duration_target=session.duration_target,
            notes=session.notes, provider_mode=session.provider_mode,
            research_json=deepcopy(session.research_json), researched_at=researched_at,
        )
        db.add(preparation)
        db.flush()
        session.state_json = {**session.state_json, "preparation_id": preparation.id,
                              "research_origin": "fresh" if researched_at else "legacy_saved",
                              "researched_at": self.timestamp(researched_at)}
        return preparation

    def backfill(self):
        """Import old research once. Deleting a library entry never resurrects it."""
        with self.session_factory() as db:
            sessions = db.scalars(select(InterviewSession).where(InterviewSession.research_json.is_not(None)))
            for session in sessions:
                if session.state_json.get("preparation_id"):
                    continue
                try:
                    Research.model_validate(session.research_json)
                except ValidationError:
                    continue
                existing = db.scalar(select(InterviewPreparation).where(
                    InterviewPreparation.source_session_id == session.id))
                if existing:
                    session.state_json = {**session.state_json, "preparation_id": existing.id}
                else:
                    self.save(db, session)
            db.commit()

    def public(self, db, preparation, detail=False):
        result = {"id": preparation.id, "company": preparation.company, "position": preparation.position,
                  "interview_type": preparation.interview_type, "provider_mode": preparation.provider_mode,
                  "created_at": self.timestamp(preparation.created_at),
                  "researched_at": self.timestamp(preparation.researched_at),
                  "source_count": len(preparation.research_json.get("sources", []))}
        if detail:
            original = db.get(InterviewSession, preparation.source_session_id)
            resume = db.get(Resume, original.resume_id) if original else None
            result.update(research=self.research(preparation), settings={
                "company": preparation.company, "position": preparation.position,
                "interview_type": preparation.interview_type,
                "duration_minutes": preparation.duration_target // 60, "notes": preparation.notes,
                "mode": original.state_json.get("mode", "simulation") if original else "simulation",
                "training_focus": (original.state_json.get("training_plan") or {}).get("focus", "") if original else "",
            }, resume={"id": resume.id, "filename": resume.filename} if resume and resume.parsed_json else None)
            if original and original.state_json.get("mode") == "group":
                config = original.state_json["group_config"]
                result["settings"].update(group_size=config["size"], group_topic=config["topic"])
        return result

    def list(self, demo=False, limit=50, offset=0):
        with self.session_factory() as db:
            condition = InterviewPreparation.provider_mode == "demo" if demo else InterviewPreparation.provider_mode != "demo"
            rows = db.scalars(select(InterviewPreparation).where(condition)
                              .order_by(InterviewPreparation.created_at.desc(), InterviewPreparation.id.desc())
                              .limit(limit).offset(offset))
            total = db.scalar(select(func.count()).select_from(InterviewPreparation).where(condition))
            return {"total": total, "limit": limit, "offset": offset,
                    "items": [self.public(db, row) for row in rows]}

    def get(self, identifier):
        with self.session_factory() as db:
            return self.public(db, self.require(db, identifier), detail=True)

    def remove(self, identifier):
        with self.session_factory() as db:
            preparation = self.require(db, identifier)
            db.delete(preparation)
            db.commit()
            return {"deleted": True}

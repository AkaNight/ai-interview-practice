from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import JSON, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def now():
    return datetime.now(timezone.utc)


def new_id():
    return str(uuid4())


class Base(DeclarativeBase):
    pass


class Resume(Base):
    __tablename__ = "resumes"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=new_id)
    filename: Mapped[str] = mapped_column(String)
    file_path: Mapped[str] = mapped_column(String)
    parsed_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)


class InterviewSession(Base):
    __tablename__ = "interview_sessions"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=new_id)
    resume_id: Mapped[str] = mapped_column(ForeignKey("resumes.id"))
    company: Mapped[str] = mapped_column(String)
    position: Mapped[str] = mapped_column(String)
    interview_type: Mapped[str] = mapped_column(String)
    notes: Mapped[str] = mapped_column(Text, default="")
    duration_target: Mapped[int] = mapped_column(Integer)
    provider_mode: Mapped[str] = mapped_column(String)
    status: Mapped[str] = mapped_column(String, default="created")
    research_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    strategy_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    state_json: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)
    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class InterviewTurn(Base):
    __tablename__ = "interview_turns"
    __table_args__ = (UniqueConstraint("session_id", "turn_index"),)
    id: Mapped[str] = mapped_column(String, primary_key=True, default=new_id)
    session_id: Mapped[str] = mapped_column(ForeignKey("interview_sessions.id"))
    turn_index: Mapped[int] = mapped_column(Integer)
    question: Mapped[str] = mapped_column(Text)
    answer: Mapped[str | None] = mapped_column(Text, nullable=True)
    topic: Mapped[str] = mapped_column(String)
    phase: Mapped[str] = mapped_column(String)
    action: Mapped[str] = mapped_column(String)
    followup_depth: Mapped[int] = mapped_column(Integer)
    decision_note: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)
    answered_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    audio_path: Mapped[str | None] = mapped_column(String, nullable=True)
    audio_mime: Mapped[str | None] = mapped_column(String, nullable=True)
    answer_transcript_raw: Mapped[str | None] = mapped_column(Text, nullable=True)
    answer_duration: Mapped[float | None] = mapped_column(nullable=True)
    transcription_provider: Mapped[str | None] = mapped_column(String, nullable=True)
    transcription_model: Mapped[str | None] = mapped_column(String, nullable=True)
    recording_id: Mapped[str | None] = mapped_column(String, nullable=True)
    feedback_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    group_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)


class InterviewReport(Base):
    __tablename__ = "interview_reports"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=new_id)
    session_id: Mapped[str] = mapped_column(ForeignKey("interview_sessions.id"), unique=True)
    report_json: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)


class InterviewPreparation(Base):
    __tablename__ = "interview_preparations"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=new_id)
    source_session_id: Mapped[str] = mapped_column(String, unique=True)
    company: Mapped[str] = mapped_column(String)
    position: Mapped[str] = mapped_column(String)
    interview_type: Mapped[str] = mapped_column(String)
    duration_target: Mapped[int] = mapped_column(Integer)
    notes: Mapped[str] = mapped_column(Text, default="")
    provider_mode: Mapped[str] = mapped_column(String)
    research_json: Mapped[dict] = mapped_column(JSON)
    researched_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)


class UsageEvent(Base):
    __tablename__ = "usage_events"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=new_id)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now, index=True)
    details: Mapped[dict] = mapped_column(JSON)


class UsagePolicy(Base):
    __tablename__ = "usage_policy"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)
    settings_json: Mapped[dict] = mapped_column(JSON)

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from uuid import UUID


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class BasicInfo(StrictModel):
    name: str
    summary: str


class Project(StrictModel):
    name: str
    overall_work: str
    personal_contribution: str
    technologies: list[str]
    results: str


class ParsedResume(StrictModel):
    basic_info: BasicInfo
    education: list[str]
    skills: list[str]
    projects: list[Project]
    research: list[str]
    publications: list[str]
    internships: list[str]
    awards: list[str]
    resume_risk_points: list[str]
    likely_interview_topics: list[str]


class Source(StrictModel):
    title: str
    url: str


class Research(StrictModel):
    company_summary: str
    position_summary: str
    interview_process: list[str]
    common_question_types: list[str]
    technical_topics: list[str]
    company_specific_topics: list[str]
    behavioral_topics: list[str]
    uncertainties: list[str]
    sources: list[Source]
    interview_preferences: list[str] = Field(default_factory=list)


class Strategy(StrictModel):
    primary_focus: list[str]
    resume_focus: list[str]
    technical_focus: list[str]
    behavioral_focus: list[str]
    company_focus: list[str]
    potential_resume_risks: list[str]
    recommended_flow: list[str]


class Question(StrictModel):
    action: Literal["follow_up", "deepen", "clarify", "new_topic", "end"]
    question: str
    topic: str
    phase: Literal["self_intro", "resume", "projects", "technical", "behavioral", "company_and_position", "candidate_questions", "closing"]
    followup_depth: int = Field(ge=0, le=3)
    decision_note: str


class Score(StrictModel):
    dimension: str
    score: int = Field(ge=0, le=10)
    evidence: str


class ReviewPoint(StrictModel):
    observation: str
    evidence: str = Field(min_length=1)
    suggestion: str


class AnswerReview(StrictModel):
    turn_index: int = Field(ge=1)
    summary: str
    strengths: list[ReviewPoint]
    issues: list[ReviewPoint]
    risks: list[ReviewPoint]
    suggested_structure: list[str]
    timing_advice: str


class TrainingStep(StrictModel):
    feedback: AnswerReview
    next_question: Question


MemberID = Literal["member_1", "member_2", "member_3", "member_4", "member_5"]


class GroupMember(StrictModel):
    member_id: MemberID
    name: str = Field(min_length=1, max_length=30)
    style: str = Field(min_length=1, max_length=200)


class GroupScenario(StrictModel):
    title: str = Field(min_length=1, max_length=160)
    background: str = Field(min_length=1, max_length=1600)
    task: str = Field(min_length=1, max_length=600)
    rules: list[str] = Field(min_length=1, max_length=6)
    teammates: list[GroupMember] = Field(min_length=2, max_length=5)
    fictional: bool


class GroupMessage(StrictModel):
    member_id: MemberID
    speech: str = Field(min_length=1, max_length=600)
    reply_to_quote: str | None = Field(max_length=200)


class GroupRound(StrictModel):
    stage: Literal["opening", "discussion", "summary", "closing"]
    messages: list[GroupMessage] = Field(max_length=5)
    user_prompt: str = Field(max_length=300)
    discussion_summary: str = Field(max_length=500)


class TurnEvidence(StrictModel):
    turn_index: int = Field(ge=1)
    quote: str = Field(min_length=1)


class KnowledgeGap(StrictModel):
    topic: str
    priority: Literal["high", "medium"]
    reason: str
    evidence: list[TurnEvidence] = Field(min_length=1)
    review_suggestions: list[str]


class NextPractice(StrictModel):
    mode: str
    focus: str
    duration_minutes: int = Field(ge=5, le=60)
    exercises: list[str]


class Report(StrictModel):
    overall_score: int | None = Field(ge=0, le=100)
    summary: str
    strengths: list[str]
    improvements: list[str]
    scores: list[Score]
    limitations: str
    answer_reviews: list[AnswerReview] = Field(default_factory=list)
    priority_practice: list[str] = Field(default_factory=list, max_length=3)
    knowledge_gaps: list[KnowledgeGap] = Field(default_factory=list)
    next_practice: NextPractice | None = None


class CreateInterview(StrictModel):
    resume_id: str
    preparation_id: str | None = None
    company: str = Field(min_length=1, max_length=200)
    position: str = Field(min_length=1, max_length=200)
    interview_type: Literal["综合模拟", "技术面", "HR面", "半结构化面试", "简历面", "压力面"] = "综合模拟"
    duration_minutes: int = Field(default=20, ge=5, le=60)
    notes: str = Field(default="", max_length=3000)
    mode: Literal["simulation", "training", "group"] = "simulation"
    training_focus: str = Field(default="", max_length=500)
    group_topic: str = Field(default="", max_length=1000)
    group_size: int = Field(default=5, ge=3, le=6)

    @model_validator(mode="after")
    def require_training_focus(self):
        self.training_focus = self.training_focus.strip()
        self.group_topic = self.group_topic.strip()
        if self.mode == "training" and not self.training_focus:
            raise ValueError("专项练习需要填写练习重点")
        return self

    @field_validator("company", "position")
    @classmethod
    def nonblank(cls, value):
        value = value.strip()
        if not value:
            raise ValueError("请输入有效的公司和岗位")
        return value


class SubmitAnswer(StrictModel):
    turn_index: int = Field(ge=1)
    answer: str = Field(min_length=1, max_length=15000)

    @field_validator("answer")
    @classmethod
    def nonblank(cls, value):
        value = value.strip()
        if not value:
            raise ValueError("回答不能为空")
        return value


class ContinueTraining(StrictModel):
    turn_index: int = Field(ge=1)


class CreatePractice(StrictModel):
    request_id: UUID


class UpdateAPISettings(StrictModel):
    provider: Literal["demo", "openai", "deepseek", "compatible"]
    base_url: str = Field(default="", max_length=500)
    main_model: str = Field(default="", max_length=150)
    api_key: str = Field(default="", max_length=500, repr=False)
    clear_key: bool = False
    api_format: Literal["auto", "chat", "responses"] = "auto"


class UpdateVoiceSettings(StrictModel):
    provider: Literal["siliconflow", "compatible"] = "siliconflow"
    base_url: str = Field(default="", max_length=500)
    model: str = Field(default="", max_length=150)
    api_key: str = Field(default="", max_length=500, repr=False)
    clear_key: bool = False

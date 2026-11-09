"""Pydantic models: the single source of truth for API contracts and LLM output validation."""
from datetime import datetime
from typing import Annotated

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, StringConstraints, field_validator

ShortText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
LongText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=600)]


def _coerce_score(v):
    # LLMs sometimes return "85", 85.0 or "85/100". Accept those, reject anything else.
    if isinstance(v, str):
        v = v.strip().split("/")[0].strip().rstrip("%")
    if isinstance(v, (int, float, str)):
        try:
            f = float(v)
        except ValueError:
            return v  # let pydantic raise a proper validation error
        if f.is_integer():
            return int(f)
        return round(f)
    return v


Score = Annotated[int, BeforeValidator(_coerce_score), Field(ge=0, le=100)]


class MatchingSkill(BaseModel):
    skill: ShortText
    evidence: LongText = Field(description="Short quote or paraphrase from the resume proving the skill")


class MatchReport(BaseModel):
    """Exactly what the LLM must return. Anything else is rejected before display."""

    model_config = ConfigDict(extra="ignore")

    score: Score
    score_rationale: LongText
    matching_skills: list[MatchingSkill] = Field(min_length=1, max_length=5)
    gaps: list[ShortText] = Field(min_length=1, max_length=3)
    interview_questions: list[LongText] = Field(min_length=3, max_length=3)
    summary: LongText
    injection_attempt_detected: bool = False

    @field_validator("matching_skills")
    @classmethod
    def _dedupe_skills(cls, skills: list[MatchingSkill]) -> list[MatchingSkill]:
        seen, out = set(), []
        for s in skills:
            key = s.skill.lower()
            if key not in seen:
                seen.add(key)
                out.append(s)
        return out

    @field_validator("gaps", "interview_questions")
    @classmethod
    def _no_duplicates(cls, items: list[str]) -> list[str]:
        if len({i.lower() for i in items}) != len(items):
            raise ValueError("items must be unique")
        return items


class AnalyzeRequest(BaseModel):
    candidate_name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=120)]
    job_description: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200_000)]
    resume: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200_000)]


class AnalysisMeta(BaseModel):
    provider: str
    model: str
    latency_ms: int
    jd_truncated: bool
    resume_truncated: bool
    pii_redacted: bool
    injection_suspected: bool
    injection_signals: list[str] = []


class AnalysisRecord(BaseModel):
    id: int
    candidate_name: str
    score: int
    report: MatchReport
    jd_excerpt: str
    meta: AnalysisMeta
    created_at: datetime


class AnalysisSummary(BaseModel):
    id: int
    candidate_name: str
    score: int
    jd_excerpt: str
    injection_suspected: bool
    created_at: datetime


class AnalysisList(BaseModel):
    items: list[AnalysisSummary]
    total: int
    limit: int
    offset: int


class ErrorResponse(BaseModel):
    error: str
    detail: str | None = None

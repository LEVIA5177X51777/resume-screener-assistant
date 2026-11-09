"""Analysis pipeline: validation, preprocessing, LLM call and persistence."""

from .config import Settings
from .db import Database
from .llm import Provider, analyze
from .schemas import AnalysisMeta, AnalysisRecord, AnalyzeRequest
from .text_utils import (
    InputError,
    detect_injection,
    excerpt,
    neutralize_delimiters,
    normalize,
    redact_pii,
    sha256,
    truncate,
)


def run_analysis(req: AnalyzeRequest, provider: Provider, db: Database, settings: Settings) -> AnalysisRecord:
    jd = normalize(req.job_description)
    resume = normalize(req.resume)
    for label, text in (("Job description", jd), ("Resume", resume)):
        if len(text) < settings.min_input_chars:
            raise InputError(f"{label} is too short (minimum {settings.min_input_chars} characters).")

    injection_signals = sorted(set(detect_injection(resume)) | {f"jd:{s}" for s in detect_injection(jd)})

    llm_jd, llm_resume = jd, resume
    if settings.redact_pii:
        llm_resume = redact_pii(llm_resume, req.candidate_name)
    llm_jd, jd_truncated = truncate(neutralize_delimiters(llm_jd), settings.max_input_chars)
    llm_resume, resume_truncated = truncate(neutralize_delimiters(llm_resume), settings.max_input_chars)

    result = analyze(provider, llm_jd, llm_resume)

    if result.report.injection_attempt_detected and "model_flagged" not in injection_signals:
        injection_signals.append("model_flagged")

    meta = AnalysisMeta(
        provider=result.provider,
        model=result.model,
        latency_ms=result.latency_ms,
        jd_truncated=jd_truncated,
        resume_truncated=resume_truncated,
        pii_redacted=settings.redact_pii,
        injection_suspected=bool(injection_signals),
        injection_signals=injection_signals,
    )
    return db.insert(
        candidate_name=req.candidate_name,
        report=result.report,
        jd_excerpt=excerpt(jd),
        jd_hash=sha256(jd),
        resume_hash=sha256(resume),
        meta=meta,
        jd_text=jd if settings.store_raw_text else None,
        resume_text=resume if settings.store_raw_text else None,
    )

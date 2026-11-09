"""LLM providers, retry handling and validated report generation."""

import json
import logging
import random
import re
import time
from dataclasses import dataclass
from typing import Protocol

import httpx
from pydantic import ValidationError

from .config import Settings
from .prompts import RESPONSE_SCHEMA, SYSTEM_PROMPT, build_repair_prompt, build_user_prompt
from .schemas import MatchReport

log = logging.getLogger("screener.llm")
logging.getLogger("httpx").setLevel(logging.WARNING)

RETRYABLE_STATUS = {408, 409, 429, 500, 502, 503, 504}
MAX_REPAIR_ATTEMPTS = 1


class LLMError(Exception):
    """Base LLM error. `user_message` is safe to display to end users."""

    user_message = "The AI service failed. Please try again."
    http_status = 502

    def __init__(self, detail: str = "", user_message: str | None = None):
        super().__init__(detail)
        if user_message:
            self.user_message = user_message


class LLMConfigError(LLMError):
    user_message = "The AI service is not configured correctly (missing or invalid API key). Contact the administrator."
    http_status = 500


class LLMUnavailable(LLMError):
    user_message = "The AI service is temporarily unavailable or rate-limited. Please try again in a minute."
    http_status = 503


class LLMBadOutput(LLMError):
    user_message = "The AI returned an invalid report twice. Please try again; if it persists, shorten the inputs."
    http_status = 502


Message = dict[str, str]


class Provider(Protocol):
    name: str
    model: str

    def complete(self, system: str, messages: list[Message]) -> str: ...


def _post_json(client: httpx.Client, url: str, headers: dict, body: dict, max_retries: int) -> dict:
    last_exc: Exception | None = None
    for attempt in range(max_retries + 1):
        try:
            resp = client.post(url, headers=headers, json=body)
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            last_exc = exc
            log.warning("llm transport error attempt=%d type=%s", attempt + 1, type(exc).__name__)
        else:
            if resp.status_code < 400:
                return resp.json()
            if resp.status_code in (401, 403):
                raise LLMConfigError(f"auth failed status={resp.status_code}")
            if resp.status_code not in RETRYABLE_STATUS:
                raise LLMError(f"non-retryable status={resp.status_code}")
            last_exc = LLMUnavailable(f"status={resp.status_code}")
            log.warning("llm retryable status=%d attempt=%d", resp.status_code, attempt + 1)
            retry_after = resp.headers.get("retry-after")
            if retry_after and retry_after.isdigit() and attempt < max_retries:
                time.sleep(min(int(retry_after), 20))
                continue
        if attempt < max_retries:
            time.sleep(min(2**attempt + random.uniform(0, 0.5), 10))
    raise LLMUnavailable(f"gave up after {max_retries + 1} attempts: {type(last_exc).__name__}")


class GeminiProvider:
    name = "gemini"

    def __init__(self, settings: Settings, client: httpx.Client | None = None):
        if not settings.gemini_api_key:
            raise LLMConfigError("GEMINI_API_KEY not set")
        self._key = settings.gemini_api_key
        self.model = settings.gemini_model
        self._retries = settings.llm_max_retries
        self._client = client or httpx.Client(timeout=settings.llm_timeout_seconds)

    def complete(self, system: str, messages: list[Message]) -> str:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{self.model}:generateContent"
        body = {
            "systemInstruction": {"parts": [{"text": system}]},
            "contents": [
                {"role": "model" if m["role"] == "assistant" else "user", "parts": [{"text": m["content"]}]}
                for m in messages
            ],
            "generationConfig": {
                "temperature": 0,
                "responseMimeType": "application/json",
                "responseSchema": RESPONSE_SCHEMA,
            },
        }
        data = _post_json(self._client, url, {"x-goog-api-key": self._key}, body, self._retries)
        try:
            candidate = data["candidates"][0]
            return "".join(p.get("text", "") for p in candidate["content"]["parts"])
        except (KeyError, IndexError, TypeError):
            reason = (data.get("promptFeedback") or {}).get("blockReason") or "no candidates"
            raise LLMBadOutput(f"empty gemini response: {reason}") from None


class OpenAICompatProvider:
    """Provider for any OpenAI-compatible chat completions API."""

    name = "openai_compat"

    def __init__(self, settings: Settings, client: httpx.Client | None = None):
        if not settings.openai_compat_api_key and "localhost" not in settings.openai_compat_base_url:
            raise LLMConfigError("OPENAI_COMPAT_API_KEY not set")
        self._key = settings.openai_compat_api_key
        self._base = settings.openai_compat_base_url.rstrip("/")
        self.model = settings.openai_compat_model
        self._retries = settings.llm_max_retries
        self._client = client or httpx.Client(timeout=settings.llm_timeout_seconds)

    def complete(self, system: str, messages: list[Message]) -> str:
        body = {
            "model": self.model,
            "temperature": 0,
            "response_format": {"type": "json_object"},
            "messages": [{"role": "system", "content": system}, *messages],
        }
        headers = {"Authorization": f"Bearer {self._key}"} if self._key else {}
        data = _post_json(self._client, f"{self._base}/chat/completions", headers, body, self._retries)
        try:
            return data["choices"][0]["message"]["content"] or ""
        except (KeyError, IndexError, TypeError):
            raise LLMBadOutput("unexpected chat completion shape") from None


SKILL_VOCAB = [
    "python",
    "java",
    "javascript",
    "typescript",
    "react",
    "node",
    "sql",
    "postgresql",
    "mysql",
    "mongodb",
    "aws",
    "azure",
    "gcp",
    "docker",
    "kubernetes",
    "terraform",
    "fastapi",
    "django",
    "flask",
    "spring",
    "git",
    "ci/cd",
    "rest",
    "graphql",
    "machine learning",
    "llm",
    "nlp",
    "pytorch",
    "tensorflow",
    "pandas",
    "kotlin",
    "swift",
    "android",
    "ios",
    "go",
    "rust",
    "c++",
    "linux",
    "redis",
    "kafka",
    "agile",
    "communication",
    "leadership",
    "testing",
    "microservices",
    "html",
    "css",
    "figma",
    "excel",
]


class MockProvider:
    """Deterministic offline provider for tests and keyless demos."""

    name = "mock"
    model = "mock-keyword-matcher"

    def complete(self, system: str, messages: list[Message]) -> str:
        prompt = messages[0]["content"]
        jd = _between(prompt, "job_description").lower()
        resume = _between(prompt, "resume").lower()

        def has(text: str, skill: str) -> bool:
            return re.search(rf"(?<![\w]){re.escape(skill)}(?![\w])", text) is not None

        required = [s for s in SKILL_VOCAB if has(jd, s)] or ["communication", "problem solving", "teamwork"]
        matched = [s for s in required if has(resume, s)]
        missing = [s for s in required if s not in matched]
        score = round(100 * len(matched) / len(required)) if required else 0
        report = {
            "score": score,
            "score_rationale": f"Keyword overlap: {len(matched)} of {len(required)} job skills found in the resume.",
            "matching_skills": [{"skill": s.title(), "evidence": f"Mentions '{s}'"} for s in matched[:5]]
            or [{"skill": "General experience", "evidence": "No direct keyword matches found"}],
            "gaps": [s.title() for s in missing[:3]] or ["Depth of experience", "Leadership", "Domain knowledge"],
            "interview_questions": [
                f"Tell us about a project where you used {(matched or ['your core skills'])[0]}.",
                f"How would you get up to speed with {(missing or ['our stack'])[0]}?",
                "Describe a difficult technical decision you made and its outcome.",
            ],
            "summary": "Mock analysis (keyword matching, no LLM). Configure LLM_PROVIDER for real analysis.",
            "injection_attempt_detected": False,
        }
        return json.dumps(report)


def _between(text: str, tag: str) -> str:
    m = re.search(rf"<{tag}>(.*?)</{tag}>", text, re.S)
    return m.group(1) if m else ""


def get_provider(settings: Settings) -> Provider:
    if settings.llm_provider == "gemini":
        return GeminiProvider(settings)
    if settings.llm_provider == "openai_compat":
        return OpenAICompatProvider(settings)
    if settings.llm_provider == "mock":
        return MockProvider()
    raise LLMConfigError(f"unknown LLM_PROVIDER={settings.llm_provider!r}")


def parse_report(raw: str) -> MatchReport:
    """Extract and validate a JSON report from raw model output."""
    text = raw.strip()
    fenced = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    if fenced:
        text = fenced.group(1).strip()
    if not text.startswith("{"):
        start, end = text.find("{"), text.rfind("}")
        if start == -1 or end <= start:
            raise ValueError("no JSON object found in model output")
        text = text[start : end + 1]
    data = json.loads(text)
    if not isinstance(data, dict):
        raise ValueError("model output is not a JSON object")
    return MatchReport.model_validate(data)


@dataclass
class AnalysisResult:
    report: MatchReport
    provider: str
    model: str
    latency_ms: int
    repaired: bool


def analyze(provider: Provider, job_description: str, resume: str) -> AnalysisResult:
    started = time.perf_counter()
    messages: list[Message] = [{"role": "user", "content": build_user_prompt(job_description, resume)}]
    repaired = False

    for attempt in range(MAX_REPAIR_ATTEMPTS + 1):
        raw = provider.complete(SYSTEM_PROMPT, messages)
        try:
            report = parse_report(raw)
            break
        except (ValueError, ValidationError) as exc:
            error = _short_error(exc)
            log.warning("llm output invalid attempt=%d error_type=%s", attempt + 1, type(exc).__name__)
            if attempt == MAX_REPAIR_ATTEMPTS:
                raise LLMBadOutput(f"validation failed after repair: {error}") from exc
            messages += [
                {"role": "assistant", "content": raw},
                {"role": "user", "content": build_repair_prompt(raw, error)},
            ]
            repaired = True

    latency_ms = int((time.perf_counter() - started) * 1000)
    log.info(
        "llm analysis ok provider=%s model=%s latency_ms=%d repaired=%s",
        provider.name,
        provider.model,
        latency_ms,
        repaired,
    )
    return AnalysisResult(
        report=report, provider=provider.name, model=provider.model, latency_ms=latency_ms, repaired=repaired
    )


def _short_error(exc: Exception) -> str:
    if isinstance(exc, ValidationError):
        return "; ".join(f"{'.'.join(map(str, e['loc']))}: {e['msg']}" for e in exc.errors()[:10])
    return str(exc)[:300]

import json
from dataclasses import replace

import httpx
import pytest

from app import llm
from app.config import Settings
from app.llm import GeminiProvider, LLMBadOutput, LLMConfigError, LLMUnavailable, analyze, parse_report
from tests.conftest import ScriptedProvider, valid_report


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    monkeypatch.setattr(llm.time, "sleep", lambda s: None)


def test_parse_report_tolerates_fences_and_prose():
    raw = "Here you go:\n```json\n" + json.dumps(valid_report()) + "\n```\nHope this helps!"
    assert parse_report(raw).score == 82


def test_parse_report_rejects_non_json():
    with pytest.raises(ValueError):
        parse_report("I cannot help with that.")


def test_analyze_repairs_invalid_output_once():
    provider = ScriptedProvider("{not json", valid_report())
    result = analyze(provider, "jd", "resume")
    assert result.repaired and result.report.score == 82
    repair_msg = provider.calls[1][-1]["content"]
    assert "failed validation" in repair_msg


def test_analyze_feeds_validation_error_back():
    provider = ScriptedProvider(valid_report(score=150), valid_report())
    analyze(provider, "jd", "resume")
    assert "score" in provider.calls[1][-1]["content"]


def test_analyze_gives_up_after_repair():
    provider = ScriptedProvider("nope", valid_report(gaps=[]))
    with pytest.raises(LLMBadOutput):
        analyze(provider, "jd", "resume")


def _gemini(handler, retries=3):
    settings = replace(Settings(), llm_provider="gemini", gemini_api_key="secret-key", llm_max_retries=retries)
    return GeminiProvider(settings, client=httpx.Client(transport=httpx.MockTransport(handler)))


def _gemini_ok(text: str) -> httpx.Response:
    return httpx.Response(200, json={"candidates": [{"content": {"parts": [{"text": text}]}}]})


def test_gemini_retries_transient_errors_then_succeeds():
    calls = []

    def handler(request: httpx.Request):
        calls.append(request)
        if len(calls) < 3:
            return httpx.Response(503)
        return _gemini_ok(json.dumps(valid_report()))

    result = analyze(_gemini(handler), "jd", "resume")
    assert result.report.score == 82 and len(calls) == 3


def test_gemini_key_sent_in_header_not_url():
    seen = {}

    def handler(request: httpx.Request):
        seen["url"], seen["key"] = str(request.url), request.headers.get("x-goog-api-key")
        return _gemini_ok(json.dumps(valid_report()))

    analyze(_gemini(handler), "jd", "resume")
    assert "secret-key" not in seen["url"] and seen["key"] == "secret-key"


def test_gemini_gives_up_after_max_retries():
    calls = []

    def handler(request):
        calls.append(1)
        return httpx.Response(429)

    with pytest.raises(LLMUnavailable):
        analyze(_gemini(handler, retries=2), "jd", "resume")
    assert len(calls) == 3


def test_gemini_timeout_is_retried():
    calls = []

    def handler(request):
        calls.append(1)
        if len(calls) == 1:
            raise httpx.ReadTimeout("slow", request=request)
        return _gemini_ok(json.dumps(valid_report()))

    assert analyze(_gemini(handler), "jd", "resume").report.score == 82


def test_gemini_auth_error_not_retried():
    calls = []

    def handler(request):
        calls.append(1)
        return httpx.Response(403)

    with pytest.raises(LLMConfigError):
        analyze(_gemini(handler), "jd", "resume")
    assert len(calls) == 1


def test_missing_key_raises_config_error():
    with pytest.raises(LLMConfigError):
        GeminiProvider(replace(Settings(), gemini_api_key=""))


def test_config_repr_hides_secrets():
    s = replace(Settings(), gemini_api_key="super-secret", openai_compat_api_key="other-secret")
    assert "secret" not in repr(s)

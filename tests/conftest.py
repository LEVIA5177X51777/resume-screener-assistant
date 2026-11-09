import json
from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.db import Database
from app.main import create_app

JD = "Senior Backend Engineer. Must have Python, FastAPI, PostgreSQL, Docker and AWS experience. " * 2
RESUME = "Jane Doe, jane@example.com. Six years of Python and FastAPI. Built PostgreSQL schemas, Docker CI/CD. " * 2


def valid_report(**overrides) -> dict:
    report = {
        "score": 82,
        "score_rationale": "Meets most must-haves.",
        "matching_skills": [{"skill": s, "evidence": f"Used {s}"} for s in ["Python", "FastAPI", "PostgreSQL", "Docker", "CI/CD"]],
        "gaps": ["AWS depth", "Kubernetes", "Kafka"],
        "interview_questions": ["Q1?", "Q2?", "Q3?"],
        "summary": "Strong backend candidate.",
        "injection_attempt_detected": False,
    }
    report.update(overrides)
    return report


class ScriptedProvider:
    """Returns queued outputs in order; an Exception in the queue is raised instead."""

    name = "scripted"
    model = "scripted-1"

    def __init__(self, *outputs):
        self.outputs = list(outputs)
        self.calls: list[list[dict]] = []

    def complete(self, system, messages):
        self.calls.append(messages)
        out = self.outputs.pop(0)
        if isinstance(out, Exception):
            raise out
        return out if isinstance(out, str) else json.dumps(out)


@pytest.fixture
def settings():
    return replace(Settings(), llm_provider="mock", database_path=":memory:", redact_pii=True, store_raw_text=False)


@pytest.fixture
def make_client(settings):
    def _make(provider):
        app = create_app(settings, Database(":memory:"), provider)
        return TestClient(app)

    return _make

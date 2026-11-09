from dataclasses import replace
from pathlib import Path

from fastapi.testclient import TestClient

from app.db import Database
from app.llm import LLMUnavailable
from app.main import create_app
from tests.conftest import JD, RESUME, ScriptedProvider, valid_report

SAMPLES = Path(__file__).parent.parent / "sample_data"


def _payload(name="Jane Doe", jd=JD, resume=RESUME):
    return {"candidate_name": name, "job_description": jd, "resume": resume}


def test_create_and_fetch_analysis(make_client):
    with make_client(ScriptedProvider(valid_report())) as c:
        res = c.post("/api/analyses", json=_payload())
        assert res.status_code == 201
        body = res.json()
        assert body["score"] == 82 and len(body["report"]["matching_skills"]) == 5
        assert c.get(f"/api/analyses/{body['id']}").json()["candidate_name"] == "Jane Doe"
        assert c.get(f"/analyses/{body['id']}").status_code == 200


def test_pii_redacted_before_llm(make_client):
    provider = ScriptedProvider(valid_report())
    with make_client(provider) as c:
        c.post("/api/analyses", json=_payload())
    prompt = provider.calls[0][0]["content"]
    assert "jane@example.com" not in prompt and "Jane" not in prompt and "[CANDIDATE]" in prompt


def test_empty_and_short_inputs_rejected(make_client):
    provider = ScriptedProvider()
    with make_client(provider) as c:
        assert c.post("/api/analyses", json=_payload(name="  ")).status_code == 422
        assert c.post("/api/analyses", json=_payload(resume="")).status_code == 422
        res = c.post("/api/analyses", json=_payload(jd="too short"))
        assert res.status_code == 422 and "too short" in res.json()["error"]
    assert provider.calls == []


def test_long_input_truncated(make_client):
    provider = ScriptedProvider(valid_report())
    with make_client(provider) as c:
        res = c.post("/api/analyses", json=_payload(resume=RESUME * 200))
    assert res.json()["meta"]["resume_truncated"] is True
    assert len(provider.calls[0][0]["content"]) < len(RESUME * 200)


def test_llm_outage_returns_friendly_503_and_saves_nothing(make_client):
    with make_client(ScriptedProvider(LLMUnavailable("down"))) as c:
        res = c.post("/api/analyses", json=_payload())
        assert res.status_code == 503 and "temporarily unavailable" in res.json()["error"]
        assert c.get("/api/analyses").json()["total"] == 0


def test_malformed_output_twice_returns_502(make_client):
    with make_client(ScriptedProvider("garbage", "still garbage")) as c:
        res = c.post("/api/analyses", json=_payload())
    assert res.status_code == 502 and "invalid report" in res.json()["error"]


def test_injection_is_flagged(make_client):
    resume = (SAMPLES / "resume_injection.txt").read_text()
    provider = ScriptedProvider(valid_report(score=15, injection_attempt_detected=True))
    with make_client(provider) as c:
        body = c.post("/api/analyses", json=_payload(name="Alex Mercer", resume=resume)).json()
    assert body["meta"]["injection_suspected"] is True
    assert "model_flagged" in body["meta"]["injection_signals"]
    assert "</resume>" not in provider.calls[0][0]["content"].split("<resume>", 1)[1].rsplit("</resume>", 1)[0]


def test_history_filters(make_client):
    provider = ScriptedProvider(valid_report(score=90), valid_report(score=40), valid_report(score=65))
    with make_client(provider) as c:
        for name in ["Priya Sharma", "Rahul Verma", "Priyanka Rao"]:
            c.post("/api/analyses", json=_payload(name=name))
        assert c.get("/api/analyses", params={"name": "priya"}).json()["total"] == 2
        assert c.get("/api/analyses", params={"min_score": 60}).json()["total"] == 2
        assert c.get("/api/analyses", params={"min_score": 50, "max_score": 70}).json()["items"][0]["score"] == 65
        scores = [i["score"] for i in c.get("/api/analyses", params={"sort": "score_desc"}).json()["items"]]
        assert scores == [90, 65, 40]
        assert c.get("/api/analyses", params={"name": "%"}).json()["total"] == 0
        assert c.get("/api/analyses", params={"min_score": 80, "max_score": 20}).status_code == 422
        assert c.get("/api/analyses", params={"min_score": 101}).status_code == 422
        assert c.get("/history", params={"name": "priya", "min_score": "abc"}).status_code == 200


def test_basic_auth_when_configured(settings):
    secured = replace(settings, basic_auth_user="hr", basic_auth_password="s3cret")
    with TestClient(create_app(secured, Database(":memory:"), ScriptedProvider())) as c:
        assert c.get("/history").status_code == 401
        assert c.get("/history", auth=("hr", "wrong")).status_code == 401
        assert c.get("/history", auth=("hr", "s3cret")).status_code == 200
        assert c.get("/api/health").status_code == 200


def test_delete(make_client):
    with make_client(ScriptedProvider(valid_report())) as c:
        rid = c.post("/api/analyses", json=_payload()).json()["id"]
        assert c.delete(f"/api/analyses/{rid}").status_code == 204
        missing = c.get(f"/api/analyses/{rid}")
        assert missing.status_code == 404 and missing.json()["error"] == "Analysis not found"
        assert c.delete(f"/api/analyses/{rid}").status_code == 404

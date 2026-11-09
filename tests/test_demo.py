from dataclasses import replace

from fastapi.testclient import TestClient

from app.db import Database
from app.demo import seed_if_empty
from app.main import create_app
from tests.conftest import JD, RESUME, ScriptedProvider, valid_report


def test_seed_fills_empty_database():
    db = Database(":memory:")
    assert seed_if_empty(db) == 4
    names = {item.candidate_name for item in db.list(limit=10)[0]}
    assert names == {"Priya Sharma", "Sneha Iyer", "Alex Mercer", "Rahul Verma"}
    assert db.stats().injection_flagged == 1


def test_seed_skips_non_empty_database():
    db = Database(":memory:")
    seed_if_empty(db)
    assert seed_if_empty(db) == 0
    assert db.count() == 4


def test_seed_only_when_enabled(settings):
    with TestClient(create_app(settings, Database(":memory:"), ScriptedProvider())) as c:
        assert c.get("/api/stats").json()["total"] == 0
        assert "Demo instance" not in c.get("/history").text

    seeded = replace(settings, seed_demo_data=True)
    provider = ScriptedProvider(valid_report())
    with TestClient(create_app(seeded, Database(":memory:"), provider)) as c:
        assert c.get("/api/stats").json()["total"] == 4
        assert "Demo instance" in c.get("/history").text
        payload = {"candidate_name": "New Person", "job_description": JD, "resume": RESUME}
        assert c.post("/api/analyses", json=payload).status_code == 201
        assert c.get("/api/stats").json()["total"] == 5

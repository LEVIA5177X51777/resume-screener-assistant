"""Preloads sample analyses into an empty database for hosted demos with ephemeral storage."""

import json
from pathlib import Path

from .db import Database
from .schemas import AnalysisMeta, MatchReport

DEMO_FILE = Path(__file__).parent.parent / "sample_data" / "demo_analyses.json"


def seed_if_empty(db: Database, path: Path = DEMO_FILE) -> int:
    if db.count() > 0:
        return 0
    records = json.loads(path.read_text(encoding="utf-8"))
    for record in records:
        db.insert(
            candidate_name=record["candidate_name"],
            report=MatchReport.model_validate(record["report"]),
            jd_excerpt=record["jd_excerpt"],
            jd_hash=record["jd_hash"],
            resume_hash=record["resume_hash"],
            meta=AnalysisMeta.model_validate(record["meta"]),
        )
    return len(records)

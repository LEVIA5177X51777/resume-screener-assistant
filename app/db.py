"""SQLite persistence layer for analyses."""

import json
import os
import sqlite3
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta

from .schemas import AnalysisMeta, AnalysisRecord, AnalysisStats, AnalysisSummary, MatchReport, ScoreBucket

STRONG_MATCH_THRESHOLD = 70
SCORE_BUCKETS = [
    ("0–24", 0, 24),
    ("25–49", 25, 49),
    ("50–69", 50, 69),
    ("70–89", 70, 89),
    ("90–100", 90, 100),
]

SCHEMA = """
CREATE TABLE IF NOT EXISTS analyses (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    candidate_name      TEXT    NOT NULL,
    score               INTEGER NOT NULL CHECK (score BETWEEN 0 AND 100),
    report_json         TEXT    NOT NULL,
    jd_excerpt          TEXT    NOT NULL,
    jd_hash             TEXT    NOT NULL,
    resume_hash         TEXT    NOT NULL,
    jd_text             TEXT,
    resume_text         TEXT,
    provider            TEXT    NOT NULL,
    model               TEXT    NOT NULL,
    latency_ms          INTEGER NOT NULL,
    jd_truncated        INTEGER NOT NULL DEFAULT 0,
    resume_truncated    INTEGER NOT NULL DEFAULT 0,
    pii_redacted        INTEGER NOT NULL DEFAULT 0,
    injection_suspected INTEGER NOT NULL DEFAULT 0,
    injection_signals   TEXT    NOT NULL DEFAULT '[]',
    created_at          TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_analyses_score      ON analyses(score);
CREATE INDEX IF NOT EXISTS idx_analyses_created_at ON analyses(created_at);
CREATE INDEX IF NOT EXISTS idx_analyses_name       ON analyses(candidate_name COLLATE NOCASE);
"""

SORTS = {
    "newest": "created_at DESC, id DESC",
    "oldest": "created_at ASC, id ASC",
    "score_desc": "score DESC, id DESC",
    "score_asc": "score ASC, id ASC",
}


class Database:
    def __init__(self, path: str):
        self.path = path
        if path != ":memory:":
            os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        self._memory_conn = sqlite3.connect(":memory:", check_same_thread=False) if path == ":memory:" else None
        with self.connect() as conn:
            conn.executescript(SCHEMA)

    @contextmanager
    def connect(self):
        conn = self._memory_conn or sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            if conn is not self._memory_conn:
                conn.close()

    def insert(
        self,
        *,
        candidate_name: str,
        report: MatchReport,
        jd_excerpt: str,
        jd_hash: str,
        resume_hash: str,
        meta: AnalysisMeta,
        jd_text: str | None = None,
        resume_text: str | None = None,
    ) -> AnalysisRecord:
        created_at = datetime.now(UTC).isoformat(timespec="seconds")
        with self.connect() as conn:
            cur = conn.execute(
                """INSERT INTO analyses (candidate_name, score, report_json, jd_excerpt, jd_hash, resume_hash,
                       jd_text, resume_text, provider, model, latency_ms, jd_truncated, resume_truncated,
                       pii_redacted, injection_suspected, injection_signals, created_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    candidate_name,
                    report.score,
                    report.model_dump_json(),
                    jd_excerpt,
                    jd_hash,
                    resume_hash,
                    jd_text,
                    resume_text,
                    meta.provider,
                    meta.model,
                    meta.latency_ms,
                    int(meta.jd_truncated),
                    int(meta.resume_truncated),
                    int(meta.pii_redacted),
                    int(meta.injection_suspected),
                    json.dumps(meta.injection_signals),
                    created_at,
                ),
            )
            new_id = cur.lastrowid
        return self.get(new_id)

    def get(self, analysis_id: int) -> AnalysisRecord | None:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM analyses WHERE id = ?", (analysis_id,)).fetchone()
        return _to_record(row) if row else None

    def list(
        self,
        *,
        name: str | None = None,
        min_score: int | None = None,
        max_score: int | None = None,
        sort: str = "newest",
        limit: int = 20,
        offset: int = 0,
    ) -> tuple[list[AnalysisSummary], int]:
        where, params = [], []
        if name:
            escaped = name.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            where.append("candidate_name LIKE ? ESCAPE '\\'")
            params.append(f"%{escaped}%")
        if min_score is not None:
            where.append("score >= ?")
            params.append(min_score)
        if max_score is not None:
            where.append("score <= ?")
            params.append(max_score)
        where_sql = f"WHERE {' AND '.join(where)}" if where else ""
        order_sql = SORTS.get(sort, SORTS["newest"])

        with self.connect() as conn:
            total = conn.execute(f"SELECT COUNT(*) FROM analyses {where_sql}", params).fetchone()[0]
            rows = conn.execute(
                f"""SELECT id, candidate_name, score, jd_excerpt, injection_suspected, created_at
                    FROM analyses {where_sql} ORDER BY {order_sql} LIMIT ? OFFSET ?""",
                [*params, limit, offset],
            ).fetchall()
        items = [
            AnalysisSummary(
                id=r["id"],
                candidate_name=r["candidate_name"],
                score=r["score"],
                jd_excerpt=r["jd_excerpt"],
                injection_suspected=bool(r["injection_suspected"]),
                created_at=r["created_at"],
            )
            for r in rows
        ]
        return items, total

    def delete(self, analysis_id: int) -> bool:
        with self.connect() as conn:
            return conn.execute("DELETE FROM analyses WHERE id = ?", (analysis_id,)).rowcount > 0

    def stats(self) -> AnalysisStats:
        bucket_sql = ", ".join(f"COALESCE(SUM(score BETWEEN {lo} AND {hi}), 0)" for _, lo, hi in SCORE_BUCKETS)
        with self.connect() as conn:
            row = conn.execute(
                f"""SELECT COUNT(*), AVG(score), COALESCE(SUM(score >= {STRONG_MATCH_THRESHOLD}), 0),
                           COALESCE(SUM(injection_suspected), 0), {bucket_sql}
                    FROM analyses"""
            ).fetchone()
        total, avg, strong, flagged, *counts = row
        return AnalysisStats(
            total=total,
            average_score=round(avg, 1) if avg is not None else None,
            strong_matches=strong,
            injection_flagged=flagged,
            distribution=[
                ScoreBucket(label=label, min_score=lo, max_score=hi, count=count)
                for (label, lo, hi), count in zip(SCORE_BUCKETS, counts, strict=True)
            ],
        )

    def purge_older_than(self, days: int) -> int:
        cutoff = (datetime.now(UTC) - timedelta(days=days)).isoformat(timespec="seconds")
        with self.connect() as conn:
            return conn.execute("DELETE FROM analyses WHERE created_at < ?", (cutoff,)).rowcount


def _to_record(row: sqlite3.Row) -> AnalysisRecord:
    return AnalysisRecord(
        id=row["id"],
        candidate_name=row["candidate_name"],
        score=row["score"],
        report=MatchReport.model_validate_json(row["report_json"]),
        jd_excerpt=row["jd_excerpt"],
        meta=AnalysisMeta(
            provider=row["provider"],
            model=row["model"],
            latency_ms=row["latency_ms"],
            jd_truncated=bool(row["jd_truncated"]),
            resume_truncated=bool(row["resume_truncated"]),
            pii_redacted=bool(row["pii_redacted"]),
            injection_suspected=bool(row["injection_suspected"]),
            injection_signals=json.loads(row["injection_signals"]),
        ),
        created_at=row["created_at"],
    )

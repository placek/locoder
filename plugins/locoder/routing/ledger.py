"""SQLite ledger: every routing decision, every attempt, every Claude Code limit hit.

The attempts table is the training set. A coder attempt that passed /verify is a positive
label for "local can do this"; one that failed is a negative. After a few weeks this is what
tells you whether the judge beats plain rules, and what a fine-tuned judge would learn from.
"""
from __future__ import annotations

import json
import sqlite3
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

SCHEMA = """
CREATE TABLE IF NOT EXISTS decisions (
    id TEXT PRIMARY KEY,
    ts REAL NOT NULL,
    brief TEXT NOT NULL,
    workdir TEXT,
    rung TEXT NOT NULL,
    reason TEXT NOT NULL,
    verdict TEXT,            -- JSON from judge.verdict_dict, NULL when the judge was down
    claude TEXT,             -- JSON: Claude Code's availability when the decision was made
    commit_sha TEXT,         -- workdir HEAD at route() time; with the brief, makes the task replayable
    dirty INTEGER,           -- 1 if the workdir had uncommitted changes then
    mode TEXT                -- the session's routing mode: auto, claude or local
);
CREATE TABLE IF NOT EXISTS attempts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    decision_id TEXT,
    ts REAL NOT NULL,
    rung TEXT NOT NULL,      -- coder | claude | openrouter
    ok INTEGER,              -- backend reported success (NULL for coder: Hermes ran it)
    verified INTEGER,        -- /verify passed afterwards; the label that matters
    limit_hit INTEGER NOT NULL DEFAULT 0,
    cost REAL NOT NULL DEFAULT 0,
    turns INTEGER,
    duration_s REAL,
    session_id TEXT,
    notes TEXT
);
CREATE TABLE IF NOT EXISTS limit_hits (
    ts REAL NOT NULL,
    window_start REAL NOT NULL,
    spent REAL NOT NULL,     -- claude cost units spent in the window when the limit hit
    until REAL NOT NULL,     -- claude is skipped until then
    reset_source TEXT,       -- "stated": read from the error; "fallback": unreadable, short lockout
    raw TEXT                 -- the error text, to fix the limit and reset parsing against
);
CREATE INDEX IF NOT EXISTS attempts_ts ON attempts(ts);
CREATE INDEX IF NOT EXISTS attempts_decision ON attempts(decision_id);
"""


class Ledger:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(self.path), timeout=10, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.executescript(SCHEMA)
        self._add_missing_columns("decisions", {"claude": "TEXT", "commit_sha": "TEXT", "dirty": "INTEGER", "mode": "TEXT"})
        self._add_missing_columns("limit_hits", {"reset_source": "TEXT", "raw": "TEXT"})

    def _add_missing_columns(self, table: str, columns: Dict[str, str]) -> None:
        """Bring a ledger written by an older version up to the current schema, keeping its rows."""
        have = {r["name"] for r in self.db.execute(f"PRAGMA table_info({table})")}
        with self.db:
            for name, sql_type in columns.items():
                if name not in have:
                    self.db.execute(f"ALTER TABLE {table} ADD COLUMN {name} {sql_type}")

    # -- writes -------------------------------------------------------------
    def add_decision(self, brief: str, workdir: Optional[str], rung: str, reason: str,
                     verdict: Optional[dict], claude: Optional[dict],
                     commit_sha: Optional[str] = None, dirty: Optional[bool] = None,
                     mode: str = "auto") -> str:
        decision_id = uuid.uuid4().hex[:12]
        with self.db:
            self.db.execute(
                "INSERT INTO decisions (id, ts, brief, workdir, rung, reason, verdict, claude, commit_sha, dirty, mode)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (decision_id, time.time(), brief, workdir, rung, reason,
                 json.dumps(verdict) if verdict else None, json.dumps(claude) if claude else None,
                 commit_sha, _tri(dirty), mode),
            )
        return decision_id

    def add_attempt(self, rung: str, decision_id: Optional[str] = None, ok: Optional[bool] = None,
                    verified: Optional[bool] = None, limit_hit: bool = False, cost: float = 0.0,
                    turns: Optional[int] = None, duration_s: Optional[float] = None,
                    session_id: Optional[str] = None, notes: Optional[str] = None) -> int:
        with self.db:
            cur = self.db.execute(
                "INSERT INTO attempts (decision_id, ts, rung, ok, verified, limit_hit, cost, turns,"
                " duration_s, session_id, notes) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (decision_id, time.time(), rung, _tri(ok), _tri(verified), int(limit_hit),
                 float(cost or 0), turns, duration_s, session_id, notes),
            )
        return int(cur.lastrowid)

    def set_verified(self, decision_id: str, rung: str, verified: bool, notes: Optional[str]) -> bool:
        """Label the latest attempt of *rung* for *decision_id*; insert one if Hermes ran it (coder)."""
        row = self.db.execute(
            "SELECT id FROM attempts WHERE decision_id=? AND rung=? ORDER BY id DESC LIMIT 1",
            (decision_id, rung),
        ).fetchone()
        with self.db:
            if row is None:
                self.db.execute(
                    "INSERT INTO attempts (decision_id, ts, rung, verified, notes) VALUES (?,?,?,?,?)",
                    (decision_id, time.time(), rung, int(verified), notes),
                )
            else:
                self.db.execute(
                    "UPDATE attempts SET verified=?, notes=COALESCE(?, notes) WHERE id=?",
                    (int(verified), notes, row["id"]),
                )
        return True

    def add_limit_hit(self, until: float, raw: str = "", reset_source: str = "stated",
                      window_start: float = 0.0, spent: float = 0.0) -> None:
        with self.db:
            self.db.execute(
                "INSERT INTO limit_hits (ts, window_start, spent, until, reset_source, raw) VALUES (?,?,?,?,?,?)",
                (time.time(), window_start, spent, until, reset_source, raw),
            )

    # -- reads --------------------------------------------------------------
    def decision(self, decision_id: str) -> Optional[sqlite3.Row]:
        return self.db.execute("SELECT * FROM decisions WHERE id=?", (decision_id,)).fetchone()

    def failed_rungs(self, decision_id: str) -> set:
        """Rungs whose attempt at this decision failed its acceptance check."""
        rows = self.db.execute("SELECT DISTINCT rung FROM attempts WHERE decision_id=? AND verified=0",
                               (decision_id,)).fetchall()
        return {r[0] for r in rows}

    def claude_spent(self, since: float) -> float:
        row = self.db.execute(
            "SELECT COALESCE(SUM(cost),0) FROM attempts WHERE rung='claude' AND ts>=?", (since,)
        ).fetchone()
        return float(row[0])

    def exhausted_until(self, now: float) -> Optional[float]:
        row = self.db.execute("SELECT MAX(until) FROM limit_hits WHERE until>?", (now,)).fetchone()
        return float(row[0]) if row and row[0] else None

    def stats(self, since: float) -> Dict[str, Any]:
        rows = self.db.execute(
            "SELECT rung, COUNT(*) n, SUM(verified=1) passed, SUM(verified=0) failed, SUM(cost) cost "
            "FROM attempts WHERE ts>=? GROUP BY rung", (since,)
        ).fetchall()
        return {r["rung"]: {"attempts": r["n"], "verified": r["passed"] or 0,
                            "failed": r["failed"] or 0, "cost": round(r["cost"] or 0, 2)} for r in rows}

    def close(self) -> None:
        self.db.close()


def _tri(value: Optional[bool]) -> Optional[int]:
    return None if value is None else int(bool(value))


__all__: List[str] = ["Ledger"]

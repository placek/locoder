import importlib.util
import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from routing import settings
from routing.ledger import Ledger

TZ = "Europe/Warsaw"
spec = importlib.util.spec_from_file_location("report", Path(__file__).resolve().parents[1] / "scripts" / "report.py")
report = importlib.util.module_from_spec(spec)
spec.loader.exec_module(report)


def at(d, h, m=0):
    return datetime(2026, 9, d, h, m, tzinfo=ZoneInfo(TZ)).timestamp()


def seeded(tmp_path):
    led = Ledger(tmp_path / "ledger.db")
    db = led.db

    def decision(id_, p_local, mode="auto"):
        db.execute("INSERT INTO decisions (id, ts, brief, rung, reason, verdict, mode) VALUES (?,?,?,?,?,?,?)",
                   (id_, 0, "b", "coder", "r", json.dumps({"p_local": p_local}), mode))

    def attempt(rung, ts, verified, decision_id=None, cost=0.0):
        db.execute("INSERT INTO attempts (decision_id, ts, rung, verified, cost) VALUES (?,?,?,?,?)",
                   (decision_id, ts, rung, verified, cost))

    decision("sure", 0.9)
    decision("fall", 0.4)
    decision("forced", 0.1, mode="local")
    attempt("claude", at(21, 10), 1, cost=2.5)
    attempt("coder", at(21, 12), 1, "sure")
    attempt("coder", at(22, 12), 0, "fall")          # inside the lockout below
    attempt("openrouter", at(22, 13), 1, cost=0.4)
    attempt("coder", at(24, 10), 1, "forced")
    db.execute("INSERT INTO limit_hits (ts, window_start, spent, until, reset_source) VALUES (?,?,?,?,?)",
               (at(22, 9), 0, 0, at(23, 21), "stated"))
    db.commit()
    return db


def cfg():
    c = settings._merge(settings.DEFAULTS, {})
    c["claude"]["week"] = {"reset_weekday": 0, "reset_hour": 9, "timezone": TZ}
    return c


def test_report_answers_the_goals(tmp_path):
    out = report.render(seeded(tmp_path), cfg(), at(24, 12))
    this_week = next(line for line in out.splitlines() if "week of Mon 21 Sep" in line)
    assert "without Claude Code  1.5 days" in this_week
    assert "verified  80%" in this_week and "openrouter    0.40" in this_week
    assert "sure thing             1/1" in out
    assert "fallback               0/1" in out
    assert "forced (local mode)    1/1" in out
    assert "P(local) 0.00-0.30:   1/1" in out
    assert "P(local) 0.30-0.85:   0/1" in out
    assert "P(local) 0.85-1.00:   1/1" in out
    assert "locked out until Wed 23 Sep 21:00  (stated)" in out
    assert "pace" not in out and "budget" not in out


def test_overlapping_limit_hits_are_not_double_counted(tmp_path):
    db = Ledger(tmp_path / "l.db").db
    for ts, until in ((0, 100), (50, 150), (300, 400)):
        db.execute("INSERT INTO limit_hits (ts, window_start, spent, until) VALUES (?,?,?,?)", (ts, 0, 0, until))
    lockouts = report._lockouts(db)
    assert lockouts == [(0, 150), (300, 400)]
    assert report.locked_out_s(lockouts, 0, 1000) == 250
    assert report.locked_out_s(lockouts, 100, 350) == 100

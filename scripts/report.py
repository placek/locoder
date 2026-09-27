#!/usr/bin/env python3
"""`make report`: what the routing ledger says about the goals it is judged on.

Claude Code should last until its weekly reset (days without it is the signal that too little
is offloaded) without the verified pass rate dropping. Below that: how the coder does when it
is chosen as a sure thing versus as a fallback, what OpenRouter cost, and whether the judge's
P(local) separates tasks the coder finishes from ones it does not, and whether the shadow
judge would have separated them better.
"""
from __future__ import annotations

import json
import math
import sqlite3
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import List, Tuple
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "plugins" / "locoder"))
from routing import policy, settings  # noqa: E402
from routing.ledger import Ledger  # noqa: E402

WEEKS = 4
DAY = 86400.0


def _weeks(now: float, cfg: dict) -> List[Tuple[float, float]]:
    start, end = policy.week_bounds(now, cfg)
    out = [(start, end)]
    for _ in range(WEEKS - 1):
        start, end = policy.week_bounds(out[-1][0] - 1, cfg)
        out.append((start, end))
    return out


def _lockouts(db: sqlite3.Connection) -> List[Tuple[float, float]]:
    """Limit-hit intervals merged, so overlapping hits are not counted twice."""
    merged: List[List[float]] = []
    for ts, until in db.execute("SELECT ts, until FROM limit_hits WHERE until > ts ORDER BY ts"):
        if merged and ts <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], until)
        else:
            merged.append([ts, until])
    return [(a, b) for a, b in merged]


def locked_out_s(lockouts: List[Tuple[float, float]], start: float, end: float) -> float:
    return sum(max(0.0, min(b, end) - max(a, start)) for a, b in lockouts)


def _locked_at(lockouts: List[Tuple[float, float]], ts: float) -> bool:
    return any(a <= ts < b for a, b in lockouts)


def _rate(passed: int, failed: int) -> str:
    return f"{100 * passed / (passed + failed):.0f}%" if passed + failed else "—"


def _day(ts: float, cfg: dict) -> str:
    return policy.when(ts, cfg)


def judge_scores(pairs: List[Tuple[dict, bool]], near: float, max_difficulty: float) -> dict:
    """How well verdicts predicted the labels: Brier score and log loss on P(local) (lower is
    better), and the tasks the verdicts would have sent to the coder as near-certain."""
    n = len(pairs)
    clip = lambda p: min(max(p, 0.01), 0.99)  # noqa: E731 - one wrong certainty must not dominate
    brier = sum((v["p_local"] - y) ** 2 for v, y in pairs) / n
    loss = -sum(math.log(clip(v["p_local"])) if y else math.log(1 - clip(v["p_local"])) for v, y in pairs) / n
    picks = [y for v, y in pairs
             if v["p_local"] >= near and v.get("expected_difficulty", math.inf) <= max_difficulty]
    return {"n": n, "brier": brier, "log_loss": loss, "picks": len(picks), "picks_passed": sum(picks)}


def _shadow_section(db: sqlite3.Connection, pol: dict, say) -> None:
    rows = db.execute("SELECT id, verdict, shadow FROM decisions WHERE shadow IS NOT NULL").fetchall()
    if not rows:
        return
    shadows = {r["id"]: json.loads(r["shadow"]) for r in rows}
    backend = next(iter(shadows.values())).get("backend") or "shadow"
    answered = [s for s in shadows.values() if s.get("verdict")]
    errors: dict = {}
    for s in shadows.values():
        if s.get("error"):
            errors[s["error"].split(":")[0]] = errors.get(s["error"].split(":")[0], 0) + 1
    ms = sorted(s["ms"] for s in answered if "ms" in s)
    say(f"\nshadow judge ({backend}): answered {len(answered)} of {len(shadows)} decisions"
        + (f", median {ms[len(ms) // 2]} ms" if ms else "")
        + (f"; errors: {', '.join(f'{k} ×{v}' for k, v in sorted(errors.items(), key=lambda kv: -kv[1]))}"
           if errors else ""))

    labelled = db.execute("""
        SELECT d.id, d.verdict, a.verified FROM decisions d
        JOIN attempts a ON a.decision_id = d.id AND a.rung = 'coder'
        WHERE d.shadow IS NOT NULL AND a.verified IS NOT NULL""").fetchall()
    both = [(json.loads(r["verdict"]), shadows[r["id"]]["verdict"], bool(r["verified"])) for r in labelled
            if r["verdict"] and shadows[r["id"]].get("verdict")]
    say(f"  scored against {len(both)} labelled coder attempts that both judges answered")
    if not both:
        return
    near, most = float(pol["local_threshold"]), float(pol["local_max_difficulty"])
    rate = sum(y for _, _, y in both) / len(both)
    base = {"p_local": rate, "expected_difficulty": 0.0}
    for name, pairs in (("judge", [(j, y) for j, _, y in both]), (backend, [(s, y) for _, s, y in both]),
                        ("base rate", [(base, y) for _, _, y in both])):
        s = judge_scores(pairs, near, most)
        picks = (f"near-certain picks {s['picks']:>3}, passed {_rate(s['picks_passed'], s['picks'] - s['picks_passed'])}"
                 if name != "base rate" else f"(always P(local)={rate:.2f})")
        say(f"  {name:<10} Brier {s['brier']:.3f}   log loss {s['log_loss']:.3f}   {picks}")
    say("  Lower is better, and a judge must beat the base rate to be worth asking. Labels come only from"
        " tasks the judge sent to the coder, so both are scored on its picks; switch backends only on a"
        " clear margin over a few dozen labels.")


def render(db: sqlite3.Connection, cfg: dict, now: float) -> str:
    db.row_factory = sqlite3.Row
    lines: List[str] = []
    say = lines.append
    lockouts = _lockouts(db)
    pol = cfg["policy"]

    say("weeks (the goal: no days without Claude Code, pass rate holding)")
    for start, end in _weeks(now, cfg):
        upto = min(end, now)
        r = db.execute("SELECT SUM(verified=1) v, SUM(verified=0) f FROM attempts WHERE ts>=? AND ts<?",
                       (start, end)).fetchone()
        spend = db.execute("SELECT COALESCE(SUM(cost),0) FROM attempts WHERE rung='openrouter' AND ts>=? AND ts<?",
                           (start, end)).fetchone()[0]
        days = locked_out_s(lockouts, start, upto) / DAY
        say(f"  week of {_day(start, cfg)}   without Claude Code {days:4.1f} days   "
            f"verified {_rate(r['v'] or 0, r['f'] or 0):>4}   openrouter {spend:7.2f}")

    say("\nopenrouter spend by month")
    months = db.execute("SELECT ts, cost FROM attempts WHERE rung='openrouter' AND cost>0").fetchall()
    zone = ZoneInfo(cfg["claude"]["week"]["timezone"])
    by_month: dict = {}
    for m in months:
        key = datetime.fromtimestamp(m["ts"], zone).strftime("%Y-%m")
        by_month[key] = by_month.get(key, 0.0) + m["cost"]
    for key in sorted(by_month)[-3:]:
        say(f"  {key}  {by_month[key]:8.2f}")
    if not by_month:
        say("  none")

    say("\nrungs (all time)")
    for r in db.execute("SELECT rung, COUNT(*) n, SUM(verified=1) v, SUM(verified=0) f, SUM(verified IS NULL) u, "
                        "ROUND(SUM(cost),2) cost, AVG(duration_s) dur FROM attempts GROUP BY rung ORDER BY rung"):
        say(f"  {r['rung']:<11} attempts {r['n']:>4}   verified {r['v'] or 0:>4}   failed {r['f'] or 0:>4}"
            f"   pass {_rate(r['v'] or 0, r['f'] or 0):>4}   unlabelled {r['u'] or 0:>4}"
            f"   cost {r['cost'] or 0:>8}   avg {r['dur'] or 0:>5.0f}s")

    say("\ncoder attempts by why it ran")
    groups = {"sure thing": [0, 0], "fallback": [0, 0], "forced (local mode)": [0, 0]}
    # Fallback: the decision was made with Claude Code locked out, or it was locked out when the
    # attempt was recorded (a limit hit mid-task). A coder attempt's ts is when it was labelled.
    rows = db.execute("""
        SELECT a.ts, a.verified, d.mode, d.claude FROM attempts a LEFT JOIN decisions d ON d.id = a.decision_id
        WHERE a.rung='coder' AND a.verified IS NOT NULL""").fetchall()
    for r in rows:
        snapshot = json.loads(r["claude"]) if r["claude"] else {}
        if (r["mode"] or "auto") == "local":
            key = "forced (local mode)"
        elif snapshot.get("available") is False or _locked_at(lockouts, r["ts"]):
            key = "fallback"
        else:
            key = "sure thing"
        groups[key][0 if r["verified"] else 1] += 1
    for key, (v, f) in groups.items():
        say(f"  {key:<20} {v:>3}/{v + f:<3} passed ({_rate(v, f)})")
    say("  A weak fallback rate is the case for a heavier local model behind Claude Code.")

    near, fallback = float(pol["local_threshold"]), float(pol["fallback_threshold"])
    rows = db.execute("""
        SELECT d.verdict, a.verified FROM decisions d
        JOIN attempts a ON a.decision_id = d.id AND a.rung = 'coder'
        WHERE d.verdict IS NOT NULL AND a.verified IS NOT NULL""").fetchall()
    say(f"\njudge calibration on {len(rows)} labelled coder attempts")
    edges = [(0.0, fallback), (fallback, near), (near, 1.0001)]
    counts = [[0, 0] for _ in edges]
    for r in rows:
        p = json.loads(r["verdict"])["p_local"]
        for i, (lo, hi) in enumerate(edges):
            if lo <= p < hi:
                counts[i][0 if r["verified"] else 1] += 1
    for (lo, hi), (v, f) in zip(edges, counts):
        say(f"  P(local) {lo:.2f}-{min(hi, 1.0):.2f}: {v:>3}/{v + f:<3} passed ({_rate(v, f)})")
    say(f"  local_threshold ({near}) belongs where the pass rate is near-certain;"
        f" fallback_threshold ({fallback}) where a try still beats paying OpenRouter.")
    _shadow_section(db, pol, say)

    hits = db.execute("SELECT ts, until, reset_source FROM limit_hits ORDER BY ts DESC LIMIT 5").fetchall()
    if hits:
        say("\nrecent Claude Code limit hits")
        for h in hits:
            say(f"  {_day(h['ts'], cfg)}  locked out until {_day(h['until'], cfg)}"
                f"  ({h['reset_source'] or 'weekly reset'})")
    return "\n".join(lines)


def main() -> None:
    db_path = settings.ledger_path()
    if not db_path.exists():
        sys.exit(f"no ledger yet at {db_path}: nothing has been routed")
    print(f"ledger: {db_path}\n")
    # Opened through Ledger so an older ledger gains the current columns first.
    print(render(Ledger(db_path).db, settings.load(), time.time()))


if __name__ == "__main__":
    main()

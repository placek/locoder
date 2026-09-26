"""Weekly pacing of the Claude Code (Max) limit, and the rung decision built on it.

The limit is weekly and opaque, so the pacer works in whatever cost unit Claude Code reports
(``total_cost_usd`` in ``--output-format json``) and learns the size of the week from the
spend recorded at each limit hit. Spending is allowed to run ``pace_slack`` ahead of a
straight line across the week; beyond that, Claude Code is kept for hard tasks only and the
rest goes to OpenRouter, so the limit lasts until the reset instead of dying two days early.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from typing import List, Optional, Tuple
from zoneinfo import ZoneInfo

from .judge import Verdict

WEEK_S = 7 * 24 * 3600
RUNGS = ("coder", "claude", "openrouter")


def window_bounds(now: float, reset_weekday: int, reset_hour: int, tz: str) -> Tuple[float, float]:
    """Start and end (epoch seconds) of the weekly window containing *now*.

    ``reset_weekday``: 0 = Monday ... 6 = Sunday, local to *tz*.
    """
    zone = ZoneInfo(tz)
    local = datetime.fromtimestamp(now, zone)
    days_back = (local.weekday() - reset_weekday) % 7
    start = (local - timedelta(days=days_back)).replace(hour=reset_hour, minute=0, second=0, microsecond=0)
    if start > local:
        start -= timedelta(days=7)
    end = start + timedelta(days=7)
    return start.timestamp(), end.timestamp()


@dataclass
class Pace:
    window_start: float
    window_end: float
    elapsed: float        # 0..1 share of the week gone
    spent: float
    budget: float
    budget_source: str    # "observed" (median of limit hits) or "configured"
    allowance: float      # what may be spent by now: budget * (elapsed + slack)
    exhausted_until: Optional[float]

    @property
    def exhausted(self) -> bool:
        return self.exhausted_until is not None

    @property
    def ahead(self) -> bool:
        return self.spent > self.allowance

    def as_dict(self) -> dict:
        d = asdict(self)
        d.update(exhausted=self.exhausted, ahead=self.ahead,
                 spent=round(self.spent, 2), budget=round(self.budget, 2),
                 allowance=round(self.allowance, 2), elapsed=round(self.elapsed, 3))
        return d


def pace(ledger, now: float, cfg: dict) -> Pace:
    week = cfg["claude"]["week"]
    start, end = window_bounds(now, int(week["reset_weekday"]), int(week["reset_hour"]), week["timezone"])
    observed = ledger.observed_budget()
    budget = observed if observed else float(cfg["claude"]["weekly_budget"])
    elapsed = min(1.0, max(0.0, (now - start) / WEEK_S))
    slack = float(cfg["policy"]["pace_slack"])
    return Pace(
        window_start=start, window_end=end, elapsed=elapsed,
        spent=ledger.claude_spent(start), budget=budget,
        budget_source="observed" if observed else "configured",
        allowance=budget * min(1.0, elapsed + slack),
        exhausted_until=ledger.exhausted_until(now),
    )


@dataclass
class Decision:
    rung: str
    reason: str
    chain: List[str]      # where the cascade goes if this rung fails verification


def paid_rung(p: Pace, expected_difficulty: Optional[float], cfg: dict) -> Tuple[str, str]:
    """Claude Code or OpenRouter, given the pace and how hard the task looks."""
    openrouter_ok = bool(cfg["openrouter"]["enabled"])
    if p.exhausted:
        if openrouter_ok:
            return "openrouter", "Claude Code weekly limit exhausted until the reset"
        return "claude", "Claude Code exhausted and OpenRouter disabled; it will fail until the reset"
    hard = expected_difficulty is not None and expected_difficulty >= float(cfg["policy"]["hard_difficulty"])
    if not p.ahead:
        return "claude", "Claude Code within weekly pace"
    if hard or not openrouter_ok:
        return "claude", "ahead of weekly pace, but the task is hard enough to spend Claude Code on"
    return "openrouter", "ahead of weekly pace: saving Claude Code for harder tasks"


def decide(verdict: Optional[Verdict], p: Pace, cfg: dict, judge_error: Optional[str] = None) -> Decision:
    pol = cfg["policy"]
    paid, paid_reason = paid_rung(p, verdict.expected_difficulty if verdict else None, cfg)
    # The other paid rung backs the chosen one up, when it is usable at all.
    alt = "openrouter" if paid == "claude" else "claude"
    alt_usable = cfg["openrouter"]["enabled"] if alt == "openrouter" else not p.exhausted
    tail = [paid, alt] if alt_usable else [paid]

    if verdict is None:
        rung = pol["fail_open_rung"] if pol["fail_open_rung"] in RUNGS else "coder"
        reason = f"judge unavailable ({judge_error}); starting on {rung}, the cascade escalates on failure"
        return Decision(rung, reason, [rung] + [r for r in tail if r != rung])

    local_ok = (verdict.p_local >= float(pol["local_threshold"])
                and verdict.expected_difficulty <= float(pol["local_max_difficulty"]))
    if local_ok:
        reason = (f"judge: P(local finishes)={verdict.p_local:.2f}, "
                  f"difficulty≈{verdict.expected_difficulty:.1f}; trying the local coder first")
        return Decision("coder", reason, ["coder"] + tail)
    reason = (f"judge: P(local finishes)={verdict.p_local:.2f}, difficulty≈{verdict.expected_difficulty:.1f}"
              f" — skipping the coder; {paid_reason}")
    return Decision(paid, reason, tail)

"""Where a delegated task starts, and where it goes if that fails.

Claude Code is the default worker and is spent freely; there is no rationing. The local
coder takes a task up front only when the judge is near-certain it will finish, and covers
for Claude Code while a limit has it locked out. OpenRouter is the last resort.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from typing import List, Optional, Tuple
from zoneinfo import ZoneInfo

from .judge import Verdict

RUNGS = ("coder", "claude", "openrouter")
# A session's routing mode: "auto" routes by the judge; "claude" and "local" force one rung.
MODES = ("auto", "claude", "local")
# The decision's rung when nothing may run: the task goes back to the user.
HAND_BACK = "user"


def window_bounds(now: float, reset_weekday: int, reset_hour: int, tz: str) -> Tuple[float, float]:
    """Start and end (epoch seconds) of the weekly Max window containing *now*.

    ``reset_weekday``: 0 = Monday ... 6 = Sunday, local to *tz*. Used to group results by week.
    """
    zone = ZoneInfo(tz)
    local = datetime.fromtimestamp(now, zone)
    days_back = (local.weekday() - reset_weekday) % 7
    start = (local - timedelta(days=days_back)).replace(hour=reset_hour, minute=0, second=0, microsecond=0)
    if start > local:
        start -= timedelta(days=7)
    end = start + timedelta(days=7)
    return start.timestamp(), end.timestamp()


def week_bounds(now: float, cfg: dict) -> Tuple[float, float]:
    week = cfg["claude"]["week"]
    return window_bounds(now, int(week["reset_weekday"]), int(week["reset_hour"]), week["timezone"])


@dataclass
class ClaudeState:
    """Whether Claude Code can take work now. Only a recorded limit hit makes it unavailable."""
    unavailable_until: Optional[float]

    @property
    def available(self) -> bool:
        return self.unavailable_until is None

    def as_dict(self) -> dict:
        return dict(asdict(self), available=self.available)


def claude_state(ledger, now: float) -> ClaudeState:
    return ClaudeState(unavailable_until=ledger.exhausted_until(now))


@dataclass
class Decision:
    rung: str             # the first rung to run, or HAND_BACK when none may
    reason: str
    chain: List[str]      # rungs in order; a rung listed twice is its one retry. After the last: the user.


def when(ts: float, cfg: dict) -> str:
    return datetime.fromtimestamp(ts, ZoneInfo(cfg["claude"]["week"]["timezone"])).strftime("%a %d %b %H:%M")


def decide(verdict: Optional[Verdict], claude: ClaudeState, cfg: dict,
           judge_error: Optional[str] = None, mode: str = "auto") -> Decision:
    pol = cfg["policy"]
    if verdict is None:
        seen = f"judge unavailable ({judge_error})"
    else:
        seen = f"judge: P(local finishes)={verdict.p_local:.2f}, difficulty≈{verdict.expected_difficulty:.1f}"
        if verdict.coverage < float(pol["min_coverage"]):
            # The answers were renormalised over mass the model mostly put elsewhere: a guess.
            seen += (f", but coverage {verdict.coverage:.2f} is below policy.min_coverage, "
                     "so it is treated as no verdict")
            verdict = None

    if mode == "local":
        return Decision("coder", f"routing mode local forces the coder, with no retry ({seen})", ["coder"])
    if mode == "claude":
        if claude.available:
            return Decision("claude", f"routing mode claude forces Claude Code ({seen})", ["claude", "claude"])
        return Decision(HAND_BACK, f"routing mode claude, and Claude Code is unavailable until "
                                   f"{when(claude.unavailable_until, cfg)}: back to the user", [])

    if claude.available:
        near_certain = (verdict is not None
                        and verdict.p_local >= float(pol["local_threshold"])
                        and verdict.expected_difficulty <= float(pol["local_max_difficulty"]))
        if near_certain:
            return Decision("coder", f"{seen}; near-certain, so the local coder goes first",
                            ["coder", "claude", "claude"])
        return Decision("claude", f"{seen}; Claude Code is the default", ["claude", "claude"])

    out = f"Claude Code unavailable until {when(claude.unavailable_until, cfg)}"
    if not cfg["openrouter"]["enabled"]:
        return Decision("coder", f"{seen}; {out} and OpenRouter disabled: the local coder is all that is left",
                        ["coder"])
    worth_a_try = verdict is not None and verdict.p_local >= float(pol["fallback_threshold"])
    if worth_a_try:
        return Decision("coder", f"{seen}; {out}: the local coder has a fair chance, OpenRouter backs it up",
                        ["coder", "openrouter"])
    return Decision("openrouter", f"{seen}; {out}: too unlikely for the local coder", ["openrouter"])


def paid_rung(claude: ClaudeState, cfg: dict) -> Tuple[str, str]:
    """escalate(backend='auto'): Claude Code while it is available, else OpenRouter."""
    if claude.available:
        return "claude", "Claude Code is available"
    if cfg["openrouter"]["enabled"]:
        return "openrouter", f"Claude Code unavailable until {when(claude.unavailable_until, cfg)}"
    return "claude", "Claude Code is locked out and OpenRouter disabled; it will fail until the reset"

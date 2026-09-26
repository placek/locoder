"""The four agent-facing tools. Handlers return JSON strings, as Hermes tools do."""
from __future__ import annotations

import json
import time
from typing import Any, Callable, Dict, Optional

from . import backends, policy, settings
from .judge import Judge, JudgeError, verdict_dict
from .ledger import Ledger

_BRIEF = {
    "type": "string",
    "description": (
        "The self-contained task brief: the goal, the files or seam involved, the acceptance "
        "check (the exact command that must pass), and 'touch nothing else'."
    ),
}

ROUTE_SCHEMA = {
    "name": "route",
    "description": (
        "Decide where a delegated coding task should START: the local coder subagent, Claude Code, "
        "or Claude Code on OpenRouter. Asks the local judge model how hard the task is and whether the "
        "local coder can finish it, and checks the weekly Claude Code pace. Call it once per delegated "
        "task, before delegating, with the brief you are about to hand over. Returns a decision_id "
        "(pass it to escalate and route_outcome), the starting rung, and the fallback chain."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "brief": _BRIEF,
            "workdir": {"type": "string", "description": "Absolute path of the project checkout."},
        },
        "required": ["brief"],
    },
}

ESCALATE_SCHEMA = {
    "name": "escalate",
    "description": (
        "Run a task on a paid rung: Claude Code (Max subscription) or Claude Code on OpenRouter. "
        "backend='auto' picks by weekly pace and switches to OpenRouter by itself if the Max limit "
        "hits mid-run. Blocks until the run ends; returns its final report, cost and turn count. The "
        "report is a claim: run the acceptance check yourself afterwards."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "brief": _BRIEF,
            "workdir": {"type": "string", "description": "Absolute path of the project checkout (must be under an allowed root)."},
            "decision_id": {"type": "string", "description": "The id route() returned."},
            "backend": {"type": "string", "enum": ["auto", "claude", "openrouter"], "default": "auto"},
            "max_turns": {"type": "integer", "description": "Override the configured turn cap."},
        },
        "required": ["brief", "workdir"],
    },
}

OUTCOME_SCHEMA = {
    "name": "route_outcome",
    "description": (
        "Record whether a delegated attempt passed its acceptance check. Call it after verifying every "
        "attempt — coder or paid — with the decision_id from route(). These labels are what the routing "
        "is tuned on; an unrecorded attempt teaches nothing."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "decision_id": {"type": "string"},
            "rung": {"type": "string", "enum": ["coder", "claude", "openrouter"]},
            "verified": {"type": "boolean", "description": "True only if you ran the acceptance check and it passed."},
            "notes": {"type": "string", "description": "One line: why it failed, or anything surprising."},
        },
        "required": ["decision_id", "rung", "verified"],
    },
}

STATUS_SCHEMA = {
    "name": "routing_status",
    "description": "Weekly Claude Code pace (spent vs. budget vs. time elapsed, limit state) and this week's results per rung.",
    "parameters": {"type": "object", "properties": {}},
}


def _json(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, default=str)


def _error(message: str) -> str:
    return _json({"error": message})


class Router:
    """Holds the ledger and judge for one Hermes process. Config is re-read per call, so an
    edited routing.yaml applies on the next tool call, no restart."""

    def __init__(self, clock: Callable[[], float] = time.time, judge_factory=None, ledger: Optional[Ledger] = None,
                 config_loader: Callable[[], dict] = settings.load):
        self.clock = clock
        self._ledger = ledger
        self._judge_factory = judge_factory or (lambda cfg: Judge(
            cfg["llama"]["base_url"], cfg["llama"]["judge_model"], cfg["llama"]["timeout_s"]))
        self._load = config_loader

    @property
    def ledger(self) -> Ledger:
        if self._ledger is None:
            self._ledger = Ledger(settings.ledger_path())
        return self._ledger

    # -- route ----------------------------------------------------------------
    def route(self, args: Dict[str, Any], **_: Any) -> str:
        brief = str(args.get("brief") or "").strip()
        if not brief:
            return _error("brief is required")
        cfg = self._load()
        verdict, judge_error = None, None
        try:
            verdict = self._judge_factory(cfg).judge(brief)
        except JudgeError as exc:
            judge_error = str(exc)
        p = policy.pace(self.ledger, self.clock(), cfg)
        d = policy.decide(verdict, p, cfg, judge_error)
        vd, pd = verdict_dict(verdict), p.as_dict()
        decision_id = self.ledger.add_decision(brief, args.get("workdir"), d.rung, d.reason, vd, pd)
        return _json({"decision_id": decision_id, "rung": d.rung, "chain": d.chain, "reason": d.reason,
                      "judge": vd, "pace": _pace_summary(pd)})

    # -- escalate -------------------------------------------------------------
    async def escalate(self, args: Dict[str, Any], **_: Any) -> str:
        cfg = self._load()
        brief = str(args.get("brief") or "").strip()
        if not brief:
            return _error("brief is required")
        try:
            workdir = backends.check_workdir(str(args.get("workdir") or ""), cfg["claude"]["workdir_roots"])
        except backends.BackendError as exc:
            return _error(str(exc))
        decision_id = args.get("decision_id") or None
        requested = str(args.get("backend") or "auto")
        if requested not in ("auto", "claude", "openrouter"):
            return _error(f"unknown backend {requested!r}")

        p = policy.pace(self.ledger, self.clock(), cfg)
        if requested == "auto":
            expected = self._expected_difficulty(decision_id)
            rung, why = policy.paid_rung(p, expected, cfg)
        else:
            rung, why = requested, "requested explicitly"

        runs = []
        while True:
            try:
                result = await backends.run(cfg, rung, brief, workdir, args.get("max_turns"))
            except backends.BackendError as exc:
                runs.append({"rung": rung, "ok": False, "error": str(exc)})
                break
            self.ledger.add_attempt(rung, decision_id, ok=result.ok, limit_hit=result.limit_hit,
                                    cost=result.cost, turns=result.turns, duration_s=result.duration_s,
                                    session_id=result.session_id)
            runs.append(result.as_dict(int(cfg["claude"]["result_chars"])))
            if not (result.limit_hit and rung == "claude"):
                break
            # The Max window ran out mid-week: remember until when, and learn the budget from it.
            self.ledger.add_limit_hit(p.window_start, self.ledger.claude_spent(p.window_start), p.window_end)
            if requested != "auto" or not cfg["openrouter"]["enabled"]:
                break
            rung, why = "openrouter", "Claude Code hit its weekly limit mid-run; retried on OpenRouter"

        return _json({"decision_id": decision_id, "backend": runs[-1]["rung"], "why": why,
                      "ok": bool(runs[-1].get("ok")), "runs": runs,
                      "next": "Run the acceptance check yourself, then call route_outcome."})

    def _expected_difficulty(self, decision_id: Optional[str]) -> Optional[float]:
        if not decision_id:
            return None
        row = self.ledger.decision(decision_id)
        if row is None or not row["verdict"]:
            return None
        return json.loads(row["verdict"]).get("expected_difficulty")

    # -- outcome & status -----------------------------------------------------
    def outcome(self, args: Dict[str, Any], **_: Any) -> str:
        decision_id = str(args.get("decision_id") or "")
        rung = str(args.get("rung") or "")
        if rung not in policy.RUNGS:
            return _error(f"rung must be one of {list(policy.RUNGS)}")
        if self.ledger.decision(decision_id) is None:
            return _error(f"unknown decision_id {decision_id!r}")
        self.ledger.set_verified(decision_id, rung, bool(args.get("verified")), args.get("notes"))
        return _json({"recorded": True, "decision_id": decision_id, "rung": rung,
                      "verified": bool(args.get("verified"))})

    def status(self, args: Dict[str, Any], **_: Any) -> str:
        cfg = self._load()
        p = policy.pace(self.ledger, self.clock(), cfg)
        return _json({"pace": _pace_summary(p.as_dict()), "this_week": self.ledger.stats(p.window_start)})


def _pace_summary(pd: dict) -> dict:
    keys = ("spent", "allowance", "budget", "budget_source", "elapsed", "ahead", "exhausted", "exhausted_until")
    return {k: pd[k] for k in keys}


def register(ctx, router: Optional[Router] = None) -> Router:
    """Register into the ``coding`` toolset: under ``agent.coding_context: focus`` Hermes collapses the
    tool list to that toolset (plus MCP), so a tool anywhere else would silently disappear."""
    router = router or Router()
    ctx.register_tool(name="route", toolset="coding", schema=ROUTE_SCHEMA, handler=router.route,
                      description="Pick the starting rung for a delegated task.", emoji="🧭")
    ctx.register_tool(name="escalate", toolset="coding", schema=ESCALATE_SCHEMA, handler=router.escalate,
                      is_async=True, description="Run a task on Claude Code or OpenRouter.", emoji="🚀")
    ctx.register_tool(name="route_outcome", toolset="coding", schema=OUTCOME_SCHEMA, handler=router.outcome,
                      description="Label a delegated attempt as verified or not.", emoji="🏷️")
    ctx.register_tool(name="routing_status", toolset="coding", schema=STATUS_SCHEMA, handler=router.status,
                      description="Weekly Claude Code pace and per-rung results.", emoji="📊")
    return router

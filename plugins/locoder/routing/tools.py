"""The agent-facing routing tools. Handlers return JSON strings, as Hermes tools do."""
from __future__ import annotations

import contextlib
import json
import os
import subprocess
import time
from typing import Any, Callable, Dict, Optional, Tuple

from . import backends, graft, policy, settings
from .judge import Judge, JudgeError, SemIfBackend, Verdict, verdict_dict
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
        "Decide where a delegated coding task should START and where it goes if that fails. Claude "
        "Code is the default; the local coder goes first only when the local judge model is near-certain "
        "it will finish, or when a limit has Claude Code locked out; OpenRouter is the last resort. Call "
        "it once per delegated task, before delegating, with the brief you are about to hand over. "
        "Returns a decision_id (pass it to escalate and route_outcome), the starting rung, and the "
        "chain: follow it in order, a rung listed twice is its one retry, and after the last the task "
        "goes back to the user."
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
        "Run a task on a paid rung: Claude Code (Max subscription), or a one-shot run of this Hermes "
        "profile on a cheaper OpenRouter model. "
        "backend='auto' picks Claude Code while it is available, else OpenRouter. Blocks until the run "
        "ends; returns its final report, cost and turn count. The report is a claim: run the acceptance "
        "check yourself afterwards. If a Max limit hits mid-run, the result says until when Claude Code "
        "is locked out and gives the rest of this task's chain to follow instead."
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
    "description": "Whether Claude Code is available or locked out by a limit (and until when), and this week's results per rung.",
    "parameters": {"type": "object", "properties": {}},
}

MODE_SCHEMA = {
    "name": "routing_mode",
    "description": (
        "Set or show this session's routing mode. 'auto' (every session starts here) routes each "
        "delegated task by the judge; 'claude' uses Claude Code only, handing the task back to the user "
        "while it is locked out; 'local' uses the local coder only, handing a failed task straight back. "
        "Call it only when the user asks to switch; omit mode to show the current one."
    ),
    "parameters": {
        "type": "object",
        "properties": {"mode": {"type": "string", "enum": list(policy.MODES)}},
    },
}


def git_head(workdir: str) -> Optional[Tuple[str, bool]]:
    """(HEAD commit, has uncommitted changes) of *workdir*, or None when it is not a readable git checkout."""
    def git(*argv: str) -> str:
        return subprocess.run(["git", "-C", workdir, *argv], capture_output=True, text=True,
                              timeout=10, check=True).stdout
    try:
        sha = git("rev-parse", "--verify", "HEAD").strip()
        dirty = bool(git("status", "--porcelain").strip())
    except (OSError, subprocess.SubprocessError):
        return None
    return sha, dirty


def hermes_conversation_root(session_id: str) -> str:
    """The session's lineage root in Hermes' session DB. Compression rotates a conversation's
    session id; the root stays, so a routing mode keyed by it survives compression."""
    try:
        from hermes_state import SessionDB
    except ImportError:
        return session_id
    db = None
    try:
        db = SessionDB(read_only=True)
        return db.get_conversation_root(session_id) or session_id
    except Exception:
        return session_id
    finally:
        close = getattr(db, "close", None)
        if callable(close):
            with contextlib.suppress(Exception):
                close()


def _child_refusal() -> Optional[str]:
    """Inside the OpenRouter rung's one-shot run, routing belongs to the orchestrator."""
    if os.environ.get(backends.CHILD_ENV):
        return _error("This is a delegated run: do the task yourself with the file and terminal tools. "
                      "Routing, escalation and routing modes belong to the orchestrator.")
    return None


def _json(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, default=str)


def _error(message: str) -> str:
    return _json({"error": message})


class Router:
    """Holds the ledger, the judge and each session's routing mode for one Hermes process.
    Config is re-read per call, so an edited routing.yaml applies on the next tool call."""

    def __init__(self, clock: Callable[[], float] = time.time, judge_factory=None, ledger: Optional[Ledger] = None,
                 config_loader: Callable[[], dict] = settings.load,
                 conversation_root: Callable[[str], str] = hermes_conversation_root):
        self.clock = clock
        self._ledger = ledger
        self._judge_factory = judge_factory or (lambda cfg: Judge(SemIfBackend(
            cfg["llama"]["base_url"], cfg["llama"]["judge_model"], cfg["llama"]["timeout_s"])))
        self._load = config_loader
        self._conversation_root = conversation_root
        # Keyed by conversation root; a conversation never switched is "auto".
        self._modes: Dict[str, str] = {}
        self._roots: Dict[str, str] = {}
        # Repos whose graft wiring failed: not retried until Hermes restarts.
        self._graft_failed: set = set()

    def _conversation(self, kwargs: Dict[str, Any]) -> str:
        """Hermes passes the calling session's id to every handler; map it to its conversation root.
        Only a resolved rotation is cached: a new session's DB row may not exist yet."""
        sid = str(kwargs.get("session_id") or "")
        if not sid:
            return ""
        if sid not in self._roots:
            root = self._conversation_root(sid) or sid
            if root == sid:
                return sid
            self._roots[sid] = root
        return self._roots[sid]

    def _mode(self, kwargs: Dict[str, Any]) -> str:
        return self._modes.get(self._conversation(kwargs), "auto")

    @property
    def ledger(self) -> Ledger:
        if self._ledger is None:
            self._ledger = Ledger(settings.ledger_path())
        return self._ledger

    # -- route ----------------------------------------------------------------
    def route(self, args: Dict[str, Any], **kwargs: Any) -> str:
        if refusal := _child_refusal():
            return refusal
        brief = str(args.get("brief") or "").strip()
        if not brief:
            return _error("brief is required")
        cfg = self._load()
        mode = self._mode(kwargs)
        verdict, judge_error = None, None
        try:
            verdict = self._judge_factory(cfg).judge(brief)
        except JudgeError as exc:
            judge_error = str(exc)
        claude = policy.claude_state(self.ledger, self.clock())
        d = policy.decide(verdict, claude, cfg, judge_error, mode=mode)
        vd, cd = verdict_dict(verdict), claude.as_dict()
        workdir = args.get("workdir") or None
        # Wire graft first, so the commit recorded below is the one the task starts from.
        graft_result = graft.ensure(workdir, cfg, self._graft_failed)
        head = git_head(workdir) if workdir else None
        decision_id = self.ledger.add_decision(brief, workdir, d.rung, d.reason, vd, cd,
                                               commit_sha=head[0] if head else None,
                                               dirty=head[1] if head else None, mode=mode)
        out = {"decision_id": decision_id, "rung": d.rung, "chain": d.chain, "reason": d.reason,
               "mode": mode, "judge": vd, "claude": cd, "graft": graft_result}
        if d.rung == policy.HAND_BACK:
            out["next"] = "Nothing may run this task now: tell the user why (the reason) and stop."
        return _json(out)

    # -- escalate -------------------------------------------------------------
    async def escalate(self, args: Dict[str, Any], **kwargs: Any) -> str:
        if refusal := _child_refusal():
            return refusal
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

        now = self.clock()
        mode = self._mode(kwargs)
        claude_now = policy.claude_state(self.ledger, now)
        if mode == "local":
            return _error("This session's routing mode is local: paid rungs are off. Use the coder, or ask the "
                          "user to switch with /routing-mode.")
        if mode == "claude":
            if requested == "openrouter":
                return _error("This session's routing mode is claude: OpenRouter is off.")
            if not claude_now.available:
                return _json({"decision_id": decision_id, "backend": None, "ok": False, "chain": [],
                              "claude_unavailable_until": policy.when(claude_now.unavailable_until, cfg),
                              "next": "Claude Code is locked out and this session's routing mode is claude: "
                                      "tell the user when it resets and stop."})
            rung, why = "claude", "routing mode claude"
        elif requested == "auto":
            rung, why = policy.paid_rung(claude_now, cfg)
        else:
            rung, why = requested, "requested explicitly"

        try:
            result = await backends.run(cfg, rung, brief, workdir, args.get("max_turns"))
        except backends.BackendError as exc:
            return _json({"decision_id": decision_id, "backend": rung, "why": why, "ok": False,
                          "error": str(exc)})
        self.ledger.add_attempt(rung, decision_id, ok=result.ok, limit_hit=result.limit_hit,
                                cost=result.cost, turns=result.turns, duration_s=result.duration_s,
                                session_id=result.session_id)
        out = {"decision_id": decision_id, "backend": rung, "why": why, "ok": result.ok,
               "run": result.as_dict(int(cfg["claude"]["result_chars"])),
               "next": "Run the acceptance check yourself, then call route_outcome."}
        if result.limit_hit and rung == "claude":
            until = self._lock_out_claude(result.result, now, cfg)
            after = policy.decide(self._stored_verdict(decision_id), policy.ClaudeState(until), cfg,
                                  judge_error="no verdict recorded for this task", mode=mode)
            # A rung this task already failed is not tried again.
            failed = self.ledger.failed_rungs(decision_id) if decision_id else set()
            after.chain = [r for r in after.chain if r not in failed]
            out.update(claude_unavailable_until=policy.when(until, cfg), chain=after.chain,
                       chain_reason=after.reason,
                       next=("Claude Code hit a limit before finishing; nothing it did counts. Park any "
                             "changes, then continue with `chain` — the rest of this task's route now that "
                             "Claude Code is locked out — instead of the chain route() returned."
                             if after.chain else
                             "Claude Code hit a limit before finishing; nothing it did counts. Park any "
                             "changes, tell the user when it resets (chain_reason), and stop."))
        return _json(out)

    def _lock_out_claude(self, error_text: str, now: float, cfg: dict) -> float:
        """Record a limit hit; Claude Code stays unavailable until the reset the error states."""
        c = cfg["claude"]
        until = backends.reset_time(error_text, now, c["week"]["timezone"])
        source = "stated"
        if until is None:
            until, source = now + float(c["limit_fallback_s"]), "fallback"
        week_start, _ = policy.week_bounds(now, cfg)
        self.ledger.add_limit_hit(until, raw=error_text[:2000], reset_source=source,
                                  window_start=week_start, spent=self.ledger.claude_spent(week_start))
        return until

    def _stored_verdict(self, decision_id: Optional[str]) -> Optional[Verdict]:
        row = self.ledger.decision(decision_id) if decision_id else None
        if row is None or not row["verdict"]:
            return None
        return Verdict(**json.loads(row["verdict"]))

    # -- outcome & status -----------------------------------------------------
    def outcome(self, args: Dict[str, Any], **_: Any) -> str:
        if refusal := _child_refusal():
            return refusal
        decision_id = str(args.get("decision_id") or "")
        rung = str(args.get("rung") or "")
        if rung not in policy.RUNGS:
            return _error(f"rung must be one of {list(policy.RUNGS)}")
        if self.ledger.decision(decision_id) is None:
            return _error(f"unknown decision_id {decision_id!r}")
        self.ledger.set_verified(decision_id, rung, bool(args.get("verified")), args.get("notes"))
        return _json({"recorded": True, "decision_id": decision_id, "rung": rung,
                      "verified": bool(args.get("verified"))})

    def status(self, args: Dict[str, Any], **kwargs: Any) -> str:
        cfg = self._load()
        now = self.clock()
        week_start, _ = policy.week_bounds(now, cfg)
        return _json({"mode": self._mode(kwargs), "claude": policy.claude_state(self.ledger, now).as_dict(),
                      "this_week": self.ledger.stats(week_start)})

    def set_mode(self, args: Dict[str, Any], **kwargs: Any) -> str:
        mode = args.get("mode")
        if mode is None:
            return _json({"mode": self._mode(kwargs)})
        if mode not in policy.MODES:
            return _error(f"mode must be one of {list(policy.MODES)}")
        if refusal := _child_refusal():
            return refusal
        self._modes[self._conversation(kwargs)] = mode
        return _json({"mode": mode})


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
                      description="Claude Code availability and per-rung results.", emoji="📊")
    ctx.register_tool(name="routing_mode", toolset="coding", schema=MODE_SCHEMA, handler=router.set_mode,
                      description="Set or show this session's routing mode.", emoji="🔀")
    return router

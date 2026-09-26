"""Paid rungs: Claude Code on the Max subscription, and the same CLI pointed at OpenRouter.

Both run ``claude -p`` headless on the host, in the task's workdir, with an explicit tool
allow-list. Using the one CLI for both means one brief format, one JSON result shape, and one
place where usage is read — OpenRouter serves an Anthropic-compatible endpoint that Claude
Code can use when ``ANTHROPIC_BASE_URL`` points at it. The OpenRouter flavour runs with its
own ``CLAUDE_CONFIG_DIR`` so the cached Max login never collides with the gateway key.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

# What Claude Code prints when a subscription limit is used up. Deliberately broad: a false
# positive only locks Claude Code out briefly, a miss fails every task until the reset.
LIMIT_PATTERN = re.compile(
    r"(usage|rate|weekly|session)[ -]limit|limit (reached|exceeded|hit)|out of (extra )?usage|"
    r"hit your (\w+ )?limit|resets? (at|on|in) ",
    re.IGNORECASE,
)

# The `-p` wording of the reset time is undocumented; these cover the shapes seen in the
# interactive CLI. Every limit hit keeps its raw text in the ledger, so a new shape can be
# added here from a real error.
_EPOCH = re.compile(r"limit reached\|(\d{10})", re.IGNORECASE)
_RELATIVE = re.compile(
    r"resets?\s+in\s+(?:(?P<d>\d+)\s*(?:d|days?)\b\s*)?(?:(?P<h>\d+)\s*(?:h|hrs?|hours?)\b\s*)?"
    r"(?:(?P<m>\d+)\s*(?:m|mins?|minutes?)\b)?",
    re.IGNORECASE,
)
_MONTHS = ("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec")
_WEEKDAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
_ABSOLUTE = re.compile(
    r"resets?\s+(?:at\s+|on\s+)?"
    rf"(?:(?P<wd>{'|'.join(_WEEKDAYS)})[a-z]*,?\s+|(?P<mon>{'|'.join(_MONTHS)})[a-z]*\.?\s+(?P<day>\d{{1,2}}),?\s+)?"
    r"(?:at\s+)?(?P<hour>\d{1,2})(?::(?P<minute>\d{2}))?\s*(?P<ampm>am|pm)?",
    re.IGNORECASE,
)
_TZ_IN_TEXT = re.compile(r"\((UTC|[A-Za-z]+(?:/[A-Za-z0-9_+-]+)+)\)")
_MAX_LOCKOUT_S = 8 * 24 * 3600


def reset_time(text: str, now: float, default_tz: str) -> Optional[float]:
    """When a limit error says the limit resets (epoch seconds), or None if it does not say.

    Clock times without a date mean their next occurrence; anything that is not in the
    future, or more than about a week out, is treated as unreadable.
    """
    found = _parse_reset(text, now, default_tz)
    if found is None or not now < found <= now + _MAX_LOCKOUT_S:
        return None
    return found


def _parse_reset(text: str, now: float, default_tz: str) -> Optional[float]:
    m = _EPOCH.search(text)
    if m:
        return float(m.group(1))
    m = _RELATIVE.search(text)
    if m and any(m.group(k) for k in ("d", "h", "m")):
        return now + sum(int(m.group(k) or 0) * s for k, s in (("d", 86400), ("h", 3600), ("m", 60)))
    m = _ABSOLUTE.search(text)
    if not m or (m.group("minute") is None and m.group("ampm") is None):
        return None
    tz_match = _TZ_IN_TEXT.search(text)
    try:
        zone = ZoneInfo(tz_match.group(1) if tz_match else default_tz)
    except (ZoneInfoNotFoundError, ValueError):
        zone = ZoneInfo(default_tz)
    hour, minute = int(m.group("hour")), int(m.group("minute") or 0)
    ampm = (m.group("ampm") or "").lower()
    if ampm == "pm" and hour < 12:
        hour += 12
    elif ampm == "am" and hour == 12:
        hour = 0
    if hour > 23 or minute > 59:
        return None
    local_now = datetime.fromtimestamp(now, zone)
    if m.group("mon"):
        month = _MONTHS.index(m.group("mon").lower()[:3]) + 1
        try:
            at = local_now.replace(month=month, day=int(m.group("day")), hour=hour, minute=minute,
                                   second=0, microsecond=0)
        except ValueError:
            return None
        if at < local_now - timedelta(days=1):
            at = at.replace(year=at.year + 1)
        return at.timestamp()
    at = local_now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if m.group("wd"):
        at += timedelta(days=(_WEEKDAYS.index(m.group("wd").lower()[:3]) - local_now.weekday()) % 7)
        if at <= local_now:
            at += timedelta(days=7)
    elif at <= local_now:
        at += timedelta(days=1)
    return at.timestamp()

# Variables that would silently switch the Max path to pay-per-token API billing or to
# another endpoint. Stripped for the subscription rung, set explicitly for the OpenRouter one.
_AUTH_VARS = ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_BASE_URL", "CLAUDE_CONFIG_DIR")
_TIER_VARS = ("ANTHROPIC_DEFAULT_FABLE_MODEL", "ANTHROPIC_DEFAULT_OPUS_MODEL",
              "ANTHROPIC_DEFAULT_SONNET_MODEL", "ANTHROPIC_DEFAULT_HAIKU_MODEL", "CLAUDE_CODE_SUBAGENT_MODEL")


class BackendError(RuntimeError):
    pass


@dataclass
class RunResult:
    rung: str
    ok: bool
    limit_hit: bool
    cost: float
    turns: Optional[int]
    duration_s: float
    session_id: Optional[str]
    result: str
    exit_code: int

    def as_dict(self, result_chars: int) -> dict:
        d = asdict(self)
        if len(self.result) > result_chars:
            d["result"] = self.result[:result_chars] + f"\n…[truncated, {len(self.result)} chars total]"
        return d


def check_workdir(workdir: str, roots: List[str]) -> Path:
    path = Path(workdir).expanduser().resolve()
    if not path.is_dir():
        raise BackendError(f"workdir does not exist: {path}")
    allowed = [Path(r).expanduser().resolve() for r in roots]
    if not any(path == root or root in path.parents for root in allowed):
        raise BackendError(f"workdir {path} is outside the allowed roots {[str(r) for r in allowed]}")
    return path


def command(cfg: dict, rung: str, brief: str, max_turns: Optional[int] = None) -> List[str]:
    c = cfg["claude"]
    cmd = [c["bin"], "-p", brief, "--output-format", "json",
           "--max-turns", str(int(max_turns or c["max_turns"])),
           "--allowedTools", ",".join(c["allowed_tools"])]
    if rung == "openrouter":
        cmd += ["--model", cfg["openrouter"]["model"]]
    return cmd


def environment(cfg: dict, rung: str, base: Optional[Dict[str, str]] = None) -> Dict[str, str]:
    env = {k: v for k, v in (base if base is not None else os.environ).items() if k not in _AUTH_VARS}
    if rung != "openrouter":
        return env
    o = cfg["openrouter"]
    key = (base if base is not None else os.environ).get(o["api_key_env"], "")
    if not key:
        raise BackendError(f"OpenRouter rung needs ${o['api_key_env']} in the environment")
    config_dir = Path(os.path.expanduser(o["config_dir"]))
    config_dir.mkdir(parents=True, exist_ok=True)
    env.update({
        "ANTHROPIC_BASE_URL": o["base_url"],
        "ANTHROPIC_AUTH_TOKEN": key,
        "ANTHROPIC_API_KEY": "",
        "CLAUDE_CONFIG_DIR": str(config_dir),
    })
    for var in _TIER_VARS:
        env[var] = o["model"]
    return env


def parse(rung: str, stdout: str, stderr: str, exit_code: int, duration_s: float) -> RunResult:
    """Read Claude Code's ``--output-format json`` result; tolerate a non-JSON failure."""
    data: dict = {}
    for line in reversed(stdout.strip().splitlines()):
        try:
            data = json.loads(line)
            break
        except ValueError:
            continue
    text = str(data.get("result") or "") or (stderr.strip() or stdout.strip())
    is_error = bool(data.get("is_error")) or exit_code != 0 or not data
    limit_hit = is_error and bool(LIMIT_PATTERN.search(f"{text}\n{stderr}"))
    return RunResult(
        rung=rung, ok=not is_error, limit_hit=limit_hit,
        cost=float(data.get("total_cost_usd") or 0.0),
        turns=data.get("num_turns"), duration_s=round(duration_s, 1),
        session_id=data.get("session_id"), result=text, exit_code=exit_code,
    )


async def run(cfg: dict, rung: str, brief: str, workdir: Path, max_turns: Optional[int] = None,
              base_env: Optional[Dict[str, str]] = None) -> RunResult:
    if rung not in ("claude", "openrouter"):
        raise BackendError(f"not a paid rung: {rung}")
    cmd = command(cfg, rung, brief, max_turns)
    env = environment(cfg, rung, base_env)
    started = time.monotonic()
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd, cwd=str(workdir), env=env, stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
    except FileNotFoundError as exc:
        raise BackendError(f"Claude Code CLI not found: {cmd[0]}") from exc
    try:
        out, err = await asyncio.wait_for(proc.communicate(), timeout=float(cfg["claude"]["timeout_s"]))
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        return RunResult(rung, False, False, 0.0, None, round(time.monotonic() - started, 1), None,
                         f"timed out after {cfg['claude']['timeout_s']}s", -9)
    return parse(rung, out.decode(errors="replace"), err.decode(errors="replace"),
                 proc.returncode or 0, time.monotonic() - started)

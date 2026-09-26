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
from pathlib import Path
from typing import Dict, List, Optional

# What Claude Code prints when the subscription window is used up. Deliberately broad: a false
# positive only sends one task to OpenRouter early, a miss burns the rest of the week in errors.
LIMIT_PATTERN = re.compile(
    r"(usage|rate|weekly|session)[ -]limit|limit (reached|exceeded|hit)|out of (extra )?usage|"
    r"resets? (at|on|in) ",
    re.IGNORECASE,
)

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

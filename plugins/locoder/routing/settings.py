"""Routing settings: ``$HERMES_HOME/routing.yaml`` merged over the defaults below.

Kept outside Hermes' ``config.yaml`` on purpose: Hermes migrates and validates its own
file, and this plugin should never depend on how it treats a key it does not know.
"""
from __future__ import annotations

import copy
import os
from pathlib import Path
from typing import Any, Dict

DEFAULTS: Dict[str, Any] = {
    "llama": {
        # OpenAI-compatible llama.cpp router; the judge is one of its presets.
        "base_url": "http://127.0.0.1:8088/v1",
        "judge_model": "judge",
        "timeout_s": 30,
    },
    "policy": {
        # Start on the local coder only when the judge thinks it can finish AND the task is not hard.
        "local_threshold": 0.6,
        "local_max_difficulty": 1.5,
        # At or above this expected difficulty (0-3 scale) Claude Code is used even when ahead of pace.
        "hard_difficulty": 2.5,
        # Share of the weekly budget usable ahead of the linear schedule.
        "pace_slack": 0.10,
        # Judge unreachable or unsure: start local, the cascade escalates if it fails.
        "fail_open_rung": "coder",
    },
    "claude": {
        "bin": "claude",
        "max_turns": 15,
        "timeout_s": 1800,
        "allowed_tools": ["Read", "Edit", "Write", "Bash", "Grep", "Glob"],
        # escalate() refuses any workdir outside these roots.
        "workdir_roots": ["/srv/data/projects"],
        "week": {"reset_weekday": 0, "reset_hour": 9, "timezone": "Europe/Warsaw"},
        # Cost units per week before the Max limit hits. Only a first guess: every limit hit
        # records what had been spent, and the median of those replaces this number.
        "weekly_budget": 300.0,
        "result_chars": 8000,
    },
    "openrouter": {
        "enabled": True,
        "api_key_env": "OPENROUTER_API_KEY",
        "base_url": "https://openrouter.ai/api",
        "model": "anthropic/claude-fable-5.1",
        # Separate Claude Code config dir: no cached Max login, so no auth conflict with the gateway key.
        "config_dir": "~/.local/state/locoder/claude-openrouter",
    },
}


def hermes_home() -> Path:
    return Path(os.environ.get("HERMES_HOME", str(Path.home() / ".hermes"))).expanduser()


def _merge(base: Dict[str, Any], over: Dict[str, Any]) -> Dict[str, Any]:
    out = copy.deepcopy(base)
    for key, value in (over or {}).items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _merge(out[key], value)
        else:
            out[key] = value
    return out


def _safe_load(text: str) -> Any:
    """Hermes' own YAML loader (ruamel-based; Hermes does not ship PyYAML), PyYAML as the
    fallback for running the tests outside Hermes."""
    try:
        from hermes_yaml import safe_load
    except ImportError:
        from yaml import safe_load
    return safe_load(text)


def load(path: Path | None = None) -> Dict[str, Any]:
    """Defaults, overridden by routing.yaml when it exists. Never raises on a missing file."""
    path = path or Path(os.environ.get("LOCODER_ROUTING_CONFIG", str(hermes_home() / "routing.yaml")))
    if not path.is_file():
        return copy.deepcopy(DEFAULTS)
    with path.open(encoding="utf-8") as fh:
        data = _safe_load(fh.read()) or {}
    if not isinstance(data, dict):
        raise ValueError(f"{path}: expected a mapping at the top level")
    return _merge(DEFAULTS, data)


def ledger_path() -> Path:
    return hermes_home() / "routing" / "ledger.db"

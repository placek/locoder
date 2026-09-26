#!/usr/bin/env python3
"""`make check`: prove the installed stack is wired, against the exact Hermes build in use.

Run with the Hermes venv's Python and HERMES_HOME set (the Makefile does both). Exits
non-zero on the first class of failure, so `make bump` can roll back a Hermes update that
broke a plugin instead of finding out from a silent log line mid-task.

  --offline   skip the checks that need the running llama.cpp router
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import urllib.request
from pathlib import Path

REQUIRED_PLUGINS = {"locoder/routing": {"route", "escalate", "route_outcome", "routing_status", "routing_mode"},
                    "web/defuddle": {"web_research"}}
PRESETS = {"orchestrator", "coder", "judge"}

failures: list[str] = []


def ok(msg: str) -> None:
    print(f"  ok    {msg}")


def fail(msg: str) -> None:
    print(f"  FAIL  {msg}")
    failures.append(msg)


def warn(msg: str) -> None:
    print(f"  warn  {msg}")


def check_plugins() -> None:
    print("hermes plugins")
    from hermes_cli.plugins import discover_plugins, get_plugin_manager

    discover_plugins(force=True)
    loaded = get_plugin_manager()._plugins
    for key, tools in REQUIRED_PLUGINS.items():
        plugin = loaded.get(key) or next((p for p in loaded.values() if getattr(p.manifest, "key", None) == key), None)
        if plugin is None:
            fail(f"{key}: not discovered (is plugins/ linked into $HERMES_HOME?)")
            continue
        if plugin.error:
            fail(f"{key}: failed to load: {plugin.error}")
            continue
        if not plugin.enabled:
            fail(f"{key}: discovered but not enabled (plugins.enabled in config.yaml)")
            continue
        missing = tools - set(plugin.tools_registered)
        if missing:
            fail(f"{key}: loaded but did not register {sorted(missing)}")
        else:
            ok(f"{key}: loaded, tools {sorted(tools)}")

    from toolsets import get_toolset

    coding = set((get_toolset("coding") or {}).get("tools", []))
    missing = REQUIRED_PLUGINS["locoder/routing"] - coding
    if missing:
        fail(f"coding toolset lacks {sorted(missing)}: under coding_context: focus the agent would not see them")
    else:
        ok("routing tools are in the coding toolset (visible under coding_context: focus)")


def check_profile() -> None:
    print("profile")
    home = Path(os.environ["HERMES_HOME"])
    for name in ("config.yaml", "SOUL.md", "routing.yaml", "skills", "plugins"):
        path = home / name
        if path.exists():
            ok(f"{name} -> {os.path.realpath(path)}")
        else:
            fail(f"{name} missing from {home}")
    from hermes_yaml import safe_load

    cfg = safe_load((home / "config.yaml").read_text())
    children = cfg.get("delegation", {}).get("max_concurrent_children")
    presets = Path(os.environ.get("LOCODER_PRESETS", "")).read_text() if os.environ.get("LOCODER_PRESETS") else ""
    coder_parallel = _preset_value(presets, "coder", "parallel")
    if coder_parallel is not None and str(children) != coder_parallel:
        fail(f"delegation.max_concurrent_children={children} but coder parallel={coder_parallel}: they must match")
    elif coder_parallel is not None:
        ok(f"delegation concurrency matches coder slots ({children})")
    if not (home / "skills" / "delegate" / "SKILL.md").is_file():
        fail("skills/delegate/SKILL.md not found through the skills link")


def _preset_value(ini: str, section: str, key: str):
    current = None
    for raw in ini.splitlines():
        line = raw.split(";", 1)[0].split("#", 1)[0].strip()
        if line.startswith("[") and line.endswith("]"):
            current = line[1:-1].strip()
        elif current == section and "=" in line:
            k, v = (s.strip() for s in line.split("=", 1))
            if k == key:
                return v
    return None


def check_tools() -> None:
    print("host tools")
    claude = shutil.which("claude")
    if claude:
        out = subprocess.run([claude, "--version"], capture_output=True, text=True, timeout=30)
        ok(f"claude CLI: {out.stdout.strip() or claude}")
    else:
        fail("claude CLI not on PATH: escalate() cannot run the Claude Code rung")
    if os.environ.get("OPENROUTER_API_KEY"):
        ok("OPENROUTER_API_KEY present")
    else:
        warn("OPENROUTER_API_KEY not set: the openrouter rung's Hermes run cannot reach OpenRouter")
    if shutil.which("locoder"):
        ok("locoder on PATH (the openrouter rung runs it one-shot)")
    else:
        warn("locoder not on PATH: the openrouter rung cannot start (set openrouter.hermes_bin)")
    defuddle = os.environ.get("HERMES_DEFUDDLE_BIN", "")
    if defuddle and os.access(defuddle, os.X_OK):
        ok(f"defuddle: {defuddle}")
    else:
        fail(f"defuddle binary not executable: {defuddle or '(HERMES_DEFUDDLE_BIN unset)'}")
    want = os.environ.get("LOCODER_GRAFT_VERSION", "")
    graft = shutil.which("graft")
    if graft:
        _graft_version("host", [graft, "--version"], want)
    else:
        fail("graft not on PATH: route() cannot wire projects (make graft)")
    image = os.environ.get("LOCODER_SANDBOX_IMAGE", "locoder-sandbox:local")
    if shutil.which("docker"):
        res = subprocess.run(["docker", "image", "inspect", image], capture_output=True, timeout=30)
        (ok if res.returncode == 0 else fail)(f"sandbox image {image}" + ("" if res.returncode == 0 else " not built"))
        if res.returncode == 0:
            _graft_version("sandbox", ["docker", "run", "--rm", image, "graft", "--version"], want)
    else:
        fail("docker not on PATH")


def _graft_version(where: str, argv: list, want: str) -> None:
    """Hermes (sandbox) and Claude Code (host) must query the graph with the same graft."""
    try:
        out = subprocess.run(argv, capture_output=True, text=True, timeout=120,
                             env={**os.environ, "DO_NOT_TRACK": "1"})
    except (OSError, subprocess.SubprocessError) as exc:
        fail(f"graft ({where}) did not run: {exc}")
        return
    got = out.stdout.strip().splitlines()[-1] if out.stdout.strip() else ""
    if out.returncode != 0 or not got:
        fail(f"graft ({where}) did not report a version: {out.stderr.strip()[:200]}")
    elif want and got != want:
        fail(f"graft ({where}) is {got}, the pin is {want}: make install")
    else:
        ok(f"graft ({where}) {got}")


def check_llama() -> None:
    print("llama.cpp router")
    sys.path.insert(0, str(Path(os.environ["HERMES_HOME"]) / "plugins" / "locoder"))
    from routing import settings
    from routing.judge import Judge, JudgeError

    cfg = settings.load()
    base = cfg["llama"]["base_url"].rstrip("/")
    try:
        with urllib.request.urlopen(base + "/models", timeout=10) as resp:
            ids = {m.get("id") for m in json.loads(resp.read()).get("data", [])}
    except OSError as exc:
        fail(f"{base}/models unreachable ({exc}); is locoder-llama.service running?")
        return
    missing = PRESETS - ids
    (fail if missing else ok)(f"presets served: {sorted(ids & PRESETS)}" + (f", missing {sorted(missing)}" if missing else ""))
    if "judge" in missing:
        return
    try:
        v = Judge(base, cfg["llama"]["judge_model"], cfg["llama"]["timeout_s"]).judge(
            "Goal: fix the typo 'recieve' in README.md.\nAcceptance: grep -q receive README.md\n"
            "Constraints: touch nothing else.")
    except JudgeError as exc:
        fail(f"judge: {exc}")
        return
    line = f"judge: P(local)={v.p_local:.2f}, difficulty≈{v.expected_difficulty:.2f}, coverage={v.coverage:.2f}"
    if v.coverage < 0.5:
        warn(line + " — low coverage: the judge model rarely answers in the allowed tokens; try another model")
    elif v.p_local < 0.5:
        warn(line + " — it doubts the coder on a one-word typo fix; its scale may need other thresholds")
    else:
        ok(line)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--offline", action="store_true")
    args = parser.parse_args()
    check_profile()
    check_plugins()
    check_tools()
    if not args.offline:
        check_llama()
    if failures:
        print(f"\n{len(failures)} check(s) failed")
        return 1
    print("\nall checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())

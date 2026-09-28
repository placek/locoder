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

import stack  # scripts/stack.py: the settings that must agree across presets, config and routing

REQUIRED_PLUGINS = {"trismegistos/routing": {"route", "escalate", "route_outcome", "routing_status", "routing_mode"},
                    "web/defuddle": {"web_research"}}

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
    missing = REQUIRED_PLUGINS["trismegistos/routing"] - coding
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
    sys.path.insert(0, str(home / "plugins" / "trismegistos"))
    from routing import settings
    from routing.judge import WORKER

    found = stack.problems(_presets(), cfg, settings.load(), WORKER)
    for msg in found:
        fail(msg)
    if not found:
        ok("presets, config.yaml and routing.yaml agree (context, slots, judge, KV types, --fit, WORKER)")
    if not (home / "skills" / "delegate" / "SKILL.md").is_file():
        fail("skills/delegate/SKILL.md not found through the skills link")


def _presets() -> stack.Presets:
    path = os.environ.get("TRISMEGISTOS_PRESETS")
    if not path:
        sys.exit("TRISMEGISTOS_PRESETS is not set: run this through make check")
    return stack.parse(Path(path).read_text())


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
    if shutil.which("trismegistos"):
        ok("trismegistos on PATH (the openrouter rung runs it one-shot)")
    else:
        warn("trismegistos not on PATH: the openrouter rung cannot start (set openrouter.hermes_bin)")
    defuddle = os.environ.get("HERMES_DEFUDDLE_BIN", "")
    if defuddle and os.access(defuddle, os.X_OK):
        ok(f"defuddle: {defuddle}")
    else:
        fail(f"defuddle binary not executable: {defuddle or '(HERMES_DEFUDDLE_BIN unset)'}")
    want = os.environ.get("TRISMEGISTOS_GRAFT_VERSION", "")
    graft = shutil.which("graft")
    if graft:
        _graft_version("host", [graft, "--version"], want)
    else:
        fail("graft not on PATH: route() cannot wire projects (make graft)")
    image = os.environ.get("TRISMEGISTOS_SANDBOX_IMAGE", "trismegistos-sandbox:local")
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
    sys.path.insert(0, str(Path(os.environ["HERMES_HOME"]) / "plugins" / "trismegistos"))
    from routing import settings
    from routing.judge import Judge, JudgeError, SemIfBackend

    cfg = settings.load()
    base = cfg["llama"]["base_url"].rstrip("/")
    presets = _presets()
    wanted = set(stack.models(presets))
    try:
        with urllib.request.urlopen(base + "/models", timeout=10) as resp:
            served = {m.get("id"): m for m in json.loads(resp.read()).get("data", [])}
    except OSError as exc:
        fail(f"{base}/models unreachable ({exc}); is trismegistos-llama.service running?")
        return
    missing = wanted - served.keys()
    (fail if missing else ok)(f"presets served: {sorted(wanted & served.keys())}"
                              + (f", missing {sorted(missing)}" if missing else ""))
    for name in sorted(wanted & served.keys()):
        status = served[name].get("status") or {}
        state = status.get("value")
        if status.get("failed"):
            fail(f"{name}: failed to load (exit code {status.get('exit_code')}): make logs shows why")
        elif state == "loading":
            warn(f"{name}: still loading; run make check again when it is up")
        elif state not in (None, "loaded", "sleeping"):
            fail(f"{name}: {state}, though every preset loads on startup: make logs shows why")
        if "preset" in status:
            ignored = stack.ignored_keys(stack.effective(presets, name), status["preset"])
            (fail if ignored else ok)(f"{name}: " + (f"the router ignored {ignored} (a typo, an option this "
                                                    "llama.cpp lacks, or an alias: use the long form)"
                                                    if ignored else "every preset key was accepted"))
    _check_context(base)
    _check_vram()
    if "semif" not in (cfg["judge"]["backend"], cfg["judge"]["shadow"]):
        ok("the SemIf judge is not asked (judge.backend and judge.shadow)")
        return
    if cfg["llama"]["judge_model"] in missing:
        return
    try:
        v = Judge(SemIfBackend(base, cfg["llama"]["judge_model"], cfg["llama"]["timeout_s"])).judge(
            "Goal: fix the typo 'recieve' in README.md.\nAcceptance: grep -q receive README.md\n"
            "Constraints: touch nothing else.")
    except JudgeError as exc:
        fail(f"judge: {exc}")
        return
    line = f"judge: P(local)={v.p_local:.2f}, difficulty≈{v.expected_difficulty:.2f}, coverage={v.coverage:.2f}"
    if v.coverage < float(cfg["policy"]["min_coverage"]):
        warn(line + " — below policy.min_coverage: route() would ignore this verdict; try another judge model")
    elif v.p_local < 0.5:
        warn(line + " — it doubts the coder on a one-word typo fix; its scale may need other thresholds")
    else:
        ok(line)


def _check_context(base: str) -> None:
    """The context the orchestrator really got, against the one Hermes will fill."""
    from hermes_yaml import safe_load

    config = safe_load((Path(os.environ["HERMES_HOME"]) / "config.yaml").read_text())
    name, want = config["model"]["default"], int(config["model"]["context_length"])
    root = base[:-3] if base.endswith("/v1") else base
    try:
        with urllib.request.urlopen(f"{root}/props?model={name}", timeout=30) as resp:
            got = json.loads(resp.read()).get("default_generation_settings", {}).get("n_ctx")
    except OSError as exc:
        fail(f"{name}: /props unreachable ({exc})")
        return
    (ok if got == want else fail)(f"{name}: slot context {got}, config.yaml context_length {want}")


def _check_vram() -> None:
    """--fit keeps fit-target MiB free at load; much less now means something else took it."""
    smi = shutil.which("nvidia-smi")
    if not smi:
        warn("nvidia-smi not on PATH: VRAM headroom not checked")
        return
    out = subprocess.run([smi, "--query-gpu=memory.used,memory.total", "--format=csv,noheader,nounits"],
                         capture_output=True, text=True, timeout=30)
    try:
        used, total = (int(x) for x in out.stdout.splitlines()[0].split(","))
    except (ValueError, IndexError):
        warn(f"nvidia-smi gave no memory reading: {out.stderr.strip()[:200]}")
        return
    free = total - used
    line = f"VRAM: {used} of {total} MiB used, {free} free"
    (warn if free < 1024 else ok)(line + (" — under 1 GiB: a desktop spike can OOM the orchestrator" if free < 1024
                                          else ""))


def check_julia() -> None:
    """Julia-1's service. Routing on it, a stopped container means every task starts on Claude
    Code; as the shadow, route() survives it but the comparison it is there for gets a gap."""
    print("julia-1")
    sys.path.insert(0, str(Path(os.environ["HERMES_HOME"]) / "plugins" / "trismegistos"))
    from routing import settings
    from routing.judge import JudgeError
    from routing.tools import make_judge

    cfg = settings.load()
    role = ("routes" if cfg["judge"]["backend"] == "julia"
            else "shadow" if cfg["judge"]["shadow"] == "julia" else None)
    if role is None:
        ok("not asked (judge.backend and judge.shadow)")
        return
    base = cfg["julia"]["base_url"].rstrip("/")
    try:
        with urllib.request.urlopen(base + "/health", timeout=10) as resp:
            info = json.loads(resp.read())
    except OSError as exc:
        fail(f"{base}/health unreachable ({exc}): make julia && systemctl --user start trismegistos-julia, "
             "or stop asking it (judge.backend / judge.shadow)")
        return
    ok(f"{info.get('repo')}@{str(info.get('commit'))[:12]}, weights {str(info.get('weights_sha256'))[:12]}…, "
       f"max_length {info.get('max_length')}, {info.get('threads')} threads")
    try:
        v = make_judge(cfg, "julia").judge("Goal: fix the typo 'recieve' in README.md.\nAcceptance: grep -q receive README.md\n"
                                    "Constraints: touch nothing else.")
    except JudgeError as exc:
        fail(f"julia-1 ({role}): {exc}")
        return
    ok(f"julia-1 ({role}): P(local)={v.p_local:.2f}, difficulty≈{v.expected_difficulty:.2f} on a one-word typo fix")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--offline", action="store_true")
    args = parser.parse_args()
    check_profile()
    check_plugins()
    check_tools()
    if not args.offline:
        check_llama()
        check_julia()
    if failures:
        print(f"\n{len(failures)} check(s) failed")
        return 1
    print("\nall checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())

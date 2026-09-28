"""Wire graft into a project the first time a task there is routed.

`graft init --agents agents claude` writes a fenced section into AGENTS.md (Hermes loads
the AGENTS.md chain and follows it with the graft CLI in the sandbox) and .mcp.json,
.claude/ hooks and a skill (Claude Code). `graft build` adds the structural graph, a local
cache it keeps out of git. Only graft's own files are committed; anything that could mix
graft's changes with the user's uncommitted work makes this skip instead.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Dict, List, Optional, Set

# Everything graft init + build may touch at the repo root, as git pathspecs.
PATHSPECS = ("AGENTS.md", ".mcp.json", ".gitignore", ".ignore", ".claude/settings.json",
             ".claude/helpers", ".claude/skills/graft")
MARKER = "<!-- graft:start -->"

COMMIT_MESSAGE = """Wire in graft

trismegistos's routing wired graft into this repository the first time it
delegated a task here: an AGENTS.md section that points Hermes at the
graft CLI, and an MCP server, hooks and skill for Claude Code. The graph
itself (graft/) is a local, regenerable cache and stays out of git.
"""


class _Failed(RuntimeError):
    pass


def _git(root: Path, *argv: str) -> str:
    return subprocess.run(["git", "-C", str(root), *argv], capture_output=True, text=True,
                          timeout=60, check=True).stdout


def _changes(root: Path) -> List[str]:
    """Paths under graft's pathspecs that differ from HEAD, untracked files included."""
    out = _git(root, "status", "--porcelain", "-uall", "--", *PATHSPECS)
    return [line[3:].split(" -> ")[-1].strip('"') for line in out.splitlines() if line.strip()]


def is_wired(root: Path) -> bool:
    try:
        agents = (root / "AGENTS.md").read_text(encoding="utf-8")
        mcp = json.loads((root / ".mcp.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    return MARKER in agents and "graft" in (mcp.get("mcpServers") or {})


def _operation_in_progress(root: Path) -> Optional[str]:
    for name, what in (("MERGE_HEAD", "a merge"), ("rebase-merge", "a rebase"), ("rebase-apply", "a rebase"),
                       ("CHERRY_PICK_HEAD", "a cherry-pick")):
        path = Path(_git(root, "rev-parse", "--git-path", name).strip())
        if (path if path.is_absolute() else root / path).exists():
            return what
    return None


def ensure(workdir: Optional[str], cfg: dict, failed: Set[str],
           base_env: Optional[Dict[str, str]] = None) -> Dict[str, str]:
    """Make sure the repo holding *workdir* is wired; returns what happened, for route()'s output."""
    g = cfg["graft"]
    if not g.get("enabled", True):
        return {"status": "off"}
    if not workdir:
        return {"status": "skipped", "reason": "no workdir"}
    try:
        root = Path(_git(Path(workdir), "rev-parse", "--show-toplevel").strip()).resolve()
    except (OSError, subprocess.SubprocessError):
        return {"status": "skipped", "reason": "not a git checkout"}
    roots = [Path(r).expanduser().resolve() for r in cfg["claude"]["workdir_roots"]]
    if not any(root == r or r in root.parents for r in roots):
        return {"status": "skipped", "reason": f"{root} is outside claude.workdir_roots"}
    if is_wired(root):
        return {"status": "already", "root": str(root)}
    if str(root) in failed:
        return {"status": "skipped", "reason": "wiring failed earlier in this Hermes process"}
    try:
        operation = _operation_in_progress(root)
        if operation:
            return {"status": "skipped", "reason": f"{operation} is in progress"}
        dirty = _changes(root)
    except (OSError, subprocess.SubprocessError) as exc:
        return {"status": "skipped", "reason": f"git status failed: {exc}"}
    if dirty:
        return {"status": "skipped", "reason": "uncommitted changes in files graft writes: " + ", ".join(dirty[:5])}

    had_cache = (root / "graft").exists()
    env = dict(base_env if base_env is not None else os.environ, DO_NOT_TRACK="1", GRAFT_NO_STATUSLINE="1")
    try:
        _run(g, root, env, "init", "--no-global", "--no-statusline", "--no-build", "--agents", "agents", "claude")
        _run(g, root, env, "build")
        changed = _changes(root)
        if not changed:
            raise _Failed("graft init changed nothing")
        _git(root, "add", "--", *changed)
        _git(root, "commit", "--quiet", "--only", "-m", COMMIT_MESSAGE, "--", *changed)
        commit = _git(root, "rev-parse", "HEAD").strip()
    except (_Failed, OSError, subprocess.SubprocessError) as exc:
        failed.add(str(root))
        _roll_back(root, had_cache)
        detail = getattr(exc, "stderr", None) or str(exc)
        return {"status": "failed", "reason": str(detail).strip()[-500:], "root": str(root)}
    return {"status": "wired", "root": str(root), "commit": commit, "files": ", ".join(changed),
            "note": ("AGENTS.md now points agents at the graft CLI. Delegated runs start fresh and follow "
                     "it now; this session's own system prompt picks it up from its next start.")}


def _run(g: dict, root: Path, env: Dict[str, str], *argv: str) -> None:
    try:
        subprocess.run([g["bin"], *argv], cwd=str(root), env=env, capture_output=True, text=True,
                       timeout=float(g["timeout_s"]), check=True)
    except FileNotFoundError as exc:
        raise _Failed(f"graft not found: {g['bin']} (make graft)") from exc
    except subprocess.CalledProcessError as exc:
        raise _Failed(f"graft {argv[0]} failed: {(exc.stderr or exc.stdout or '').strip()[-400:]}") from exc


def _roll_back(root: Path, had_cache: bool) -> None:
    """Undo whatever graft left under its pathspecs; they were clean before it ran."""
    try:
        _git(root, "reset", "--quiet", "--", *PATHSPECS)
        tracked = [p for p in _changes(root) if _git(root, "ls-files", "--", p).strip()]
        if tracked:
            _git(root, "checkout", "--", *tracked)
        for p in _changes(root):
            target = root / p
            if target.is_file() or target.is_symlink():
                target.unlink()
        if not had_cache and (root / "graft").is_dir():
            shutil.rmtree(root / "graft", ignore_errors=True)
    except (OSError, subprocess.SubprocessError):
        pass

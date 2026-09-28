#!/usr/bin/env python3
"""Pick the OpenRouter model: replay past delegated tasks on candidate models.

    make bakeoff MODELS=deepseek/deepseek-v4.1-flash,qwen/qwen3.8-flash [TASKS=10]

Takes the latest routed tasks that recorded a clean commit and whose brief names an
acceptance command. For each task and model: a throwaway git worktree at that commit, the
brief run through the OpenRouter rung (a one-shot Hermes run) on the model, then the
acceptance command. Changes outside the brief's "touch nothing outside" paths fail the task,
as in /verify. Reads the ledger read-only; never touches the real checkouts.

A fresh worktree has no untracked build state (virtualenvs, node_modules), so a check that
needs one fails for every model alike: compare models against each other, not against 100%.
"""
from __future__ import annotations

import argparse
import asyncio
import copy
import re
import sqlite3
import subprocess
import sys
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "plugins" / "trismegistos"))
from routing import backends, settings  # noqa: E402

_BACKTICKED = re.compile(r"`([^`]+)`")


@dataclass
class Task:
    decision_id: str
    brief: str
    repo: Path
    commit: str
    check: str
    allowed: List[str]


@dataclass
class Score:
    passed: int = 0
    tasks: int = 0
    cost: float = 0.0
    seconds: float = 0.0
    failures: List[str] = field(default_factory=list)


def acceptance_command(brief: str) -> Optional[str]:
    for line in brief.splitlines():
        if line.strip().lower().startswith("acceptance:"):
            rest = line.split(":", 1)[1]
            quoted = _BACKTICKED.search(rest)
            command = (quoted.group(1) if quoted else rest).strip()
            return command or None
    return None


_NOT_PATHS = {"and", "or", "the", "these", "files", "paths"}


def allowed_paths(brief: str) -> List[str]:
    """Paths after "touch nothing outside", backticked or as plain words up to the clause's end."""
    marker = "touch nothing outside"
    for line in brief.splitlines():
        low = line.lower()
        if marker not in low:
            continue
        tail = line[low.index(marker) + len(marker):]
        words = _BACKTICKED.findall(tail) or re.split(r"[,\s]+", re.split(r";|\.(?=\s|$)", tail)[0])
        return [w.strip("<>").rstrip("/") for w in (w.strip() for w in words)
                if w and w.strip("<>").lower() not in _NOT_PATHS]
    return []


def outside(changed: List[str], allowed: List[str]) -> List[str]:
    if not allowed:
        return []
    return [f for f in changed if not any(f == a or f.startswith(a + "/") for a in allowed)]


def select(db: sqlite3.Connection, limit: int, include_dirty: bool) -> tuple[List[Task], Dict[str, int]]:
    skipped = {"no commit": 0, "dirty tree": 0, "no acceptance command": 0, "repo missing": 0}
    tasks: List[Task] = []
    for row in db.execute("SELECT id, brief, workdir, commit_sha, dirty FROM decisions ORDER BY ts DESC"):
        if len(tasks) >= limit:
            break
        if not row["commit_sha"] or not row["workdir"]:
            skipped["no commit"] += 1
            continue
        if row["dirty"] and not include_dirty:
            skipped["dirty tree"] += 1
            continue
        check = acceptance_command(row["brief"])
        if not check:
            skipped["no acceptance command"] += 1
            continue
        if not Path(row["workdir"]).is_dir():
            skipped["repo missing"] += 1
            continue
        tasks.append(Task(row["id"], row["brief"], Path(row["workdir"]), row["commit_sha"], check,
                          allowed_paths(row["brief"])))
    return tasks, skipped


def _git(repo: Path, *argv: str) -> str:
    return subprocess.run(["git", "-C", str(repo), *argv], capture_output=True, text=True, check=True).stdout


def changed_files(worktree: Path) -> List[str]:
    return [line[3:].split(" -> ")[-1] for line in _git(worktree, "status", "--porcelain", "-uall").splitlines()]


def run_check(command: str, worktree: Path, where: str, image: str, timeout: int) -> bool:
    if where == "host":
        argv = ["sh", "-c", command]
    else:
        ids = f"{subprocess.check_output(['id', '-u'], text=True).strip()}:" \
              f"{subprocess.check_output(['id', '-g'], text=True).strip()}"
        argv = ["docker", "run", "--rm", "--user", ids, "-v", f"{worktree}:{worktree}", "-w", str(worktree),
                image, "sh", "-c", command]
    try:
        return subprocess.run(argv, cwd=worktree, capture_output=True, timeout=timeout).returncode == 0
    except subprocess.TimeoutExpired:
        return False


def replay(task: Task, model: str, cfg: dict, scratch: Path, args) -> tuple[bool, float, float, str]:
    worktree = scratch / f"{task.decision_id}-{uuid.uuid4().hex[:6]}"
    _git(task.repo, "worktree", "add", "--detach", "--quiet", str(worktree), task.commit)
    try:
        run_cfg = copy.deepcopy(cfg)
        run_cfg["openrouter"]["model"] = model
        started = time.monotonic()
        result = asyncio.run(backends.run(run_cfg, "openrouter", task.brief, worktree))
        seconds = time.monotonic() - started
        if not result.ok:
            return False, result.cost, seconds, f"run failed (exit {result.exit_code})"
        stray = outside(changed_files(worktree), task.allowed)
        if stray:
            return False, result.cost, seconds, f"touched {', '.join(stray[:3])}"
        if not run_check(task.check, worktree, args.check_in, args.image, args.check_timeout):
            return False, result.cost, seconds, "acceptance check failed"
        return True, result.cost, seconds, ""
    finally:
        _git(task.repo, "worktree", "remove", "--force", str(worktree))


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--models", required=True, help="comma-separated OpenRouter model ids (2-3)")
    ap.add_argument("--tasks", type=int, default=10)
    ap.add_argument("--ledger", type=Path, default=None)
    ap.add_argument("--scratch", type=Path, default=None,
                    help="where worktrees go (default: <first workdir root>/.trismegistos-bakeoff, inside the sandbox mount)")
    ap.add_argument("--include-dirty", action="store_true",
                    help="also replay tasks routed from a dirty tree (their uncommitted files are missing)")
    ap.add_argument("--check-in", choices=("sandbox", "host"), default="sandbox")
    ap.add_argument("--image", default="trismegistos-sandbox:local")
    ap.add_argument("--check-timeout", type=int, default=900)
    args = ap.parse_args(argv)

    models = [m.strip() for m in args.models.split(",") if m.strip()]
    cfg = settings.load()
    ledger = args.ledger or settings.ledger_path()
    if not ledger.exists():
        print(f"no ledger at {ledger}", file=sys.stderr)
        return 1
    db = sqlite3.connect(f"file:{ledger}?mode=ro", uri=True)
    db.row_factory = sqlite3.Row
    tasks, skipped = select(db, args.tasks, args.include_dirty)
    db.close()
    skips = ", ".join(f"{n} {why}" for why, n in skipped.items() if n) or "none"
    print(f"{len(tasks)} tasks to replay; skipped: {skips}\n")
    if not tasks:
        return 1

    scratch = args.scratch or Path(cfg["claude"]["workdir_roots"][0]) / ".trismegistos-bakeoff"
    scratch.mkdir(parents=True, exist_ok=True)
    scores = {m: Score() for m in models}
    for task in tasks:
        for model in models:
            ok, cost, seconds, why = replay(task, model, cfg, scratch, args)
            s = scores[model]
            s.tasks += 1
            s.passed += ok
            s.cost += cost
            s.seconds += seconds
            if not ok:
                s.failures.append(f"{task.decision_id}: {why}")
            print(f"  {task.decision_id}  {model:<40} {'pass' if ok else 'FAIL'}  {cost:7.3f}  {seconds:6.0f}s  {why}")

    print("\nmodel                                     passed   cost total  cost/task   time")
    for model, s in scores.items():
        print(f"{model:<40} {s.passed:>3}/{s.tasks:<3} {s.cost:>11.3f} {s.cost / s.tasks:>10.3f} {s.seconds:>6.0f}s")
    print("\nSet the winner as openrouter.model in profile/routing.yaml.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

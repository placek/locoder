import hashlib
import importlib.util
import stat
import subprocess
import sys
from pathlib import Path

import pytest

from routing.ledger import Ledger

spec = importlib.util.spec_from_file_location("bakeoff", Path(__file__).resolve().parents[1] / "scripts" / "bakeoff.py")
bakeoff = importlib.util.module_from_spec(spec)
sys.modules["bakeoff"] = bakeoff  # dataclasses look their module up here
spec.loader.exec_module(bakeoff)

BRIEF = """Goal: create done.txt
Where: the repo root
Acceptance: `test -f done.txt`
Constraints: touch nothing outside `done.txt`; no refactors.
"""

# Stands in for `locoder -z`: what it does depends on the model it is given.
FAKE_HERMES = """#!/usr/bin/env python3
import json, os, sys
argv = sys.argv[1:]
model, workdir = argv[argv.index("-m") + 1], argv[argv.index("--in") + 1]
json.dump({"estimated_cost_usd": 0.1, "cost_status": "estimated"}, open(argv[argv.index("--usage-file") + 1], "w"))
if model != "lazy":
    open(os.path.join(workdir, "done.txt"), "w").write("done")
if model == "sprawl":
    open(os.path.join(workdir, "other.txt"), "w").write("oops")
print("done")
"""


def git(repo, *argv):
    return subprocess.run(["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@t", *argv],
                          check=True, capture_output=True, text=True).stdout


def test_brief_parsing():
    assert bakeoff.acceptance_command(BRIEF) == "test -f done.txt"
    assert bakeoff.acceptance_command("Acceptance: make test\n") == "make test"
    assert bakeoff.acceptance_command("Goal: x\n") is None
    assert bakeoff.allowed_paths(BRIEF) == ["done.txt"]
    plain = "Constraints: touch nothing outside src/app.py, tests/ and docs/api.md; no refactors.\n"
    assert bakeoff.allowed_paths(plain) == ["src/app.py", "tests", "docs/api.md"]
    assert bakeoff.allowed_paths("Constraints: touch nothing outside src.\n") == ["src"]
    assert bakeoff.outside(["done.txt", "src/a.py"], ["done.txt"]) == ["src/a.py"]
    assert bakeoff.outside(["src/a.py"], ["src"]) == []
    assert bakeoff.outside(["anything"], []) == []


@pytest.fixture
def world(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-q")
    (repo / "README").write_text("r")
    git(repo, "add", "README")
    git(repo, "commit", "-q", "-m", "r")
    sha = git(repo, "rev-parse", "HEAD").strip()

    hermes = tmp_path / "locoder"
    hermes.write_text(FAKE_HERMES)
    hermes.chmod(hermes.stat().st_mode | stat.S_IEXEC)
    routing_yaml = tmp_path / "routing.yaml"
    routing_yaml.write_text(f"openrouter:\n  hermes_bin: {hermes}\nclaude:\n  workdir_roots: [{tmp_path}]\n")
    monkeypatch.setenv("LOCODER_ROUTING_CONFIG", str(routing_yaml))

    led = Ledger(tmp_path / "ledger.db")
    led.add_decision(BRIEF, str(repo), "claude", "r", None, None, commit_sha=sha, dirty=False)
    led.add_decision(BRIEF, str(repo), "claude", "r", None, None, commit_sha=sha, dirty=True)
    led.add_decision("Goal: no check\n", str(repo), "claude", "r", None, None, commit_sha=sha, dirty=False)
    led.add_decision(BRIEF, None, "claude", "r", None, None)
    led.close()
    return {"repo": repo, "ledger": tmp_path / "ledger.db", "scratch": tmp_path / "scratch"}


def test_bakeoff_scores_each_model_and_leaves_no_trace(world, capsys):
    before = hashlib.sha256(world["ledger"].read_bytes()).hexdigest()
    code = bakeoff.main(["--models", "good,lazy,sprawl", "--ledger", str(world["ledger"]),
                         "--scratch", str(world["scratch"]), "--check-in", "host"])
    out = capsys.readouterr().out
    assert code == 0
    assert "1 tasks to replay; skipped: 1 no commit, 1 dirty tree, 1 no acceptance command" in out
    table = {line.split()[0]: line.split()[1] for line in out.splitlines()
             if line.split() and line.split()[0] in ("good", "lazy", "sprawl") and "/" in line.split()[1]}
    assert table == {"good": "1/1", "lazy": "0/1", "sprawl": "0/1"}
    good = next(line.split() for line in out.splitlines() if line.startswith("good ") and "/" in line)
    assert good[2:4] == ["0.100", "0.100"] and good[4].endswith("s")   # total cost, cost per task, time
    assert "acceptance check failed" in out and "touched other.txt" in out
    assert hashlib.sha256(world["ledger"].read_bytes()).hexdigest() == before
    assert list(world["scratch"].iterdir()) == []
    assert len(git(world["repo"], "worktree", "list").splitlines()) == 1
    assert not (world["repo"] / "done.txt").exists()

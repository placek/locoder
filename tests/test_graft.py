import json
import stat
import subprocess

import pytest

from routing import graft, settings
from routing.ledger import Ledger
from routing.tools import Router

# Stands in for the real graft: `init` writes what graft 0.20 writes with our flags,
# `build` adds the cache and its .gitignore entry. FAKE_GRAFT_FAIL names a step to fail.
FAKE_GRAFT = """#!/usr/bin/env python3
import json, os, sys
step = sys.argv[1]
open(os.environ["FAKE_GRAFT_LOG"], "a").write(json.dumps({"argv": sys.argv[1:], "dnt": os.environ.get("DO_NOT_TRACK")}) + "\\n")
if os.environ.get("FAKE_GRAFT_FAIL") == step:
    sys.stderr.write(step + " exploded\\n")
    sys.exit(1)
if step == "init":
    old = open("AGENTS.md").read() if os.path.exists("AGENTS.md") else ""
    open("AGENTS.md", "w").write(old + "\\n<!-- graft:start -->\\n## Graft\\n<!-- graft:end -->\\n")
    json.dump({"mcpServers": {"graft": {"command": "graft", "args": ["mcp"]}}}, open(".mcp.json", "w"))
    os.makedirs(".claude/helpers", exist_ok=True)
    os.makedirs(".claude/skills/graft", exist_ok=True)
    open(".claude/settings.json", "w").write("{}")
    open(".claude/helpers/graft-hooks.cjs", "w").write("//")
    open(".claude/skills/graft/SKILL.md", "w").write("graft")
    os.makedirs("graft", exist_ok=True)
elif step == "build":
    open(".gitignore", "a").write("/graft/\\n")
    open(".ignore", "w").write("graft/\\n")
    open("graft/INDEX.md", "w").write("index")
"""


def git(repo, *argv):
    return subprocess.run(["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@t", *argv],
                          check=True, capture_output=True, text=True).stdout


@pytest.fixture
def world(tmp_path, monkeypatch):
    fake = tmp_path / "graft-bin"
    fake.write_text(FAKE_GRAFT)
    fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
    log = tmp_path / "graft.log"
    monkeypatch.setenv("FAKE_GRAFT_LOG", str(log))
    monkeypatch.setenv("GIT_AUTHOR_NAME", "t")
    monkeypatch.setenv("GIT_AUTHOR_EMAIL", "t@t")
    monkeypatch.setenv("GIT_COMMITTER_NAME", "t")
    monkeypatch.setenv("GIT_COMMITTER_EMAIL", "t@t")
    projects = tmp_path / "projects"
    repo = projects / "app"
    repo.mkdir(parents=True)
    git(repo, "init", "-q")
    (repo / "AGENTS.md").write_text("# app\n")
    (repo / "main.py").write_text("print(1)\n")
    (repo / "lib.py").write_text("x = 1\n")
    git(repo, "add", ".")
    git(repo, "commit", "-q", "-m", "init")
    cfg = settings._merge(settings.DEFAULTS, {"graft": {"bin": str(fake)},
                                              "claude": {"workdir_roots": [str(projects)]}})
    return {"repo": repo, "cfg": cfg, "log": log, "projects": projects}


def log_lines(world):
    return [json.loads(line) for line in world["log"].read_text().splitlines()] if world["log"].exists() else []


def test_wires_and_commits_only_graft_files(world):
    repo = world["repo"]
    (repo / "main.py").write_text("print(2)\n")           # the user's unstaged work
    (repo / "lib.py").write_text("x = 2\n")
    git(repo, "add", "lib.py")                            # and their staged work
    r = graft.ensure(str(repo), world["cfg"], set())
    assert r["status"] == "wired"
    committed = set(git(repo, "show", "--name-only", "--format=", "HEAD").split())
    assert committed == {"AGENTS.md", ".mcp.json", ".gitignore", ".ignore", ".claude/settings.json",
                         ".claude/helpers/graft-hooks.cjs", ".claude/skills/graft/SKILL.md"}
    assert r["commit"] == git(repo, "rev-parse", "HEAD").strip()
    assert git(repo, "log", "-1", "--format=%s").strip() == "Wire in graft"
    status = git(repo, "status", "--porcelain")
    assert " M main.py" in status and "M  lib.py" in status and "graft" not in status
    assert [l["argv"][0] for l in log_lines(world)] == ["init", "build"]
    assert log_lines(world)[0]["argv"] == ["init", "--no-global", "--no-statusline", "--no-build",
                                           "--agents", "agents", "claude"]
    assert all(l["dnt"] == "1" for l in log_lines(world))


def test_already_wired_repo_is_left_alone(world):
    graft.ensure(str(world["repo"]), world["cfg"], set())
    head = git(world["repo"], "rev-parse", "HEAD")
    assert graft.ensure(str(world["repo"] / "sub"), world["cfg"], set())["status"] in ("already", "skipped")
    assert graft.ensure(str(world["repo"]), world["cfg"], set())["status"] == "already"
    assert git(world["repo"], "rev-parse", "HEAD") == head
    assert len(log_lines(world)) == 2


def test_uncommitted_changes_in_graft_files_skip_without_writing(world):
    (world["repo"] / "AGENTS.md").write_text("# app, edited\n")
    r = graft.ensure(str(world["repo"]), world["cfg"], set())
    assert r["status"] == "skipped" and "AGENTS.md" in r["reason"]
    assert not (world["repo"] / ".mcp.json").exists() and log_lines(world) == []


def test_not_git_or_outside_roots_is_skipped(world, tmp_path):
    plain = world["projects"] / "plain"
    plain.mkdir()
    assert graft.ensure(str(plain), world["cfg"], set())["reason"] == "not a git checkout"
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    git(outside, "init", "-q")
    assert "outside claude.workdir_roots" in graft.ensure(str(outside), world["cfg"], set())["reason"]
    assert graft.ensure(None, world["cfg"], set())["status"] == "skipped"


@pytest.mark.parametrize("step", ["init", "build"])
def test_a_failed_step_rolls_back_and_is_not_retried(world, monkeypatch, step):
    repo = world["repo"]
    (repo / "main.py").write_text("print(2)\n")
    before = git(repo, "status", "--porcelain", "-uall")
    monkeypatch.setenv("FAKE_GRAFT_FAIL", step)
    failed = set()
    r = graft.ensure(str(repo), world["cfg"], failed)
    assert r["status"] == "failed" and f"{step} exploded" in r["reason"]
    assert git(repo, "status", "--porcelain", "-uall") == before
    assert (repo / "AGENTS.md").read_text() == "# app\n" and not (repo / "graft").exists()
    assert graft.ensure(str(repo), world["cfg"], failed)["reason"] == "wiring failed earlier in this Hermes process"


def test_a_failed_commit_rolls_back(world):
    hook = world["repo"] / ".git" / "hooks" / "pre-commit"
    hook.write_text("#!/bin/sh\necho 'no commits today' >&2\nexit 1\n")
    hook.chmod(0o755)
    r = graft.ensure(str(world["repo"]), world["cfg"], set())
    assert r["status"] == "failed" and "no commits today" in r["reason"]
    assert git(world["repo"], "status", "--porcelain", "-uall") == ""


def test_disabled_does_nothing(world):
    world["cfg"]["graft"]["enabled"] = False
    assert graft.ensure(str(world["repo"]), world["cfg"], set()) == {"status": "off"}
    assert log_lines(world) == []


class _Judge:
    def judge(self, brief):
        from routing.judge import Verdict
        return Verdict(p_local=0.2, difficulty={}, expected_difficulty=2.0, coverage=1.0)


def test_route_wires_before_recording_the_commit(world, tmp_path):
    router = Router(judge_factory=lambda _c: _Judge(), ledger=Ledger(tmp_path / "l.db"),
                    config_loader=lambda: world["cfg"], conversation_root=lambda s: s,
                    shadow_factory=lambda _c: _Judge(), shadow_runner=lambda job: job())
    d = json.loads(router.route({"brief": "task", "workdir": str(world["repo"])}))
    assert d["graft"]["status"] == "wired"
    assert router.ledger.decision(d["decision_id"])["commit_sha"] == d["graft"]["commit"]
    assert router.ledger.decision(d["decision_id"])["dirty"] == 0
    again = json.loads(router.route({"brief": "task", "workdir": str(world["repo"])}))
    assert again["graft"]["status"] == "already"

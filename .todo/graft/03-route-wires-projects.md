# 03: route() wires an unwired project automatically

Status: done
Blocked by: 01

## What to build

On a delegated task in a git checkout under `claude.workdir_roots` that has no graft
wiring, `route()` runs `graft init --no-global --no-statusline --no-build --agents
agents claude` and `graft build` at the repository root, and commits only graft's files,
before recording the decision's commit. The spec's safety rules apply; the outcome
(wired, already, skipped + reason, failed + reason) is part of `route()`'s output.

## Acceptance criteria

- [x] An unwired repo is wired and committed once; the decision records the commit after it.
- [x] An already-wired repo is left alone.
- [x] Uncommitted changes in a file graft writes → skipped with the reason; nothing written.
- [x] Outside the workdir roots, or not a git repo → skipped.
- [x] A failing init, build or commit leaves the tree as it was and is not retried.
- [x] The user's other staged and unstaged changes are not in the wiring commit.
- [x] `graft.enabled: false` turns it off.

Tests: `tests/test_graft.py` (fake graft). End to end with the real graft 0.20.0 on a
scratch repo: wired and committed eight graft files, left an untracked `wip.py` alone,
second call `already`, `.mcp.json` written as `graft mcp`, `graft callers` answered.

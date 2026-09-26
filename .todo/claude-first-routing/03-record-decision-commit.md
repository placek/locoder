# 03: Record the commit each routed task starts from

Status: done
Blocked by: none

## What to build

Each routing decision records the git commit its workdir was at when `route()` was
called (and whether the tree was dirty). Together with the brief, which already
holds the acceptance command, that makes a past delegated task replayable: check out
the commit, hand the brief to a model, run the acceptance command. The OpenRouter
model bake-off (ticket 06) depends on this.

A workdir that is not a git checkout, or no workdir at all, records nothing and
routing carries on as before.

## Acceptance criteria

- [x] `route()` with a workdir inside a git checkout stores its HEAD commit and a dirty flag on the decision.
- [x] `route()` with no workdir, or a non-git one, still decides and stores no commit.
- [x] Existing ledgers gain the new columns without losing data.
- [x] `make test` passes.

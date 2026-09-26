# 06: Pick the OpenRouter model with a bake-off

Status: ready-for-agent
Blocked by: 03, 05

## What to build

A one-off script that replays past delegated tasks on 2–3 candidate OpenRouter
models and prints, per model: tasks whose acceptance command passed, total and
per-task cost, and wall time.

It takes decisions from the ledger that have a recorded commit (ticket 03), for
each one checks the commit out into a throwaway worktree, runs the brief through the
OpenRouter step (ticket 05) with the candidate model, and runs the brief's
acceptance command. Changes outside the brief's stated paths count as a failure, as
in `/verify`. It never touches the real checkouts, and it never writes to the
routing ledger. Candidates and the number of tasks (default 10) are arguments.

The winner goes into `routing.yaml` as `openrouter.model`, with the bake-off result
summarised in the commit that sets it.

## Acceptance criteria

- [ ] Running it with two candidates prints passes, cost and time per model.
- [ ] Each replay starts from the task's recorded commit in a throwaway worktree, which is removed afterwards.
- [ ] Decisions without a recorded commit, or whose brief has no acceptance command, are skipped and counted.
- [ ] The routing ledger is unchanged after a run.

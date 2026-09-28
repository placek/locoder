# 01: Replay Claude-solved tasks on the local model overnight

Status: ready-for-agent
Blocked by: none

## What to build

A command, `make replay [TASKS=n]`, and a nightly user timer. It re-runs past delegated
tasks that some rung verified, and that the local model has not run in its current
configuration, on the local model:
- in a throwaway worktree at the recorded commit;
- with the brief as a one-shot Hermes run on the local provider, with the worker preamble
  and the routing-child guard;
- checked with the brief's acceptance command in the sandbox and the stray-change rule, as
  the bake-off does.

Each result is a label in a new ledger table beside the decision. Real attempts, checkouts
and the rest of the ledger are untouched. Before each task it checks that the orchestrator's
slot is idle, and stops for the night when it is not.

`make report`'s judge comparison uses these labels together with real coder attempts, and
says how many came from each.

## Acceptance criteria

- [ ] Only tasks some rung verified, with a clean recorded commit and an acceptance command, are replayed; the rest are counted by reason.
- [ ] A task already replayed with the same local-worker fingerprint is skipped; after a preset change it is eligible again.
- [ ] Each replay writes one label with the fingerprint, verified bit, duration and notes; no real attempt is added or changed.
- [ ] Worktrees are removed afterwards, including after a failed or interrupted run; real checkouts are unchanged.
- [ ] A busy orchestrator slot stops the run before the next task.
- [ ] `make report` scores the judges on real and replay labels together and states both counts.
- [ ] A user timer runs it nightly with a task budget; `make enable` / `make disable` include the timer.

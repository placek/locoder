---
name: delegate
description: Route, run, verify, record and escalate any implementation work bigger than a surgical few-line edit, across the local coder subagent, Claude Code and OpenRouter. The only path by which code gets written by someone other than you.
---

# Delegate

You keep the plan and the gates; someone else types the code. Where it goes is not a feeling. `route()` asks a local judge model how hard the brief is and whether the local coder can finish it, checks whether a limit has Claude Code locked out, and returns a starting rung plus a chain. You follow the chain. Every attempt gets verified by you and recorded.

Claude Code is the default. The local coder goes first only when the judge is near-certain it will finish, or when Claude Code is locked out and the coder has a fair chance. OpenRouter is the last resort.

| rung | how | cost |
|---|---|---|
| `coder` | `delegate_task`: a child on your own local model, with a fresh context | time only |
| `claude` | `escalate(backend="claude")` — Claude Code on the Max plan | Max limits |
| `openrouter` | `escalate(backend="openrouter")` — a one-shot run of this Hermes profile on a cheaper OpenRouter model | money |

## 1. Write the brief

One brief, reused verbatim for every rung, so attempts are comparable. It must stand alone — the receiver has none of this conversation:

```
Goal: <one sentence, the behaviour that must exist afterwards>
Where: <files / module / seam; what to read first>
Acceptance: <the exact command that must pass, e.g. `pytest tests/test_x.py -q`>
Constraints: touch nothing outside <paths>; no refactors; no new dependencies unless named here.
Context: <only what the receiver cannot find by reading the code>
```

No acceptance command means the task is not ready to delegate. Write the failing test first (`/tdd`), then delegate making it pass.

## 2. Route it

Call `route(brief, workdir)` once. Keep the `decision_id` and the `chain`. Read the `reason` — if it is obviously wrong (say, a one-line typo routed to a paid rung), note that in `route_outcome` later rather than overriding the chain.

`route()` follows the session's routing mode (`/routing-mode`): in `claude` the chain is `claude, claude` (its one retry), in `local` it is `coder` alone with no retry, and `escalate()` refuses the rungs a mode rules out. A `rung` of `user` means nothing may run the task now (Claude Code-only mode while it is locked out): tell the user the `reason` and stop.

## 3. Run the current rung

- **coder** — `delegate_task(tasks=[{"goal": <brief>}])`. One child at a time: it runs on your own model, in its single slot, while you wait. It sees only the brief, never this conversation.
- **claude / openrouter** — `escalate(brief, workdir, decision_id, backend=<rung>)`. It blocks until the run ends. On `claude`, if a Max limit hits mid-run, the result carries `claude_unavailable_until` and a new `chain`: the rest of this task's route with Claude Code locked out. Park whatever the run left behind (step 5.1), record nothing for it, and continue with that `chain` instead of the one `route()` returned. An empty `chain` (Claude Code-only mode) means tell the user when it resets and stop.

## 4. Verify and record — every attempt

1. Run the acceptance command yourself. Read the output. (`/verify` rules apply: the receiver's "done" is a claim.)
2. Look at `git diff --stat`: changes outside the stated paths are a failure, even if the check passes.
3. `route_outcome(decision_id, rung, verified=<true only if both held>, notes=<one line on why not>)`.

Skipping the record is the one mistake here that compounds: these labels are what the routing thresholds get tuned on.

## 5. On failure, move down the chain

1. Park the failed attempt instead of building on it: `git stash push -m "delegate <decision_id> <rung>"`. Nothing is lost, and the next rung starts from the same clean base.
2. Append to the brief's Context what the previous rung tried and the failing check output, trimmed to the lines that show the failure. Nothing more — not its reasoning, not its diff.
3. Run the next rung in `chain`. Same verify-and-record step.

A rung listed twice (`claude, claude`) is one retry on that rung: the second attempt differs only by the failure in its Context.

After the last rung in the chain, stop. Report to the user what each rung did, the failing output, and the stash names. Do not loop, do not hand-patch the result into passing.

## Rules

- Never run `claude` from the terminal. `escalate()` is the only path: it records limit hits so routing avoids Claude Code while it is locked out, hands back the rest of the chain when one hits, and writes the ledger.
- Never pick a rung yourself because a task "feels big". If you disagree with `route()`, say so in `route_outcome` notes; the thresholds are tuned from the ledger, and an override leaves no trace there.
- Never delegate `/ship`, an unscoped "make it work", or the decision that something is done.
- `routing_status` shows whether Claude Code is available (or locked out, and until when) and this week's per-rung results, when the user asks how things stand.

# Claude-first routing

Status: implemented (see coverage.md)

Hermes stays the one harness. Claude Code becomes the default worker for delegated
tasks; local models take only what they are near-certain to finish, and pick up the
slack when Claude Code is out. OpenRouter is the last resort.

This replaces the current policy, which starts tasks on the local coder whenever the
judge is optimistic, rations Claude Code across the week with a pacer, and falls back
to OpenRouter (not local) when Claude Code is ahead of pace or out.

## Decisions

| # | question | decision |
|---|---|---|
| 1 | Claude Code's role | The default worker. Local models are an optimisation for simple work. |
| 2 | Rationing | None: spend Claude Code freely until a limit hits. The pacer goes. |
| 3 | Claude Code out, judge pessimistic about local | Try local only if P(local finishes) clears a lower bar; below it, OpenRouter. |
| 4 | OpenRouter spending cap | None. |
| 5 | Which limits | All of them: lock out until the reset the limit error states, not until the week ends. Detection is failure-only; no statusline or usage probing. |
| 6 | Orchestrator model | Always local. |
| 7 | Success measure | Claude Code lasts until the weekly reset (as a signal, reported, not enforced), with the verified pass rate as a floor. |
| 8 | Who does simple work | The orchestrator does mechanical work itself (commits, file moves, surgical edits). Delegated tasks go to the coder only when the judge is near-certain. |
| 9 | Claude Code fails the acceptance check | Retry once on Claude Code with the failing output, then hand back to the user. |
| 10 | OpenRouter step | A Hermes child on a cheaper non-Anthropic model, not `claude -p`. |
| 11 | Choosing that model | A bake-off replaying real past delegated tasks on 2–3 candidates. |
| 12 | Local coder | Unchanged (fast, small-active MoE). Its pass rate as a fallback is tracked separately; revisit with the orchestrator's model as a heavier fallback if it is poor. |
| 13 | Routing modes | Three: `auto` (the judged chains below), `claude` (Claude Code only), `local` (the coder only). |
| 14 | Mode scope | Per Hermes session: every session starts in `auto`; a switch lasts until that session ends. |
| 15 | Forced modes on failure | `claude`: Claude Code out means the task comes back to the user with the reset time; a verification failure still gets the one retry. `local`: a coder verification failure comes straight back to the user, no retry. Neither ever falls to another rung. |

## The chain

For a delegated task, `route()` returns one of:

| situation | chain |
|---|---|
| Claude Code available, judge near-certain (P ≥ 0.85, difficulty ≤ 0.5) | coder → claude → claude (retry) → user |
| Claude Code available, otherwise | claude → claude (retry) → user |
| Claude Code out, P ≥ 0.3 | coder → openrouter → user |
| Claude Code out, P < 0.3 | openrouter → user |
| judge unreachable | as if the judge were pessimistic: claude first while available |

A limit hit mid-run switches the rest of the task to the "Claude Code out" rows.
Thresholds are starting values in `routing.yaml`, tuned later from `make report`.

That table is `auto` mode. The forced modes ignore the judge's verdict (it is still
asked and recorded, since forced-local attempts on tasks it rated hard are exactly
the calibration data `auto` never produces):

| mode | situation | chain |
|---|---|---|
| `claude` | Claude Code available | claude → claude (retry) → user |
| `claude` | Claude Code out (or hits a limit mid-run) | user, told when it resets |
| `local` | any | coder → user |

## Open facts

- Whether Hermes at the pinned revision can run a delegated child on a model other
  than the delegation default (ticket 04).
- The exact wording of Claude Code's limit error under `claude -p`, and how it
  states the reset time. Undocumented; ticket 02 stores the raw text of every hit so
  the parser can be fixed against real errors.

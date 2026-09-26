# 08: Per-session routing modes — auto, claude, local

Status: done
Blocked by: 01, 02

## What to build

The user can switch a Hermes session between three routing modes by telling the
orchestrator (a slash skill, e.g. `/routing-mode local`):

- `auto` — the judged chains from ticket 01. Every session starts here.
- `claude` — Claude Code only: claude → one retry → user. If Claude Code is out, or
  hits a limit mid-run, the task comes back to the user with the time it resets.
  Never falls to the coder or OpenRouter.
- `local` — the local coder only: coder → user, no retry. Never a paid rung.

The mode lasts until the session ends or is switched again; a new session is back in
`auto`, even within the same Hermes process. Prefer keeping the mode in the routing
plugin keyed by the Hermes session, so it survives context compression; only if
plugin tools cannot see which session called them, fall back to the orchestrator
passing the mode on every `route()` call. At the pinned Hermes revision plugin
handlers receive `session_id` (and `task_id`) as keyword arguments
(`model_tools.py:826-838`, `tools/registry.py:893-910` in the Hermes tree), so the
plugin-side option applies.

In the forced modes the judge is still asked and its verdict recorded, but ignored.
Each decision records the mode it was made in, and `route()`'s reason says a mode
forced the rung. `routing_status` shows the session's current mode.

The skill must not share a name with a Hermes built-in command or an existing skill
(see `AGENTS.md`), gets a line in the README's skill index, and is written after
loading `writing-for-agents`. The `delegate` skill explains the modes.

## Acceptance criteria

- [x] A new session routes in `auto`; after switching to `local`, a task the judge rates hard still routes to the coder with chain coder → user.
- [x] In `claude` mode with Claude Code available, the chain is claude → claude; with Claude Code out, `route()` returns no rung to run and the reset time.
- [x] In `claude` mode, a mid-run limit hit returns the reset time and no remaining chain.
- [x] A switch in one session does not change the mode of another session, and a new session starts in `auto`.
- [x] Forced-mode decisions still store the judge's verdict and record the mode.
- [x] `routing_status` shows the current mode.
- [ ] `make test` and `make check-offline` pass; the new skill appears in `skills list`. (`make test` passes;
  `make check-offline` and `skills list` need the pinned Hermes: run them on alpha.)

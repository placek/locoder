# 01: Claude-first routing decision

Status: done
Blocked by: none

## What to build

`route()` makes Claude Code the default starting rung and returns the chains in
`spec.md`. The local coder starts a task only when the judge is near-certain; when
Claude Code is out, the coder is tried first above a lower bar and OpenRouter
otherwise. A Claude Code verification failure gets exactly one retry on Claude Code
before the chain ends with the user. When the judge is unreachable, the task starts
on Claude Code (or, if it is out, goes where a pessimistic verdict would send it).

The weekly pacer goes entirely: no spend-vs-allowance comparison, no "ahead of pace"
diversion, no learned weekly budget. Whether Claude Code is out still comes from the
recorded limit hits (ticket 02 changes how long a hit lasts). The decision snapshot
stored in the ledger records Claude Code's availability instead of the pace, so the
report can later tell fallback attempts apart. `routing_status` reports Claude Code's
availability and this week's per-rung results instead of pace.

Everything that describes the old policy says the new one: the `delegate` skill
(rungs table, the retry-once step, "if the whole chain fails, stop"), `SOUL.md`, the
README's priorities, flow diagram and tuning section, and the `routing.yaml` /
settings defaults (new thresholds; `pace_slack`, `hard_difficulty`, `weekly_budget`
removed).

## Acceptance criteria

- [x] With Claude Code available, P(local)=0.9 and difficulty 0.3 route to the coder with chain coder → claude → claude.
- [x] With Claude Code available, P(local)=0.7 (below 0.85) routes to claude with chain claude → claude.
- [x] With Claude Code out, P(local)=0.4 routes to the coder with chain coder → openrouter; P(local)=0.2 routes to openrouter alone.
- [x] With the judge unreachable, the task starts on claude while it is available.
- [x] No code, config key, tool output or doc still refers to pace, allowance or a weekly budget.
- [x] Thresholds (0.85, 0.5, 0.3) are `routing.yaml` settings, applied on the next tool call.
- [ ] `make test` and `make check-offline` pass. (`make test` passes; `make check-offline` needs the
  pinned Hermes install and has not been run yet: run it on alpha before merging.)
  Verified in a cloud container with the pinned Hermes installed: the profile and plugin
  checks of `make check-offline` pass (all five routing tools loaded and in the `coding`
  toolset); its two failures there, defuddle and the sandbox image, are that container's.

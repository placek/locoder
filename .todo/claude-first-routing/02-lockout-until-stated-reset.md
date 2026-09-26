# 02: Lock Claude Code out only until the reset its limit error states

Status: ready-for-agent
Blocked by: 01

## What to build

A Claude Code limit hit (session or weekly) makes Claude Code unavailable until the
reset time stated in the error, not until the end of the week. When no reset time
can be read from the error, Claude Code is locked out for a short configurable
period (default one hour); the next task after that simply tries again, and a still-
active limit fails fast and re-locks. Detection stays failure-only: no usage probes,
no statusline reading.

Every limit hit stores the raw error text in the ledger, so the pattern and reset
parser can be corrected against real errors (the `-p` wording is undocumented).

A limit hit mid-run no longer retries on OpenRouter inside `escalate()`. It returns
the run, when Claude Code becomes available again, and the rest of this task's chain
under the "Claude Code out" rules (coder first if the decision's stored P(local)
clears the lower bar, else OpenRouter), so the orchestrator continues from there.
The `delegate` skill says so.

## Acceptance criteria

- [ ] An error stating a reset a few hours ahead locks Claude Code out until that time and no longer; `route()` afterwards returns the "Claude Code out" chains until then, and Claude-first chains after.
- [ ] Reset times given as a clock time, a date and a relative duration are each parsed (tests use invented but plausible wordings; the real ones replace them once seen).
- [ ] An error with no readable reset locks out for the configured fallback period.
- [ ] The raw text of each limit hit is in the ledger.
- [ ] A mid-run limit hit returns the unavailable-until time and the remaining chain; it does not start an OpenRouter run by itself.
- [ ] `make test` passes.

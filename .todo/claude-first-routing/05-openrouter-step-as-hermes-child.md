# 05: Run the OpenRouter step as a Hermes child

Status: ready-for-agent
Blocked by: 01, 04

## What to build

The `openrouter` rung runs the brief as a Hermes child on a cheaper non-Anthropic
OpenRouter model, instead of `claude -p` pointed at OpenRouter. Ticket 04 found that
`delegate_task` cannot choose a model per task, so `escalate()` spawns a headless
one-shot run (`locoder -z <brief> -m <model> --provider custom:openrouter --in
<workdir> --usage-file <file> -t coding`); see ticket 04's findings for exit codes,
the usage file and the non-interactive model guard. The Claude Code rung is unchanged.

Its attempts land in the ledger like the other rungs, with cost taken from
OpenRouter's usage data, and are labelled through `route_outcome` as before. The
model is one `routing.yaml` setting (`openrouter.model`), a placeholder until
ticket 06 picks it. The `claude -p` OpenRouter path (its gateway environment and
separate Claude config directory) is removed, along with the docs that describe
"same CLI, same model family": the `delegate` skill's rungs table, the README's
flow and "both paid rungs are the same CLI" section, and `make check`'s wording.

## Acceptance criteria

- [ ] A task routed to `openrouter` runs as a Hermes child on the configured model, with its commands in the sandbox.
- [ ] The attempt's cost from OpenRouter is recorded in the ledger.
- [ ] Nothing launches `claude` against OpenRouter any more.
- [ ] `make test` and `make check-offline` pass.

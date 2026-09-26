# 05: Run the OpenRouter step as a Hermes child

Status: implemented (verify on alpha with ticket 04's smoke run)
Blocked by: 01, 04

## What to build

The `openrouter` rung runs the brief as a Hermes child on a cheaper non-Anthropic
OpenRouter model, instead of `claude -p` pointed at OpenRouter. Ticket 04 found that
`delegate_task` cannot choose a model per task, so `escalate()` spawns a headless
one-shot run (`locoder -z <brief> -m <model> --provider custom:openrouter --in
<workdir> --usage-file <file> -t file,terminal,web,todo`); see ticket 04's findings for exit codes,
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
  (Command, exit codes and usage file are covered by tests against a fake `locoder`; the
  sandbox part needs the live run on alpha.)
- [x] The attempt's cost from OpenRouter is recorded in the ledger.
- [x] Nothing launches `claude` against OpenRouter any more.
- [ ] `make test` and `make check-offline` pass. (`make test` passes; `make check-offline` on alpha.)
  Verified in a cloud container with the pinned Hermes installed: the profile and plugin
  checks of `make check-offline` pass (all five routing tools loaded and in the `coding`
  toolset); its two failures there, defuddle and the sandbox image, are that container's.

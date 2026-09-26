# 04: Spike — can Hermes run a delegated child on an OpenRouter model?

Status: ready-for-agent
Blocked by: none

## What to build

A finding, not a feature. The OpenRouter step is to be a Hermes child on a
non-Anthropic model (spec decision 10), but the profile's delegation block names a
single model and provider: the local coder.

Against the Hermes revision pinned in `hermes.rev`, find out how to run one
delegated task on the `openrouter` custom provider while the default stays the local
coder. Candidates, in order of preference: a per-task model/provider on
`delegate_task`; a second named delegation target; a headless one-shot Hermes run
spawned by the routing plugin with the same profile and the OpenRouter provider.
The child must run its commands in the sandbox, like the coder's, and its token
usage and cost must be readable afterwards.

Record the answer in this file under `## Findings` (mechanism, how cost is read,
anything that breaks under `agent.coding_context: focus`), with a working command
or call that ran a trivial task on an OpenRouter model.

## Acceptance criteria

- [ ] `## Findings` names the mechanism and shows it working on a trivial task.
- [ ] It states where the run's cost comes from.
- [ ] It states whether the child's tools stay visible under the `focus` coding context.

# 04: Spike — can Hermes run a delegated child on an OpenRouter model?

Status: findings-recorded (smoke run pending on alpha)
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
- [x] It states where the run's cost comes from.
- [x] It states whether the child's tools stay visible under the `focus` coding context.

## Findings

From the Hermes source at the pinned revision (`7b761da`); paths are in that tree.

**`delegate_task` cannot pick a model per task.** Its schema has only `tasks[]`
(`goal`, `context`, `output_schema`, `images`, `group`), `action`, `subagent_id`
and `message` (`tools/delegate_tool.py:634-730`). All children in a call share one
credential set from the `delegation:` block (`:495-498`, `:370-395`). The only
per-call override, `credentials_cfg`, is a keyword-only argument for internal Python
callers (`:444`, `:492-495`). Plugin handlers are not given the parent agent, so a
plugin cannot use it. No named delegation targets or per-agent model profiles exist.

**Mechanism: a headless one-shot Hermes run**, spawned by the routing plugin:

```
locoder -z "<brief>" -m <openrouter model> --provider custom:openrouter \
        --in <workdir> --usage-file <tmp>/usage.json -t coding
```

- The `locoder` wrapper sets `HERMES_HOME` and sources the profile `.env`, so the
  run uses this profile's config, skills and plugins. `-p` is not needed.
- Use `custom:openrouter`, not bare `openrouter`, which is a built-in provider and
  bypasses the profile's `custom_providers` entry
  (`hermes_cli/runtime_provider_custom.py:122-136`). The entry's `api_key_env` is
  honoured (`hermes_cli/config_providers.py:196-198`).
- stdout is only the final response. Exit codes: 0 completed; 2 failed, partial or
  out of iterations; 1 no text or an exception; 130 interrupted
  (`hermes_cli/oneshot.py:100-108`).
- `-m`/`--provider` without a TTY is refused (exit 1) if the model trips a selection
  guard (`hermes_cli/main.py:1154-1227`). The cost guard fires only above $20/M input
  or $100/M output (`hermes_cli/model_cost_guard.py:12-13`), so a cheaper model
  passes. A data-training-tier warning (e.g. `:free` models) needs
  `security.allow_data_training_tiers_noninteractive: true` in `config.yaml`.

**Cost.** `--usage-file` writes JSON with `estimated_cost_usd`, `cost_status`, token
counts, `model`, `provider`, `session_id` and `completed`/`partial`/`interrupted`,
even when the run fails (`hermes_cli/oneshot.py:31-35`, `:217-237`). For an
openrouter.ai host, cost comes from OpenRouter's models-API pricing
(`agent/usage_pricing.py:392-393`, `:463-468`). A model that API does not list gets
`cost_status: "unknown"`; whether the `~`-prefixed alias ids are listed can't be read
from the code, so prefer a concrete model id.

**Sandbox.** The run loads the profile's `terminal:` config, so it uses the docker
backend like any session. With `docker_persist_across_processes` it reattaches to
the labelled container (`tools/environments/docker.py:751-770`). This is inferred
from config loading, not traced end to end.

**Focus mode.** The `coding_context: focus` collapse happens only in the
interactive CLI and TUI (`cli.py:1522-1525`, `tui_gateway/server.py:1925-1926`). A
`-z` run gets the CLI platform toolsets (`hermes_cli/oneshot.py:555-556`) unless
`-t` is given. `-t coding` keeps file and terminal tools (`toolsets.py:70`,
`:191-196`).

**Still to do on alpha:** run the command above on a trivial task, e.g. "create
hello.txt containing hi" in a scratch git repo. Confirm exit 0, the file, a non-zero
`estimated_cost_usd` in the usage file, and that the file was written from inside
the sandbox container. Tick the first criterion then.

**Partial check in a cloud container** (pinned Hermes, no OpenRouter key): the exact command
above was accepted by the CLI; the run failed before any API call (exit 1) and still wrote
the usage file with every field null plus `failed: true` and `failure`, which the rung
parses. The failure itself was the container's uv picking Python 3.14.0rc2, which Hermes'
OpenAI client setup trips on (`_eval_type() got an unexpected keyword argument
'prefer_fwd_module'`); check that alpha's venv is on a 3.14 release, not an rc.

**Revised after the coverage audit:** `-t coding` includes `delegate_task` (and, once the
plugin loads, the routing tools), and a one-shot run still reads `SOUL.md` and the
auto-loaded `delegate` skill, so the child could re-delegate or escalate. The rung now
passes `-t file,terminal,web,todo`, prefixes the brief with a worker preamble, and sets
`LOCODER_ROUTING_CHILD=1`, which makes the routing plugin refuse inside the child.

# locoder

A local coding agent in one repository: the llama.cpp router that serves the
models, a pinned [Hermes Agent](https://github.com/NousResearch/hermes-agent)
install, the `locoder` profile (config, soul, skills, plugins), and the routing
that decides where each delegated task runs.

**Claude Code is the default worker**, spent freely. Local models take the work
they are near-certain to finish and cover for Claude Code while a limit has it
locked out; OpenRouter is the last resort. Success is Claude Code lasting until
its weekly reset without the verified pass rate dropping. One interface — the
Hermes TUI — for all of it.

Contents: [how work flows](#how-work-flows) · [layout](#layout) ·
[requirements](#requirements) · [install](#install) · [how-to](#how-to) ·
[reference](#reference) · [troubleshooting](#troubleshooting) ·
[known limits](#known-limits)

## How work flows

```
 you ──► orchestrator (Laguna-S 2.1, local) ── plans, keeps the gates, verifies,
            │                                    does mechanical work itself
            │  anything bigger than a surgical edit: /delegate
            ▼
         route(brief) ──► judge preset (small CPU model): difficulty 0-3, P(coder finishes)
            │             is Claude Code locked out by a limit?
            │             the session's routing mode (auto / claude / local)
            ▼
   Claude Code available                       Claude Code locked out
     near-certain:  coder → claude → claude      P ≥ 0.3:  coder → openrouter
     otherwise:     claude → claude              P < 0.3:  openrouter
            │                                   (after the last rung: back to you)
            ▼
   coder: delegate_task          claude: escalate()        openrouter: escalate()
   (Qwen3.6-35B-A3B,             claude -p on the          locoder -z: this Hermes
    local, free)                 Max plan                  profile, one-shot, on a
                                                           cheap OpenRouter model
            │
            ▼
   orchestrator runs the acceptance check itself ──► route_outcome(verified) ──► ledger
```

- **Claude Code first, local when it is a sure thing.** The judge sends a
  task to the coder up front only when it is near-certain the coder finishes
  it; everything else starts on Claude Code, which gets one retry with the
  failing check output before the task comes back to you. A wrong "coder" is
  cheap: the chain moves on to Claude Code. The ledger shows when the
  thresholds need moving (`make report`).
- **The judge reads probabilities, not prose.** It asks typed questions about
  the brief, in the interface "System One" decision models share (TypeSafe's
  Jev, the open CLM-8B): a `Noul` (will the coder finish? → P(yes)) and a
  `Score` (difficulty 0–3 → a distribution). Its backend is the SemIf trick on
  a small local model: each question becomes a one-token prompt, sent with
  `max_tokens=1` and `top_logprobs`, and the probability of each allowed
  answer is read off. It runs as its own CPU-only preset because the
  orchestrator and coder each have a single KV slot: one judge query there
  would evict a 64k-token conversation. A Jev or CLM backend would be another
  class with the same `system_one(state, questions)` method
  (`plugins/locoder/routing/judge.py`).
- **A guessing judge is ignored.** Each answer carries *coverage*, the share of
  the judge's probability mass that landed on an allowed answer. Below
  `policy.min_coverage` (0.5) the verdict is recorded but routing treats it as
  no verdict, exactly as when the judge is unreachable: the task starts on
  Claude Code (or, while Claude Code is locked out, on OpenRouter).
- **No rationing; limits are handled when they hit.** Claude Code is used
  until a limit — the short session one or the weekly one — stops it. When
  one hits mid-run, `escalate()` reads the reset time from the error, locks
  Claude Code out until then (an hour if the error names no time), and hands
  the orchestrator the rest of that task's chain: the coder first when the
  judge gives it a fair chance, OpenRouter otherwise. After the reset, Claude
  Code is the default again.
- **OpenRouter runs inside Hermes.** The OpenRouter rung is a one-shot run of
  this same profile (`locoder -z <brief> -m <model> --provider
  custom:openrouter --in <workdir> -t file,terminal,web,todo`) on a cheaper
  model: Hermes stays the harness, its commands go to the sandbox, and
  `--usage-file` reports the cost. The run is the worker, not an orchestrator:
  its toolsets leave out `delegate_task`, `clarify` and the routing tools, its
  brief starts with a line saying so, and the routing plugin refuses inside it
  (`LOCODER_ROUTING_CHILD`), so it cannot re-delegate or spend again.
  `delegate_task` cannot pick a model per task, which is why it is a separate
  process rather than a child. Without a TTY, Hermes refuses a model
  priced over $20/M input or $100/M output, so pick a cheap one.
- **Every project gets [graft](https://github.com/trailhq/graft).** The first
  time a task is routed in a project, `route()` wires graft into it and
  commits that: a section in `AGENTS.md` that points Hermes (orchestrator,
  coder, OpenRouter run) at the `graft` CLI in the sandbox, and a `.mcp.json`
  server, hooks and skill that give Claude Code graft's MCP tools. Agents then
  query a code graph (`graft map`, `ask`, `callers`, `skeleton`, `grep`)
  instead of re-exploring the repo on every task. See
  [use graft in your projects](#use-graft-in-your-projects).
- **Every decision and attempt lands in the ledger** (SQLite, in the Hermes
  home), and `make report` reads it back against the goals above.

## Layout

| path | what | lands at |
|---|---|---|
| `llama/presets.ini` | orchestrator, coder, judge presets | mounted into the router container |
| `llama/image.lock` | llama.cpp CUDA image, pinned by digest | created by `make pin-llama`; commit it |
| `systemd/locoder-llama.service.in` | the router as a user service | `~/.config/systemd/user/` |
| `hermes.rev` | the Hermes commit in use | `~/.local/share/locoder/hermes` (git + uv venv) |
| `profile/` | `config.yaml`, `SOUL.md`, `routing.yaml`, `env.example` | symlinked into the Hermes home |
| `skills/` | the house method; see the [skill index](#skill-index) | symlinked as the profile's skills root |
| `plugins/locoder/routing/` | `route`, `escalate`, `route_outcome`, `routing_status`, `routing_mode` | symlinked plugins dir |
| `plugins/web/defuddle/` | clean page extraction + `web_research` | symlinked plugins dir |
| `sandbox/Dockerfile` | the container every agent `terminal()` call runs in, with the pinned graft | image `locoder-sandbox:local` |
| `bin/locoder.in` | the `locoder` wrapper: pinned Hermes + this home + its `.env` | `~/.local/bin/locoder` |
| `scripts/` | install, bump, check, report, bakeoff | — |
| `tests/` | routing plugin, report and bake-off tests (no Hermes needed) | — |
| `.todo/` | specs and tickets, committed with the code | — |

State lives outside the repo: the Hermes home (sessions, memories, the routing
ledger at `routing/ledger.db`, `.env`) is `~/.local/state/locoder/home`.

`make install` symlinks the running agent's home straight into this checkout,
so editing a skill, plugin or profile file changes the live agent; `AGENTS.md`
lists what takes effect when and what to run afterwards.

## Requirements

On NixOS, only what needs root stays in `configuration.nix`:

```nix
hardware.nvidia-container-toolkit.enable = true;  # GPUs for Docker via CDI
virtualisation.docker.enable = true;
programs.nix-ld.enable = true;                     # uv's managed Python 3.14 is a generic Linux binary
users.users.placek.extraGroups = [ "docker" ];
```

and in your user environment: `uv` (recent — 0.8.x cannot parse Hermes'
lockfile; tested with 0.12.19), `git`, `npm` (Node 20 or newer: graft is installed
with it, and its tree-sitter prebuilt binaries rely on `nix-ld`), `make`, `curl`,
and the `claude` CLI logged in to your Max plan. The Hermes venv must end up on a Python 3.14
*release*: on 3.14.0rc2 Hermes cannot build its OpenAI client (see
[troubleshooting](#troubleshooting)).

Models go in `/srv/data/models` (override with `MODELS=`): the two GGUFs named
in `presets.ini`, plus the judge at `judge.gguf` — any small instruct model
(3–4B, Q4) will do; a symlink within the directory is fine. `make check` asks
it a sanity question and warns if its answers are unusable.

## Install

```sh
git clone git@github.com:placek/locoder.git && cd locoder
make install        # Hermes venv, profile links, defuddle, sandbox image, router unit, `locoder` on PATH
$EDITOR ~/.local/state/locoder/home/.env     # OPENROUTER_API_KEY
make enable         # start the router now and at login (loginctl enable-linger for boot)
make check          # plugins loaded, tools visible, presets served, judge answering
make tui            # or just: locoder
```

Then, once:

1. Set `claude.week` in `profile/routing.yaml` to when your Max week resets
   (`/usage` in Claude Code shows it). Weekly results are grouped by it.
2. [Smoke-test the OpenRouter rung](#smoke-test-the-openrouter-rung), so the
   last resort is known to work before you need it.

### Moving over from home.nix

This replaces three things in `placek/home.nix`, which can go once `make
check` passes:

- `machines/alpha/configuration.nix`: the `services.llama-cpp.*` block, its
  `systemd.services.llama-cpp` override, and the `hermes0` firewall rules for
  port 8088. The router now listens on `127.0.0.1:8088` only, and Hermes runs
  on the host, so nothing needs a bridge rule.
- `modules/utils/hermes-agent.nix`: but see below before removing it.
- `~/.hermes/profiles/locoder`: the old profile. Sessions and memories there
  are not migrated; this home starts clean.

**Careful:** the brain profile's Telegram gateway (`hermes-gateway.service`,
`~/.hermes`) runs the Hermes binary that `hermes-agent.nix` installs. Removing
the module removes that binary too. Either keep the module until the brain
profile moves, or point that service at this repo's venv
(`~/.local/share/locoder/hermes/.venv/bin/hermes`) with its own `HERMES_HOME`.

## How-to

### Get a change made

Ask for it in the TUI. The orchestrator does mechanical work itself (commits,
moving files, surgical edits); anything bigger goes through the `delegate`
skill, which you can also start with `/delegate`. What to expect:

1. It writes a brief: goal, where, the exact acceptance command, "touch nothing
   outside …". No acceptance command means it writes the failing test first
   (`/tdd`).
2. `route()` picks the starting rung and the chain; the reason is shown.
3. Each attempt is verified by the orchestrator running the acceptance command
   itself, then recorded with `route_outcome`. A failed attempt is stashed
   (`git stash list` shows `delegate <decision_id> <rung>`) and the next rung
   starts from a clean tree.
4. If the whole chain fails, the task comes back to you with what each rung
   did, the failing output and the stash names.

### Use graft in your projects

Nothing to do: the first delegated task in a project wires it. `route()` runs,
at the repository root,

```sh
graft init --no-global --no-statusline --no-build --agents agents claude
graft build
```

and commits only the files graft wrote, as "Wire in graft": the fenced section
in `AGENTS.md`, `.mcp.json`, `.claude/settings.json` (hooks), `.claude/helpers/`,
`.claude/skills/graft/`, and the `.gitignore` / `.ignore` entries for `graft/`.
The graph itself, `graft/`, is a local cache and stays out of git; every graft
query refreshes it from the working tree, so there is nothing to rebuild by
hand. `route()`'s output says what happened under `graft`.

It only wires git checkouts under `claude.workdir_roots`, and it skips (and
says why) when a file graft would write has uncommitted changes or a merge or
rebase is in progress; commit or stash those and the next task wires it. Your
other staged or unstaged work is never part of the wiring commit. If `init`,
`build` or the commit fails, graft's changes are rolled back and that repo is
not tried again until Hermes restarts.

- **Who uses it how.** Hermes reads the `AGENTS.md` chain when a session
  starts, so delegated runs (coder, OpenRouter) follow graft's section at once;
  the orchestrator session that triggered the wiring picks it up next session.
  Claude Code loads `.mcp.json`, the hooks and the skill on its own in `-p`
  runs; `mcp__graft` in `claude.allowed_tools` lets it call the tools.
- **Wire a project by hand** (e.g. one you only work on outside the harness):
  run the two commands above in its root with `locoder`'s PATH, or in the
  sandbox, and commit what they wrote.
- **Turn it off:** `graft.enabled: false` in `profile/routing.yaml`. To unwire
  a project, `graft uninstall -y --no-global` in it and commit.
- **No telemetry:** the `locoder` wrapper and the sandbox set `DO_NOT_TRACK=1`,
  so graft sends no usage pings. Its daily npm version check still runs.
- **The LLM layer is not used:** `graft build --deep` (concept nodes and
  per-symbol summaries) needs a provider key and is left off; the structural
  graph is free and deterministic.

### Force Claude Code or local for a session

```
/routing-mode claude     Claude Code only, with its one retry
/routing-mode local      the local coder only; a failure comes straight back
/routing-mode auto       back to judged routing
/routing-mode            show the current mode
```

The mode lasts until the session ends, context compression included; every new
session starts in `auto`. In `claude` mode, while Claude Code is locked out,
tasks come back to you with the reset time instead of running anywhere else,
and `escalate()` refuses OpenRouter; in `local` mode it refuses both paid rungs. The judge is still asked in the
forced modes and its verdict recorded: forced-local runs on tasks it rated hard
are calibration data `auto` never produces.

### See where Claude Code stands

Ask the agent "routing status" (it calls `routing_status`): the session's mode,
whether Claude Code is available or locked out and until when, and this week's
attempts, passes, failures and cost per rung.

### Handle a Claude Code limit

Nothing to do in the moment: the run that hit the limit hands the rest of the
task to the coder or OpenRouter (skipping any rung that task already failed),
and Claude Code is skipped until the reset its error stated. In `claude` mode
the task comes back to you with the reset time instead. Afterwards, check that the reset was read:

```sh
sqlite3 ~/.local/state/locoder/home/routing/ledger.db \
  "SELECT datetime(ts,'unixepoch','localtime'), datetime(until,'unixepoch','localtime'), reset_source, raw
   FROM limit_hits ORDER BY ts DESC LIMIT 5"
```

`reset_source = fallback` means the reset time was not understood and the
lockout was the one-hour `claude.limit_fallback_s`. Add the wording in `raw` as
a case in `tests/test_routing.py` (`test_reset_time_reads_clock_date_and_relative_forms`),
fix `reset_time()` in `plugins/locoder/routing/backends.py`, and `make test`.
Claude Code's `-p` wording is undocumented, so the first real hits are the
ones to check.

### Read the weekly report

```sh
make report
```

- **without Claude Code … days** — the main signal. Rising means too little is
  offloaded: loosen `policy.local_threshold` / `local_max_difficulty`.
- **verified** — the floor. If it drops after loosening, tighten again.
- **coder attempts by why it ran** — *sure thing* should pass almost always;
  a weak *fallback* rate is the case for a heavier local model behind Claude
  Code; *forced (local mode)* shows what the coder does on tasks `auto` would
  not give it.
- **judge calibration** — pass rate per P(local) band, split at the two
  thresholds. The bands should rise from left to right.
- **openrouter** — spend per week and per month.

### Tune the routing

Everything is in `profile/routing.yaml` and applies on the next tool call, no
restart:

- `policy.local_threshold` / `local_max_difficulty` (0.85 / 0.5) — how
  near-certain the judge must be before a task starts on the coder instead of
  Claude Code. Loosen to offload more; tighten if the coder keeps failing what
  it is given.
- `policy.fallback_threshold` (0.3) — while Claude Code is locked out, the
  P(local) at which the coder is tried before OpenRouter.
- `policy.min_coverage` (0.5) — how much of the judge's probability mass must
  land on its allowed answers for the verdict to count. Raise it if `make
  report` shows low-coverage verdicts routing badly; lower it (or change the
  judge model) if `route()` keeps saying the verdict was ignored.
- `openrouter.model` — what runs when Claude Code is out and the coder is not;
  pick it with the bake-off below.

The full list of keys is under [reference](#routingyaml).

### Pick the OpenRouter model

```sh
make bakeoff MODELS=deepseek/deepseek-v4.1-flash,qwen/qwen3.8-flash [TASKS=10]
```

It replays the latest routed tasks that recorded a clean commit, on each model,
in throwaway git worktrees under the first `claude.workdir_roots` entry, and
scores each by the brief's acceptance command (run in the sandbox image;
changes outside the brief's paths count as failures). It reads the ledger
read-only and removes every worktree it adds. Tasks routed from a dirty tree
are skipped (their uncommitted files, usually the failing test from `/tdd`,
are missing from the commit); `scripts/bakeoff.py --include-dirty` replays them
anyway. A fresh worktree lacks untracked build state, so compare models with
each other, not against 100%. Put the winner in `openrouter.model` and
summarise the result in that commit. Use concrete model ids: Hermes prices runs
from OpenRouter's model list, and an unlisted alias costs "unknown".

### Smoke-test the OpenRouter rung

In a scratch git repo under `/srv/data/projects`:

```sh
locoder -z "Create hello.txt containing the word hi" -m deepseek/deepseek-v4.1-flash \
  --provider custom:openrouter --in "$PWD" --usage-file /tmp/usage.json -t file,terminal,web,todo; echo "exit=$?"
cat hello.txt /tmp/usage.json
```

Expect exit 0, `hello.txt`, and a non-zero `estimated_cost_usd`. This is what
`escalate(backend="openrouter")` runs, minus the worker preamble it puts in
front of the brief and the `LOCODER_ROUTING_CHILD=1` it sets.

### Change a skill, plugin or the profile

The checkout is live (see `AGENTS.md`), so after touching `skills/`,
`plugins/` or `profile/`:

```sh
make test            # routing plugin, report and bake-off tests
make check-offline   # plugins load in the pinned Hermes, tools in the coding toolset, profile wired
```

A new skill goes in `skills/<name>/SKILL.md` with `name` matching the
directory; add it to the [skill index](#skill-index). `AGENTS.md` covers the
discovery traps.

### Update Hermes, graft or the llama.cpp image

```sh
make bump                # Hermes to origin/main; kept only if `make check` passes, else rolled back
make bump REV=<sha>      # a specific commit; commit hermes.rev afterwards
make pin-llama           # re-resolve the llama.cpp image tag to a new digest; commit llama/image.lock
```

Graft is pinned by `GRAFT_VERSION` in the `Makefile`, once for both places it
runs: change it, `make install` (reinstalls it on the host and rebuilds the
sandbox image), then `make check`, which fails if either copy differs from the
pin. Hermes (sandbox) and Claude Code (host) share each project's `graft/`
cache, so they should run the same version.

Use `make bump`, not `hermes update`: the checkout is detached at the pinned
commit, and `bump` re-runs the plugin checks against the new build before
keeping it. After a bump, re-check the facts the OpenRouter rung relies on
(the `-z` flags and exit codes, recorded in
`.todo/claude-first-routing/04-spike-hermes-child-on-openrouter.md`) with the
smoke test above.

## Reference

### Make targets

```
make install        build/link everything (idempotent); after editing presets.ini it restarts the router
make graft          install the pinned graft on the host (part of install)
make enable|disable start/stop the router now and at login
make restart        after `make install` regenerated the unit (new GPU_ARGS, MODELS, image)
make status         router state and served presets
make logs           follow the router
make check          the installed stack is wired: profile, plugins, host tools, router, judge
make check-offline  the same without the router and judge
make tui            open the agent (same as `locoder`)
make test           unit tests, no Hermes needed
make report         the ledger against the routing goals
make bakeoff MODELS=a,b [TASKS=10]   pick the OpenRouter model
make bump [REV=…]   move Hermes, kept only if `make check` passes
make pin-llama      pin a new llama.cpp image digest
make uninstall      remove the unit, wrapper, links and the Hermes install; PURGE=1 also deletes the state
```

### Routing tools

All register in the `coding` toolset, so they stay visible under
`agent.coding_context: focus`.

| tool | arguments | what it does |
|---|---|---|
| `route` | `brief`, `workdir` | wires graft into the project if needed, asks the judge, checks Claude Code's lockout and the session mode; returns `decision_id`, `rung`, `chain`, `reason`, and `graft` (wired, already, skipped or failed, with the reason). A rung listed twice is its one retry; `rung: user` means nothing may run now |
| `escalate` | `brief`, `workdir`, `decision_id`, `backend` (`auto`/`claude`/`openrouter`), `max_turns` | runs a paid rung and records the attempt; on a limit hit returns `claude_unavailable_until` and the rest of the `chain`, minus rungs the task already failed. Honours the session's mode |
| `route_outcome` | `decision_id`, `rung`, `verified`, `notes` | labels an attempt after the orchestrator ran the acceptance check |
| `routing_status` | — | the session's mode, Claude Code's lockout, this week's results per rung |
| `routing_mode` | `mode` (optional) | sets or shows the session's routing mode |

### `routing.yaml`

Linked into the Hermes home and re-read on every tool call. Keys left out fall
back to the defaults in `plugins/locoder/routing/settings.py`.

| key | default | meaning |
|---|---|---|
| `llama.base_url` | `http://127.0.0.1:8088/v1` | the llama.cpp router |
| `llama.judge_model` | `judge` | the CPU-only judge preset; never the orchestrator or coder |
| `llama.timeout_s` | 30 | per judge question |
| `policy.local_threshold` | 0.85 | P(local) for the coder to go first |
| `policy.local_max_difficulty` | 0.5 | and expected difficulty (0–3) at most this |
| `policy.fallback_threshold` | 0.3 | P(local) for the coder to go first while Claude Code is locked out |
| `policy.min_coverage` | 0.5 | a verdict whose answers got less of the judge's probability mass is ignored |
| `claude.bin` | `claude` | the Claude Code CLI |
| `claude.max_turns` | 15 | `--max-turns` per run |
| `claude.timeout_s` | 1800 | per run |
| `claude.allowed_tools` | Read, Edit, Write, Bash, Grep, Glob, mcp__graft | `--allowedTools`; `mcp__graft` allows every tool of the project's graft MCP server |
| `claude.workdir_roots` | `/srv/data/projects` | `escalate()` refuses workdirs elsewhere |
| `claude.week` | Monday 09:00, Europe/Warsaw | when the Max week resets; groups weekly results |
| `claude.limit_fallback_s` | 3600 | lockout when a limit error names no readable reset |
| `claude.result_chars` | 8000 | how much of a run's final report comes back |
| `graft.enabled` | true | wire graft into a project on its first routed task |
| `graft.bin` | `graft` | the host graft (the wrapper puts the pinned one on PATH) |
| `graft.timeout_s` | 300 | per `graft init` / `graft build` |
| `openrouter.enabled` | true | off: the coder is all that is left while Claude Code is locked out |
| `openrouter.hermes_bin` | `locoder` | what runs the one-shot Hermes run |
| `openrouter.provider` | `custom:openrouter` | the profile's provider entry (bare `openrouter` is Hermes' built-in) |
| `openrouter.model` | `deepseek/deepseek-v4.1-flash` | placeholder until a bake-off picks one |
| `openrouter.toolsets` | `file,terminal,web,todo` | `-t` for the one-shot run: no `delegate_task`, `clarify` or routing tools |
| `openrouter.timeout_s` | 1800 | per run |

### The ledger

`~/.local/state/locoder/home/routing/ledger.db`. Older ledgers gain new columns
in place when the plugin opens them.

- `decisions` — one row per `route()`: the brief, workdir, starting rung and
  reason, the judge's verdict (JSON), Claude Code's availability (JSON), the
  workdir's `commit_sha` and `dirty` flag, and the session `mode`.
- `attempts` — one row per run: rung, whether the backend reported success
  (`ok`), whether the acceptance check passed (`verified`, the label that
  matters), `limit_hit`, `cost`, turns, duration, session id, notes.
- `limit_hits` — when a limit hit, `until` when Claude Code is skipped, whether
  that came from the error (`reset_source = stated`) or the fallback, and the
  `raw` error text.

### Skill index

Invoke any of them as `/<name>`.

| skill | what it is for |
|---|---|
| `delegate` | route, run, verify, record and escalate implementation work bigger than a surgical edit |
| `routing-mode` | switch this session between `auto`, Claude Code only and local only |
| `ship` | run a feature end to end: grill, spec, tickets, failing tests, implement, verify, document |
| `implement` | implement a piece of work from a spec or tickets |
| `tdd` | test-first, red-green-refactor |
| `verify` | evidence before claiming anything is done, fixed or passing |
| `diagnosing-bugs` | the diagnosis loop for hard bugs and performance regressions |
| `refactor` | behaviour-preserving refactoring in small test-gated steps |
| `code-review` | review changes since a fixed point against the repo's standards and the spec |
| `resolving-merge-conflicts` | resolve an in-progress merge or rebase |
| `research` | investigate against primary sources, save the findings as Markdown |
| `grilling`, `grill-me`, `grill-with-docs` | a relentless interview to sharpen a plan (the last also writes ADRs and a glossary) |
| `to-spec`, `to-tickets` | turn a conversation into a spec, or a spec into tracer-bullet tickets in `.todo/` |
| `triage` | move `.todo/` issues through triage and write agent-ready briefs |
| `wayfinder` | plan work too big for one session as a map of decision tickets |
| `prototype` | a throwaway prototype to answer a design question |
| `codebase-design`, `improve-codebase-architecture` | deep-module vocabulary; find and grill deepening opportunities |
| `domain-modeling` | sharpen the domain model, `CONTEXT.md` and ADRs |
| `to-agent`, `to-report`, `to-questionnaire` | a handoff for another agent, a plain-language report, a questionnaire for someone else |
| `wait-what` | re-pitch a message that did not land |
| `writing-for-agents` | how to write skills, `AGENTS.md` and other agent-facing documents |

## Troubleshooting

| symptom | cause and fix |
|---|---|
| `make hermes` fails with `Failed to parse uv.lock` | `uv` is too old; use 0.12 or newer |
| a one-shot run fails with `_eval_type() got an unexpected keyword argument 'prefer_fwd_module'` | the Hermes venv is on Python 3.14.0rc2; remove `~/.local/share/locoder/hermes/.venv` and `.locoder-rev` there, then `PYTHON=3.14.<n> make hermes` with a release version |
| the OpenRouter rung exits 1 with "Refusing this startup model override in non-interactive mode" | the model tripped Hermes' cost guard (over $20/M input or $100/M output) or is a data-training tier; pick a cheaper model, or for `:free` tiers set `security.allow_data_training_tiers_noninteractive: true` in `config.yaml` |
| `make check` warns `locoder not on PATH` | the OpenRouter rung cannot start; add `~/.local/bin` to PATH or set `openrouter.hermes_bin` to the wrapper's full path |
| OpenRouter attempts show cost 0 and `cost_status: unknown` | the model id is an alias OpenRouter's price list does not have; use a concrete id |
| the reason says the judge's coverage is below `policy.min_coverage` | the judge model mostly answers outside the allowed tokens: try another small instruct model as `judge.gguf` (`make check` warns on it), or lower `policy.min_coverage` |
| every task starts on Claude Code and the reason says "judge unavailable" | the judge preset is down or `judge.gguf` is missing; `make status`, `make check` |
| Claude Code is skipped although its limit has reset | the lockout came from the fallback or a misread reset; see [handle a Claude Code limit](#handle-a-claude-code-limit) |
| a skill does not show up in `locoder skills list` | a symlink inside `skills/`, a `name` that differs from its directory, or invalid frontmatter; see `AGENTS.md` |
| `route()` says `graft: skipped — uncommitted changes in files graft writes` | commit or stash those files (`AGENTS.md`, `.mcp.json`, `.gitignore`, `.claude/…`); the next routed task wires the repo |
| `route()` says `graft: failed` | the reason names the step (`init`, `build`, or the commit, e.g. a pre-commit hook); fix it and restart Hermes, or wire by hand |
| `make check` fails on `graft (host)` or `graft (sandbox)` | a copy is missing or not at `GRAFT_VERSION`; `make install` |
| Claude Code never calls graft's tools | the project is not wired (no `.mcp.json`), or `mcp__graft` is missing from `claude.allowed_tools` |

## Known limits

- Claude Code's usage is not visible in advance: no documented command reports
  it without a session, so limits are only learned when a run fails on one.
- Limit hits and their reset times are read from Claude Code's error text,
  whose `-p` wording is undocumented. If a future CLI words it differently,
  the run fails as an ordinary error instead of falling back, or the reset is
  unreadable and the lockout is `claude.limit_fallback_s`. Every hit keeps its
  raw text in the ledger's `limit_hits.raw`; `LIMIT_PATTERN` and
  `reset_time()` in `plugins/locoder/routing/backends.py` are the place to fix.
- `escalate()` runs Claude Code on the host (it needs your Max login), with the
  tool allow-list in `routing.yaml` and workdirs restricted to
  `claude.workdir_roots`. The local agent's own commands, and the OpenRouter
  rung's, run in the sandbox.
- The routing mode is held in memory by the plugin, keyed by the
  conversation's root session (so compression keeps it): restarting Hermes
  resets every session to `auto`.
- Graft's wiring reaches the orchestrator's own system prompt only from its
  next session (Hermes reads `AGENTS.md` at session start); delegated runs see
  it at once.
- The coder serves one slot, so `delegation.max_concurrent_children` is 1:
  fan-out skills (`code-review`, `research`) run their children in sequence.

## Credits

Most skills are near-verbatim copies of [Matt Pocock's skills](https://github.com/mattpocock/skills)
(MIT, see `skills/LICENSE.mattpocock`), adapted to track issues as markdown in
`.todo/`; they came here from `placek/skills`.

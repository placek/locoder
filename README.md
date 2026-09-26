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

## How work flows

```
 you ──► orchestrator (Laguna-S 2.1, local) ── plans, keeps the gates, verifies,
            │                                    does mechanical work itself
            │  anything bigger than a surgical edit: /delegate
            ▼
         route(brief) ──► judge preset (small CPU model): difficulty 0-3, P(coder finishes)
            │             is Claude Code locked out by a limit?
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
- **The judge reads probabilities, not prose.** It asks one-token questions
  with `max_tokens=1` and `top_logprobs`, and reads the probability of each
  allowed answer (the SemIf trick). It runs as its own CPU-only preset because
  the orchestrator and coder each have a single KV slot: one judge query there
  would evict a 64k-token conversation.
- **No rationing; limits are handled when they hit.** Claude Code is used
  until a limit — the short session one or the weekly one — stops it. When
  one hits mid-run, `escalate()` reads the reset time from the error, locks
  Claude Code out until then (an hour if the error names no time), and hands
  the orchestrator the rest of that task's chain: the coder first when the
  judge gives it a fair chance, OpenRouter otherwise. After the reset, Claude
  Code is the default again.
- **OpenRouter runs inside Hermes.** The OpenRouter rung is a one-shot run of
  this same profile (`locoder -z <brief> -m <model> --provider
  custom:openrouter --in <workdir> -t coding`) on a cheaper model: Hermes stays
  the harness, its commands go to the sandbox, and `--usage-file` reports the
  cost. `delegate_task` cannot pick a model per task, which is why it is a
  separate process rather than a child. Without a TTY, Hermes refuses a model
  priced over $20/M input or $100/M output, so pick a cheap one.

## Layout

| path | what | lands at |
|---|---|---|
| `llama/presets.ini` | orchestrator, coder, judge presets | mounted into the router container |
| `llama/image.lock` | llama.cpp CUDA image, pinned by digest | created by `make pin-llama`; commit it |
| `systemd/locoder-llama.service.in` | the router as a user service | `~/.config/systemd/user/` |
| `hermes.rev` | the Hermes commit in use | `~/.local/share/locoder/hermes` (git + uv venv) |
| `profile/` | `config.yaml`, `SOUL.md`, `routing.yaml`, `env.example` | symlinked into the Hermes home |
| `skills/` | the house method (`ship`, `tdd`, `verify`, `delegate`, `routing-mode`, …) | symlinked as the profile's skills root |
| `plugins/locoder/routing/` | `route`, `escalate`, `route_outcome`, `routing_status`, `routing_mode` | symlinked plugins dir |
| `plugins/web/defuddle/` | clean page extraction + `web_research` | symlinked plugins dir |
| `sandbox/Dockerfile` | the container every agent `terminal()` call runs in | image `locoder-sandbox:local` |
| `scripts/` | install, bump, check, report | — |
| `tests/` | routing plugin tests (no Hermes needed) | — |

State lives outside the repo: the Hermes home (sessions, memories, the routing
ledger, `.env`) is `~/.local/state/locoder/home`.

## Requirements

On NixOS, only what needs root stays in `configuration.nix`:

```nix
hardware.nvidia-container-toolkit.enable = true;  # GPUs for Docker via CDI
virtualisation.docker.enable = true;
programs.nix-ld.enable = true;                     # uv's managed Python 3.14 is a generic Linux binary
users.users.placek.extraGroups = [ "docker" ];
```

and in your user environment: `uv` (recent — 0.8.x cannot parse Hermes'
lockfile; tested with 0.12.19), `git`, `npm`, `make`, `curl`, and the `claude`
CLI logged in to your Max plan.

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

Set `claude.week` in `profile/routing.yaml` to when your Max week resets
(`/usage` in Claude Code shows it) — weekly results are grouped by it.

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

## Operating

```
make status         router state and served presets
make logs           follow the router
make restart        after `make install` regenerated the unit (new GPU_ARGS, MODELS, image)
make install        after editing presets.ini: restarts the router if running
make bump           Hermes to origin/main; kept only if `make check` passes, else rolled back
make bump REV=<sha> a specific commit; commit hermes.rev afterwards
make pin-llama      re-resolve the llama.cpp image tag to a new digest; commit llama/image.lock
make report         days without Claude Code, pass rates, coder as fallback, OpenRouter spend, judge calibration
make bakeoff MODELS=a,b   replay recent delegated tasks on candidate OpenRouter models; set the winner as openrouter.model
make test           the routing plugin's unit tests
```

Update Hermes with `make bump`, not `hermes update`: the checkout is detached
at the pinned commit, and `bump` is what re-runs the plugin checks against the
new build before keeping it.

## Routing modes

`/routing-mode <auto|claude|local>` switches the current session; every session
starts in `auto`, the judged routing above.

- `claude` — Claude Code only, with its one retry. While a limit has it locked
  out, tasks come straight back to you with the reset time.
- `local` — the local coder only, no paid rung. A failed task comes straight
  back to you.

The judge is still asked in the forced modes and its verdict recorded, and each
decision records its mode: forced-local runs on tasks the judge rated hard are
calibration data `auto` never produces.

## Tuning the routing

Everything is in `profile/routing.yaml` and applies on the next tool call:

- `policy.local_threshold` / `local_max_difficulty` — how near-certain the
  judge must be before a task starts on the coder instead of Claude Code
  (0.85 / 0.5). Loosen them to offload more if Claude Code keeps running out
  before the reset; tighten them if the coder keeps failing what it is given.
  `make report` buckets the coder's pass rate by the judge's P(local).
- `policy.fallback_threshold` — while Claude Code is locked out, the P(local)
  at which the coder is tried before OpenRouter (0.3).
- `openrouter.model` — what runs when Claude Code is out and the coder is not.

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
  `claude.workdir_roots`. The local agent's own commands run in the sandbox.
- The coder serves one slot, so `delegation.max_concurrent_children` is 1:
  fan-out skills (`code-review`, `research`) run their children in sequence.

## Credits

Most skills are near-verbatim copies of [Matt Pocock's skills](https://github.com/mattpocock/skills)
(MIT, see `skills/LICENSE.mattpocock`), adapted to track issues as markdown in
`.todo/`; they came here from `placek/skills`.

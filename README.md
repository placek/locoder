# locoder

A local coding agent in one repository: the llama.cpp router that serves the
models, a pinned [Hermes Agent](https://github.com/NousResearch/hermes-agent)
install, the `locoder` profile (config, soul, skills, plugins), and the routing
that decides when a task is worth a paid model.

Priorities it is built around, in order: **time** (don't wait on a local model
that was never going to finish), **the weekly Claude Code limit** (make it last
until the reset instead of running out two days early), then **money**. One
interface — the Hermes TUI — for all of it.

## How work flows

```
 you ──► orchestrator (Laguna-S 2.1, local) ── plans, keeps the gates, verifies
            │
            │  anything bigger than a surgical edit: /delegate
            ▼
         route(brief) ──► judge preset (small CPU model): difficulty 0-3, P(coder finishes)
            │             pacer: Claude Code spend vs. time left in the week
            ▼
   chain, e.g.  coder ──fail──► claude ──fail──► openrouter
                  │                │                 │
          delegate_task      escalate():        escalate():
          (Qwen3.6-35B-A3B,  claude -p on the   the same claude -p,
           local, free)      Max plan           pointed at OpenRouter
            │
            ▼
   orchestrator runs the acceptance check itself ──► route_outcome(verified) ──► ledger
```

- **The judge picks where a task starts, not where it ends.** A wrong call is
  cheap: if it starts on the coder and fails, the cascade escalates anyway.
  So a zero-shot judge is good enough on day one, and the ledger shows when
  its thresholds need moving (`make report`).
- **The judge reads probabilities, not prose.** It asks one-token questions
  with `max_tokens=1` and `top_logprobs`, and reads the probability of each
  allowed answer (the SemIf trick). It runs as its own CPU-only preset because
  the orchestrator and coder each have a single KV slot: one judge query there
  would evict a 64k-token conversation.
- **The pacer rations Max.** Spend may run 10% ahead of a straight line across
  the week. Ahead of that, Claude Code is kept for hard tasks and the rest goes
  to OpenRouter. When the limit hits mid-run, `escalate()` records it, retries
  that task on OpenRouter, and routes around Claude Code until the reset. Each
  limit hit also records what had been spent, and the median of those becomes
  the week's budget — the pacer learns the size of your limit.
- **Both paid rungs are the same CLI.** OpenRouter serves an
  Anthropic-compatible endpoint, so the OpenRouter rung is `claude -p` with
  `ANTHROPIC_BASE_URL` pointed there (and its own `CLAUDE_CONFIG_DIR`, so the
  cached Max login never collides with the gateway key). Same brief, same JSON
  result, same usage accounting. Blackout days use the same model family.

## Layout

| path | what | lands at |
|---|---|---|
| `llama/presets.ini` | orchestrator, coder, judge presets | mounted into the router container |
| `llama/image.lock` | llama.cpp CUDA image, pinned by digest | created by `make pin-llama`; commit it |
| `systemd/locoder-llama.service.in` | the router as a user service | `~/.config/systemd/user/` |
| `hermes.rev` | the Hermes commit in use | `~/.local/share/locoder/hermes` (git + uv venv) |
| `profile/` | `config.yaml`, `SOUL.md`, `routing.yaml`, `env.example` | symlinked into the Hermes home |
| `skills/` | the house method (`ship`, `tdd`, `verify`, `delegate`, …) | symlinked as the profile's skills root |
| `plugins/locoder/routing/` | `route`, `escalate`, `route_outcome`, `routing_status` | symlinked plugins dir |
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
(`/usage` in Claude Code shows it) — "ahead of pace" is measured from there.

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
make report         judge calibration and per-rung results from the ledger
make test           the routing plugin's unit tests
```

Update Hermes with `make bump`, not `hermes update`: the checkout is detached
at the pinned commit, and `bump` is what re-runs the plugin checks against the
new build before keeping it.

## Tuning the routing

Everything is in `profile/routing.yaml` and applies on the next tool call:

- `policy.local_threshold` / `local_max_difficulty` — how optimistic the judge
  must be before a task starts on the coder. Lower them if the coder keeps
  passing tasks it was not given; raise them if it keeps failing ones it was.
  `make report` buckets the coder's pass rate by the judge's P(local).
- `policy.hard_difficulty` — the bar for spending Claude Code when ahead of pace.
- `policy.pace_slack` — how far ahead of schedule spending may run.
- `claude.weekly_budget` — only the starting guess; limit hits replace it.
- `openrouter.model` — what the blackout days run on.

## Known limits

- The pacer measures Claude Code's reported `total_cost_usd`, not the
  subscription's own meter, which it cannot read. The unit only has to be
  consistent: the budget is learned from what had been spent at each limit hit.
- Limit hits are detected from Claude Code's error text. If a future CLI words
  it differently, the run fails as an ordinary error instead of falling back;
  `LIMIT_PATTERN` in `plugins/locoder/routing/backends.py` is the one place to fix.
- `escalate()` runs Claude Code on the host (it needs your Max login), with the
  tool allow-list in `routing.yaml` and workdirs restricted to
  `claude.workdir_roots`. The local agent's own commands run in the sandbox.
- The coder serves one slot, so `delegation.max_concurrent_children` is 1:
  fan-out skills (`code-review`, `research`) run their children in sequence.

## Credits

Most skills are near-verbatim copies of [Matt Pocock's skills](https://github.com/mattpocock/skills)
(MIT, see `skills/LICENSE.mattpocock`), adapted to track issues as markdown in
`.todo/`; they came here from `placek/skills`.

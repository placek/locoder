# AGENTS.md

The locoder stack: llama.cpp router, pinned Hermes, the `locoder` profile, and
the routing plugin. See [README.md](./README.md) for how the parts fit.

## This checkout is live

`make install` symlinks the running agent's home straight into this repo:

| path | effect of editing it |
|---|---|
| `skills/` | the running agent's skills — a saved `SKILL.md` is live next time it loads |
| `plugins/` | Hermes loads these; a Python change needs a new session |
| `profile/config.yaml`, `profile/SOUL.md` | take effect **next session**, not this one |
| `profile/routing.yaml` | re-read on every routing tool call |
| `llama/presets.ini` | read at router start; `make install` restarts a running router |

So a broken `SKILL.md`, `plugin.yaml` or plugin module breaks the agent editing
it. After touching any of them:

```sh
make test           # routing plugin unit tests
make check-offline  # plugins load in the pinned Hermes, tools visible, profile wired
```

Two couplings a change must keep in step:

- `delegation.max_concurrent_children` in `profile/config.yaml` equals the
  coder's `parallel` in `llama/presets.ini` (`make check` enforces it).
- Plugin tools register into the `coding` toolset. Under
  `agent.coding_context: focus` Hermes collapses tools to that toolset, so a
  tool registered anywhere else silently vanishes.

## Writing a skill

`skills/<name>/SKILL.md`, YAML frontmatter with `name` (matching the directory)
and `description`; reference files sit beside it. Add a line to the README's
skill index. Load `writing-for-agents` before writing one — it covers the
description wording that decides whether the skill ever fires, and
`SKILL-MECHANICS.md` beside it covers frontmatter.

Two names to avoid: anything Hermes already owns as a built-in command (that is
why `handoff` became `to-agent`), and a name already in `skills/`.

## Discovery traps

Skill and plugin discovery do **not** behave the same way:

- **Skills** are found with `Path.rglob("**/SKILL.md")`, which refuses to
  descend into symlinked directories. A symlink *inside* a skills root finds
  nothing; only the root itself may be a link.
- **Plugins** are found with `iterdir()`, which follows symlinks fine.

A skill that does not appear in `skills list` is usually this, a `name` that
disagrees with its directory, or invalid frontmatter YAML.

## Conventions

Issues are markdown in `.todo/`, committed with the code; finishing one changes
its `Status:` line rather than deleting the file. Commits use an imperative
subject and a body explaining why, not what.

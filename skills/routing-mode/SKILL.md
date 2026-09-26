---
name: routing-mode
description: Switch this session's routing between auto, Claude Code only, and local only.
---

The argument is the mode: `auto`, `claude` or `local`.

1. With a mode, call `routing_mode(mode=<mode>)`. With no argument, call `routing_mode()` to read the current one.
2. Tell the user the session's mode in one line, with what it means for the next delegated task:
   - `auto` — `route()` picks the rung by the judge: Claude Code by default, the local coder when it is a sure thing.
   - `claude` — Claude Code only, with its one retry. While a limit has it locked out, tasks come back to the user.
   - `local` — the local coder only. A task that fails its check comes back to the user, no retry.

The mode lasts until this session ends; a new session starts in `auto`. It changes where `route()` starts a task, nothing else: keep delegating through the `delegate` skill.

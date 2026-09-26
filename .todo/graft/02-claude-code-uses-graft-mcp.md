# 02: The Claude Code rung can call graft's MCP tools

Status: done
Blocked by: 01

## What to build

`claude -p` already loads a project's `.mcp.json` servers, hooks and skills without a
prompt, but `escalate()` passes an explicit `--allowedTools`, which would deny graft's
MCP tools. `mcp__graft` joins the default `claude.allowed_tools`.

## Acceptance criteria

- [x] The default allow-list contains `mcp__graft`, and the Claude Code command carries it.
- [x] `routing.yaml` documents it.

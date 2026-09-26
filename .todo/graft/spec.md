# Graft in every project the harness works on

Status: implemented

[Graft](https://github.com/trailhq/graft) indexes a repo into a code graph (tree-sitter,
no model, no key) that agents query instead of re-exploring: `graft map`, `ask`,
`skeleton`, `callers`, `grep`. Every project the harness works on should use it, through
each harness's own channel.

## Decisions

| # | question | decision |
|---|---|---|
| 1 | Where the wiring lives | In each project, as graft writes it: `graft init --agents agents claude` puts a fenced section in `AGENTS.md` (Hermes loads the AGENTS.md chain and follows it with the `graft` CLI) and `.mcp.json`, `.claude/skills/graft/`, `.claude/settings.json` hooks (Claude Code: MCP tools, hooks, skill). Committed in the project. |
| 2 | When a project is wired | Automatically, on the first delegated task in an unwired project: `route()` wires it and commits before recording the decision's commit. |
| 3 | Graft's LLM layer (`--deep`) | Not used: structural graph only, $0, refreshed on every query. |
| 4 | Telemetry | Off: `DO_NOT_TRACK=1` in the sandbox and in the Hermes process (so in every child it starts). |
| 5 | Out-of-repo writes | None: `--no-global`, and `--no-statusline` (headless runs have no statusline). |
| 6 | Version | Pinned once (`GRAFT_VERSION` in the Makefile) for both the host install and the sandbox image; with graft on PATH, `init` writes `.mcp.json` as `graft mcp`, so Claude Code's MCP server runs the pinned binary too. |

## Where graft runs

| who | how it reaches graft |
|---|---|
| orchestrator, coder, OpenRouter one-shot (Hermes) | `AGENTS.md` section → `graft` CLI in the sandbox image |
| Claude Code rung (`claude -p` on the host) | project `.mcp.json` (loaded by `-p` without a prompt), tools allowed by `mcp__graft` in `claude.allowed_tools`, project hooks and skill |
| `route()` wiring a project | the pinned host install, on PATH via the `locoder` wrapper |

## Safety rules for automatic wiring

- Only git checkouts under `claude.workdir_roots`, wired at the repository root.
- Skipped, with the reason in `route()`'s output, when any file graft would write has
  uncommitted changes or a merge or rebase is in progress.
- The commit contains only graft's files (`git commit --only`), so the user's other
  staged or unstaged work is untouched.
- If `init`, `build` or the commit fails, graft's changes are rolled back, the reason
  is reported, and that repo is not retried until Hermes restarts.

# 09: One local model, the orchestrator, as the local worker

Status: implemented (verify on alpha: `make install && make check`, then one delegated task)
Blocked by: none

## What to build

Revises spec decision 12. The Qwen3.6-35B-A3B coder preset goes. Laguna S 2.1, the
orchestrator (118B total, 8B active), becomes the only GPU model and the `coder` rung:
`delegation.model` points `delegate_task` children at it. The user chose results over
speed. Local tasks get a stronger model and run several times slower per token.

The VRAM the coder held goes to the orchestrator's quality, not its speed:

- **Context 64k → 128k** (`ctx-size`, `model.context_length`).
- **KV cache q4_0 → q8_0.** Laguna S is hybrid: 36 of its 48 layers use a 512-token
  sliding window (mainline `src/models/laguna.cpp`, SWA period 4). Only 12 layers'
  KV grows with the context, so 128k at q8_0 is about 3.3 GB.
- **Experts placed by llama.cpp's `--fit`**, not a hand-picked `n-cpu-moe`.
  `fit-target` 2560 MiB keeps the desktop headroom the old layout was measured to need.
- **Poolside's sampling** (`temperature` 0.7, `top-p` 0.95, `top-k` 20), and
  **reasoning on**. Hermes sends neither to a custom provider, so the preset sets them.
  `reasoning-budget` 8192 ends a reasoning loop.
- **A RAM prompt cache** (`cache-ram` 16 GiB) restores the parent's KV after a child
  has used the single slot.

Mistakes are caught before they reach alpha. `scripts/stack.py` holds the rules that
presets, `config.yaml` and `routing.yaml` must satisfy together. `make test` runs them
on the repo, `make check-offline` on the install. `make check` adds live checks:
- the router's echo of each preset (it ignores unknown keys with only a log line);
- each preset's load status;
- the orchestrator's real slot context;
- free VRAM.

## Acceptance criteria

- [x] No `coder` preset; `delegation.model` is `orchestrator`, with
  `max_concurrent_children` equal to its `parallel`.
- [x] `model.context_length` equals the orchestrator's `ctx-size` (131072).
- [x] `make test` fails on each mismatch `stack.py` guards; on the old two-model files it
  reports the hand-set layer counts that would switch `--fit` off.
- [x] The judge's `WORKER` text describes the orchestrator model, and `make test` fails
  when its context or step count drifts from the presets and `config.yaml`.
- [x] `make check` flags a preset key the router ignored and a slot context that differs
  from `context_length` (exercised against a fake router).
- [ ] On alpha: `make check` passes, and the startup log shows the KV cache at about
  3.3 GB and how many expert layers `--fit` kept on the GPU.
- [ ] On alpha: one delegated task runs as a child on the orchestrator, and the parent
  resumes from the prompt cache without re-reading its context.

## Notes

The rung keeps the name `coder`, so the ledger, the policy and the tools are unchanged. The
ledger does not record which model ran a coder attempt, so any earlier Qwen attempts mix
into `make report`'s coder figures. Keep `policy.local_threshold` strict until the report
shows the new worker's pass rate.

If startup fails to allocate the KV cache, the SWA assumption is the first suspect. Check
that the startup log reports a sliding window for Laguna S. Without one, 128k at q8_0
needs about 13 GB. Fall back to q4_0 and a smaller `ctx-size`, changing
`model.context_length` with it.

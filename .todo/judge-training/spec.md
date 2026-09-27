# Train the judge on this machine's own outcomes

Status: ready-for-agent

## Problem Statement

The routing judge decides whether a delegated task starts on the local model or on Claude
Code. Both judges predict that from general knowledge:
- `semif` is a small chat model;
- `julia` is Julia-1, a decision model trained on public benchmarks.

Neither has seen this machine's briefs, this local model, or which of those tasks it
actually finishes.

The ledger records every checked outcome, but only for tasks the routing judge chose to
send to the local model. That is a handful a week, all of them the judge's own picks. The
data is too thin to train on, and too biased to judge any judge fairly.

## Solution

Three steps, each usable on its own:

1. **Replay.** Overnight, re-run tasks that Claude Code already solved on the local model,
   in throwaway worktrees, and record whether the acceptance check passed. That gives many
   more labels, including on tasks the judge would never have picked.
2. **Calibrate.** From about 30 labels, fit two numbers that rescale a judge's P(local) to
   match what actually passed. The model is unchanged, the fit is cheap and safe, and it
   works for either judge.
3. **Fine-tune.** From about 200 labels, fine-tune Julia-1 on the "will the local model
   finish this?" question. Evaluate on held-out recent tasks, and promote the new checkpoint
   only if it beats the current one.

`make report` already scores the judges against labels, so each step's effect shows there.

## User Stories

1. As the user, I want past Claude-solved tasks re-run on the local model overnight, so that
   the ledger learns what the local model can do without my waiting on it during the day.
2. As the user, I want replays never to touch my checkouts, so that an overnight run cannot
   damage work in progress.
3. As the user, I want replays to use the local model only when I am not, so that my
   daytime sessions keep the single slot.
4. As the user, I want each replayed task run at most once per local-model configuration,
   so that the nights are spent on new labels.
5. As the user, I want replays labelled with the same acceptance check and stray-change rule
   as `/verify`, so that a replay label means what a real label means.
6. As the user, I want replay labels kept apart from real attempts, so that the weekly
   report's pass rates and costs still describe real work.
7. As the user, I want `make report` to count replay labels in the judge comparison, and to
   say how many came from replays, so that I can see the selection bias shrink.
8. As the user, I want to calibrate either judge with one command, so that its P(local)
   means what it says before I retune the thresholds.
9. As the user, I want calibration to refuse to run on too few labels, so that two numbers
   fitted to five tasks don't steer my routing.
10. As the user, I want calibration kept only if it improves held-out recent tasks, so that
    it can't make a judge worse.
11. As the user, I want the raw and the calibrated P(local) both kept, so that I can refit
    later without losing history.
12. As the user, I want `make train-julia` to fine-tune Julia-1 on my labels and tell me
    whether the result beats the current model on held-out tasks, so that I promote on
    evidence.
13. As the user, I want training on the CPU, so that it never competes with the orchestrator
    for VRAM.
14. As the user, I want a promoted checkpoint pinned by hash and served by the same service,
    so that nothing else changes when the judge improves.
15. As the user, I want to roll back to the published Julia-1 with one command, so that a bad
    promotion is cheap to undo.
16. As the user, I want every checkpoint to record its training window, label count, metrics
    and base model, so that I know what is running.
17. As the user, I want the briefs to stay on this machine, so that training never sends my
    code or tasks anywhere.

## Implementation Decisions

- **The label.** One bit per task: did the local model's run pass the acceptance check,
  with no changes outside the brief's allowed paths. Real coder attempts (`route_outcome`)
  and replays both produce it. There is no ground truth for difficulty, so only the "will it
  finish?" (`local`) question is trained or calibrated.
- **Replay reuses the bake-off.** Same task selection (clean recorded commit, an acceptance
  command in the brief), throwaway worktree, sandboxed check and stray-change rule. The
  worker is the local model rather than an OpenRouter one: a one-shot Hermes run on the
  local provider, with the worker preamble and the routing-child guard, like the OpenRouter
  rung.
- **Which tasks replay.** Only tasks some rung verified: a task nothing solved may have a
  broken check. Tasks the local model already ran are skipped. The newest go first, up to
  a nightly budget.
- **When replays run.** A user systemd timer at night. Each task starts only if the
  orchestrator's slot is idle, so a live session always wins; a replay in progress
  finishes.
- **Where replay labels live.** A new ledger table, one row per replay: the decision, when,
  a fingerprint of the local worker (preset model and settings), the verified bit, duration
  and notes. Real attempts stay untouched.
- **Scoring uses both label sources.** The report's judge comparison, calibration and
  training read real coder attempts and replays, and say how many of each they used.
- **A time split, always.** Calibration and training fit on older labels and evaluate on the
  newest fifth. A random split would leak one project's near-duplicate briefs across both
  halves.
- **Calibration is Platt scaling per judge:** P' = sigmoid(a·logit(P) + b). It is stored as
  state beside the ledger, not in the repo, and applied to that judge's P(local) in either
  role. The recorded verdict keeps the raw value next to the calibrated one. It must still
  load back into a `Verdict`, because `escalate()` reads it after a mid-run limit hit.
- **Minimum data:** 30 labels to calibrate and 200 to fine-tune. Below that, the commands say
  how many more are needed and change nothing.
- **Fine-tuning.** It runs in a container built from the Julia-1 image, on the CPU, as a few
  epochs at a low learning rate with early stopping on the held-out split. It uses the
  upstream training entry point if the model repository ships one. Otherwise it uses a
  plain loop: cross-entropy on Julia-1's logits against the target option, with inputs
  encoded by the upstream serializer so training and serving agree.
- **Promotion and rollback.** A checkpoint is promoted only if it beats the current model
  (after calibration) on the held-out split by a margin, and is then served by the same
  service. Checkpoints live in the state directory with a manifest: base commit, training
  window, label counts, metrics and weights hash. The image can be built from a local
  checkpoint (hash-checked against its manifest) or from Hugging Face, which is the
  rollback. A promotion invalidates Julia-1's calibration, so it is refitted.

## Testing Decisions

- Tests exercise behaviour through the highest seam, as the existing ones do:
  - the replay command against a scratch repo and a fake one-shot worker, like the
    bake-off tests;
  - calibration and training on seeded ledgers;
  - the service through its HTTP handler.
- Replay: tasks selected and skipped as specified; checkouts and real attempts unchanged;
  labels written with the worker fingerprint; a busy slot defers the task.
- Calibration: refuses below the minimum; a deliberately miscalibrated seeded judge comes
  out better on the held-out split; raw values kept; stored verdicts still load.
- Fine-tuning: the training loop runs against stand-ins for torch and the upstream package
  (as the self-test does); the promotion rule keeps the old checkpoint when the new one is
  worse; a local checkpoint whose hash differs from its manifest fails the build.
- Prior art: `tests/test_bakeoff.py` (worktrees, fake workers, the ledger left unchanged)
  and `tests/test_julia.py` (the handler, the stand-in runtime, the report comparison).

## Out of Scope

- Training or calibrating the difficulty question: there are no labels for it.
- Fine-tuning SemIf's chat model.
- Replaying on OpenRouter models, which is what `make bakeoff` is for.
- Changing the routing policy or its thresholds automatically. The report informs; the user
  retunes.
- Sharing labels or checkpoints off this machine.

## Further Notes

- The replay is useful before any training. It removes most of the selection bias from
  `make report`'s comparison of the two judges as they are.
- Replays of briefs whose checks need untracked build state (virtualenvs, node_modules)
  fail for the same reason bake-off replays do; the notes should say so, so those labels can
  be excluded.
- Julia-1's published evaluation used 1,024 tokens. Briefs longer than that are neither
  judged nor trained on.

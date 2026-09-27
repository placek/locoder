# 10: Julia-1 as a shadow judge

Status: implemented (verify on alpha: `make install && make enable && make check`, then a few weeks of `make report`)
Blocked by: none

## What to build

Supersonic Labs' Julia-1 (released 2026-09-26) is a 144M-parameter decision model:
- built on mmBERT-small, Apache-2.0;
- runs on a CPU;
- answers `choice`, `score` and `noul` questions with a probability per option;
- its typed interface is the one `judge.py` already uses.

It may judge better than SemIf on a 4B chat model, but none of its benchmarks resemble our
question. So it runs beside the judge and is only recorded: the ledger will show which
predicts the labels better.

- **A sidecar, `julia/`.** The upstream PyTorch runtime, which ships inside the model
  repository, runs on the CPU with the evaluated settings: 1,024 tokens, a 512-token head,
  strict encoding. It sits behind a stdlib HTTP server on `127.0.0.1:8089`, runs as the
  `locoder-julia` user service, and uses two threads so the orchestrator's CPU experts keep
  the rest. The build:
  - downloads the model;
  - checks the weights' SHA-256;
  - installs the repository's own requirements, if any;
  - answers a self-test.

  At run time the image is offline and read-only.
- **`JuliaBackend`** in `judge.py` maps `Noul`/`Score`/`Choice` to Julia's typed questions
  and reads the probabilities back. `Noul` gains optional `criteria`, since Julia-1 scores
  described outcomes far better than bare yes/no. Coverage is 1 by construction.
- **`route()`** asks the shadow after the judge and stores its verdict or error, and the
  latency, in `decisions.shadow`. Anything it raises is caught: it never reaches the
  decision or the tool's output.
- **`make report`** scores judge, shadow and the base rate on the same labelled coder
  attempts, using Brier score, log loss and near-certain picks.
- **`make check`** checks the sidecar's `/health` and asks it a sanity question.

## Acceptance criteria

- [x] Every `route()` with `shadow_judge.enabled` records the shadow's verdict, or its error,
  beside the judge's, and the decision is the judge's alone.
- [x] A shadow that is down, refuses the brief or raises anything is recorded as an error;
  routing carries on.
- [x] Julia's typed questions and answers map both ways, including a Score whose levels do
  not start at 0; a malformed answer is a JudgeError.
- [x] The sidecar's handler passes Julia's refusals (400) and crashes (500) back and keeps
  serving; the self-test calls the runtime as julia-mlx's parity tests call it.
- [x] The weights are pinned by hash: a re-upload fails `fetch.py`.
- [x] `make report` compares the two judges and the base rate on the same labels.
- [x] Older ledgers gain the `shadow` column.
- [ ] On alpha: `make julia` builds (it needs Hugging Face and download.pytorch.org),
  `make check` shows the sidecar's commit and a sane verdict, and the ledger fills.
- [ ] After a few dozen labelled coder attempts: decide from `make report` whether to
  switch judges.

## Notes

Written against julia-mlx's source (zm2231/julia-mlx), which mirrors the upstream API
and tests parity with it: `julia.inference.load_model(dir, device="cpu", max_length,
head_length, strict_encoding)` and `julia.typed.predict_typed(engine, state, questions)`.
This container cannot reach Hugging Face, so the real runtime has not run here. The
build's self-test is the first place a mismatch would show.

The labels still come only from tasks the judge sent to the coder, so the comparison
inherits that selection bias. The overnight replay of Claude-solved tasks on the local
model would remove it, and would also give a fine-tuning set for Julia-1.

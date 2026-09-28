# 02: Calibrate a judge's P(local) on the ledger

Status: ready-for-agent
Blocked by: none (better with 01's labels)

## What to build

`make calibrate JUDGE=semif|julia` fits Platt scaling (two numbers) to the judge's recorded
P(local), from either of its roles, against the labels: real coder attempts, plus replays
once 01 exists. It fits on older labels and evaluates on the newest fifth. It keeps the fit
only if the held-out Brier score improves, and prints before and after.

The fit is stored as state beside the ledger and applied to that judge's P(local) wherever
it is asked. The recorded verdict keeps the raw P(local) too. `make calibrate JUDGE=… RESET=1`
removes a fit.

Below 30 labels it changes nothing and says how many more are needed.

## Acceptance criteria

- [ ] Fewer than 30 labels: no fit is written, and the output names the shortfall.
- [ ] On a seeded ledger whose judge is systematically overconfident, the fit lowers the held-out Brier score and is kept; on a well-calibrated one it is not kept.
- [ ] A kept fit changes that judge's P(local) in routing and in shadow verdicts, and both raw and calibrated values are recorded.
- [ ] Verdicts recorded with a fit still load back into a Verdict, so `escalate()` after a mid-run limit hit keeps working.
- [ ] `make report` shows whether each judge is calibrated, and scores it as it is now applied.
- [ ] RESET removes the fit, and the next route uses the raw value.

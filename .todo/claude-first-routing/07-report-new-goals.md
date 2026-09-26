# 07: Report against the new goals

Status: ready-for-agent
Blocked by: 02, 05

## What to build

`make report` answers the questions the new policy is judged on (spec decision 7):

- **Days without Claude Code**, per weekly window: how much of each week Claude Code
  was locked out. The main signal that too little is being offloaded.
- **Verified pass rate** per rung, the floor that must not drop.
- **Coder pass rate as a fallback**, kept apart from its pass rate on near-certain
  tasks: coder attempts made while Claude Code was unavailable.
- **OpenRouter spend** per week and per month.
- Judge calibration as today, now read against the two thresholds (0.85 near-certain,
  0.3 fallback).

The pacer's sections (limit-hit spend, learned budget) go.

## Acceptance criteria

- [ ] On a ledger seeded with lockouts, coder attempts both with and without Claude Code available, and OpenRouter attempts with cost, each figure above prints correctly.
- [ ] Fallback and near-certain coder attempts are counted separately.
- [ ] No output refers to pace or a weekly budget.

# 11: Choose which judge routes, and run the other as a background shadow

Status: implemented (verify on alpha: `make check`, then swap `judge.backend` / `judge.shadow` once and route a task)
Blocked by: 10

## What to build

The user picks the routing judge with one setting, and the other judge becomes the shadow,
or is not asked at all:

```yaml
judge:
  backend: semif | julia    # route() acts on its verdict
  shadow: julia | semif | none   # asked too, only recorded
```

It is read on the next routing call, with no restart. This replaces ticket 10's
`shadow_judge` block; the Julia-1 service moves to `julia.base_url` and `julia.timeout_s`.

How the shadow behaves:
- It is asked **after** the decision is recorded, on a background thread, and its verdict
  (or error) joins the decision's row. A SemIf shadow takes seconds on the CPU and must
  not delay `route()`.
- It is asked even when the routing judge is down; a failure to start it is swallowed.
- It is never shown to the orchestrator.
- A shadow equal to the backend counts as none.

Each decision records which judge made its verdict. `make report` scores each judge on
every verdict it gave, in either role, so the comparison survives a switch, while the
calibration bands show only the routing judge's own verdicts. `routing_status` shows both
roles. `make check` checks only the judges that are asked. `make test` refuses:
- an unknown judge name;
- a shadow equal to the backend;
- the retired `shadow_judge` block.

It requires the `judge` preset only while SemIf is asked.

## Acceptance criteria

- [x] `judge.backend: julia` routes on Julia-1 through the default factories; `semif` on the preset.
- [x] The shadow is recorded after `route()` returns; a slow shadow does not hold it up.
- [x] A failing shadow, or a runner that cannot start, is recorded or ignored; `route()` answers.
- [x] `shadow: none`, or a shadow equal to the backend, is not asked.
- [x] The report compares both judges across a switch; calibration shows only the routing judge.
- [x] Older ledgers gain `judge_backend`; rows without it count as `semif`.
- [x] `make test` refuses unknown names, a shadow equal to the backend, and `shadow_judge`, and
  needs no `judge` preset when only Julia-1 is asked.
- [ ] On alpha: `make check` passes with both judges, and after a swap.

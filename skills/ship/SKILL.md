---
name: ship
description: "Run a feature end to end through the other skills: grill the idea, spec and ticket it, write failing acceptance tests, implement outside-in, verify, document, report. Resumes from `.todo/<feature>/` when re-run."
---

# Ship

Drive one piece of work from a loose idea to a verified, documented, reported change, by running the other skills in order. Ship itself decides nothing about the code; it decides **which skill runs next**, records where you are, and holds the gates.

```
vision  →  plan  →  acceptance  →  implement  →  verify  →  document  →  report  →  finish
 (gate)     (gate)     (gate)
```

Three gates stop for the user: after **vision** (shared understanding), after **plan** (tickets approved), and after **acceptance** (the acceptance tests that frame the work are written and red). From there ship runs to the end without check-ins. Only four things stop it: an irreversible or destructive operation; a security-sensitive action; a side effect outside the repo (a merge, a push, a publish); or a plan so broken that every path forward is a guess. Everything else is a **ruling**: decide, record it in the ship log as `Ruling: <what> - <why> - <cost if wrong>`, keep going.

## State and resume

Everything lives in `.todo/<feature-slug>/`, next to the `spec.md` and tickets the other skills write. Ship keeps its own section at the bottom of `spec.md`:

```markdown
## Ship

Branch: feature/<slug>

- [x] vision (shared understanding reached)
- [x] plan (4 tickets approved)
- [ ] acceptance
- [ ] implement
- [ ] verify
- [ ] document
- [ ] report
- [ ] finish

Ruling: ...
```

On invocation, resolve the feature slug from the argument (or ask for one line describing the work and derive it). If `.todo/<slug>/spec.md` exists, read its `## Ship` section and resume at the first unchecked stage; do not redo checked stages. If it does not exist, start at vision. Announce where you are: "Resuming `invoice-payments` at implement, 2 of 4 tickets done."

Before touching code, make sure you are on `feature/<slug>` (create it from the current branch if needed). Never implement on `main`/`master`.

## Stages

### 1. Vision

Size the request first and say the size out loud so the user can correct it:

- **Spike**: a feasibility question. Answer it cheaply, keep nothing, report the finding. Ship ends here.
- **Small**: a well-scoped change to a flow that already exists (a flag, a small endpoint, a one-file fix). Grill briefly, then write a single ticket in step 2 instead of a spec and breakdown.
- **Feature**: everything else. Full pipeline.
- **Too big for one map**: several independent subsystems, or more than one session's worth of decisions. Stop and suggest `wayfinder`; ship one of its resolved slices later.

Load two skills with `skill_view` (`grilling` and `domain-modeling`) and grill until the frontier is empty. Explore the codebase yourself for facts; the decisions are the user's. When you believe you have shared understanding, write it back in a few lines (what, for whom, done-when, out of scope) and **stop for the gate**: the user confirms or corrects.

Tick `vision`.

### 2. Plan

Load the `to-spec` skill (`skill_view`): synthesise the conversation into `.todo/<slug>/spec.md`. Then load `to-tickets`: break it into tracer-bullet tickets with `Blocked by:` edges. `to-tickets` already quizzes the user on the breakdown; their approval of it is the **second gate**. For a small change, write one ticket by hand from the `to-tickets` template and skip the quiz.

Append the `## Ship` section to `spec.md`, tick `plan` with the ticket count, commit `.todo/` on the branch.

### 3. Acceptance

This is **outside-in TDD** (the double loop): an outer loop of acceptance tests written now from the spec, and an inner loop of unit-level red-green per ticket in step 4. Load the `tdd` skill (`skill_view`) for the seam vocabulary and the test rules; both loops follow them.

**Acceptance tests** are the frame for the implementation: they say what the software must do for a person, at the highest seam, and they all start red. They are not a specification of the code's shape.

1. Pick the seam. The highest one available: the HTTP or CLI surface, the application service, the UI flow. The spec's Testing Decisions should name it. If the project has no seam an end-to-end test can drive cheaply (no integration harness, no test database, no app-level entry point), building that seam is the first ticket; do not fall back to unit tests and call them acceptance tests.
2. Write one failing acceptance test per user story in the spec (one per ticket where stories are too coarse). Each asserts an **outcome** the story's actor can observe, in the domain's vocabulary, with expected values taken from the spec, never derived from the code. Mocks only at system boundaries.
3. Run them. Every one must fail, for the right reason (the behaviour is missing, not a typo). Record which tests cover which tickets under `## Tests` in each ticket file.
4. Show the list and the red run, and **stop for the gate**: the user confirms these are the right outcomes, or changes them.

Tick `acceptance`.

**Hardening.** Acceptance tests are soft on detail and hard on outcome. As implementation fixes the details the spec left open (an exact message, a field name, the shape of a fixture, the order of two steps), update the test to match; that is the test hardening, and it is expected. Loosening an assertion because the code "works" is not: that turns the frame into a tautology. The rule that separates the two is that **every change to an acceptance test after the gate is a ruling** in the ship log (`Ruling: acceptance 03 now expects net not gross - spec said "amount", chose net - cost if wrong: one column`). A silent edit to an acceptance test is the one forbidden move.

### 4. Implement

Load the `implement` skill (`skill_view`); it keeps ticket `Status:` lines current. Work the frontier in number order. Each ticket is the inner loop of `tdd`, driven by its acceptance tests:

- Run the ticket's acceptance tests; they are red. Take the first unit-level behaviour needed to move them: write the failing unit test, see it fail, write the minimum code, see it pass, commit. Repeat.
- When the ticket's acceptance tests go green, the ticket is `done`, with its acceptance criteria ticked. Not before.
- Refactoring waits for step 5. Acceptance-test changes are rulings (see Hardening).

**Delegating tickets.** A ticket bigger than a surgical edit goes through `delegate` (`skill_view`), one ticket per brief: the ticket file, its acceptance tests as the acceptance command, `spec.md`, `CONTEXT.md` if present, and an instruction to load `tdd`. Nothing from this conversation. `route()` decides whether the local coder or a paid rung takes it; the tickets run one after another, because the coder serves a single slot. When one returns, do not trust its report: read `git diff` for its commits and run the ticket's acceptance tests yourself before marking it done — then record the outcome, as `delegate` says.

Do not stop between tickets. Plan defects are rulings. If a test will not go green and the cause is not obvious, load `diagnosing-bugs` rather than guessing.

Tick `implement` when every ticket is `Status: done`.

### 5. Verify

Load the `verify` skill (`skill_view`) and hold to it for the rest of the run.

1. Run the project's full checks: typecheck, lint, test suite, build. All green, evidence in this session.
2. Walk the spec's acceptance criteria and user stories one by one against the running code. Tick each in the ticket files; a criterion you cannot demonstrate is not met.
3. Load `code-review` (`skill_view`) against the branch's merge base. Fix every Standards or Spec finding that would affect a person using the software: reproduce with a failing test, make it pass, rerun the full checks. This is also where refactoring belongs (`refactor` if it is more than a local cleanup). Findings you decline become rulings. Minor polish is listed for the report, not fixed.
4. Repeat 1-3 until a pass produces no fixes.

Tick `verify` with the commands and results (`tests 84/84, tsc clean, build ok`).

### 6. Document

Documentation is part of the change, not an afterthought.

- Update every doc the change made stale or incomplete: README, `docs/`, CHANGELOG, API reference, CLI help, config examples, comments that described the old behaviour. Add docs for anything a user needs to know to use the feature.
- Load `domain-modeling` (`skill_view`) once more: any term the work introduced goes into `CONTEXT.md`; any decision that met the three ADR tests gets its ADR.
- Commit.

Tick `document`.

### 7. Report

Load the `to-report` skill (`skill_view`) and write a **change summary** for the person who asked for the feature, saved to `.todo/<slug>/report.md`. If the run stopped short (a spike, a blocker), write a progress report instead.

Set `spec.md`'s `Status:` to `done`, tick `report`, commit.

### 8. Finish

Run the full checks once more on the final tree (evidence, again). Then present exactly this and wait:

```
Shipped <feature> on feature/<slug>: N commits, all checks green, report at .todo/<slug>/report.md.

1. Merge into <base> locally
2. Push and open a pull request
3. Leave the branch as it is
```

Carry out the choice. For 1, run the checks again on the merged result before deleting the branch. For 2, follow the repo's PR conventions and report the URL. Tick `finish`.

End with two lists in your final message, both exhaustive: **Rulings I made** (from the ship log, each with its cost if wrong) and **Left for later** (deferred review findings, follow-up ideas, remaining smells). They are the only place the decisions you took on the user's behalf reach them.

## What ship does not do

- It does not re-grill on resume: a checked stage is done.
- It does not skip a gate because the answer seems obvious. Present, then wait.
- It does not merge, push, or publish without the user choosing it in step 8.
- It does not fix bugs or refactor beyond the tickets. Those are new tickets, or a `refactor` session.

## Credits

The stage shape, gates, rulings, the four stops, resume-from-ledger, and the finish menu are adapted from [obra/superpowers](https://github.com/obra/superpowers) (MIT): `brainstorming`, `writing-plans`, `executing-plans`, `subagent-driven-development`, `finishing-a-development-branch`. The stages themselves are this repo's skills.

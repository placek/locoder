---
name: refactor
description: Safe, behaviour-preserving refactoring of a module or codebase in small test-gated steps. Use when the user says "refactor", "clean up", "tidy", "simplify", "extract", "this is a mess", or asks to improve code structure without changing what it does.
---

# Refactor

Improve the structure of existing code without changing what it does. A refactoring is a sequence of small, named, behaviour-preserving steps, each proven by the tests before the next begins. This is the *change the shape* discipline; adding behaviour is `tdd`, and judging a finished diff is `code-review`.

## The contract

1. **Behaviour is preserved.** Outputs, side effects, errors, and public contracts stay identical. If a step would change any of them, it is a feature or a bug fix, not a refactoring: stop and ask.
2. **One operation per step.** Each step is a single named technique ("Extract Function", "Rename", "Move Method") that can be reviewed in a minute and reverted on its own.
3. **Tests gate every step.** Green before, green after. Red means revert the step and take a smaller one; never fix forward.
4. **No hat-switching.** Do not fix bugs, add features, or "while I'm here" anything. Note them for a follow-up.

Read `CONTEXT.md` (if it exists) so new names use the project's vocabulary, and respect ADRs in the area. Load the `codebase-design` skill (`skill_view`) when the question is where a seam belongs or how deep a module should be; use its terms (module, interface, depth, seam, adapter).

## Process

### 1. Baseline

- Read every file you intend to change. Read, don't skim.
- Find the test command (project config, README, CI). Run it. **The suite must be green before any edit.** If it is red, stop and report; don't refactor on a red baseline.
- If the target has no tests, say so. Either write **characterization tests** first (they assert what the code does *now*, right or wrong, at the public interface) or get the user's explicit acknowledgement that verification is manual. Never proceed silently.
- Commit or stash unrelated changes so `git status` is clean. Every green step will be a commit.

### 2. Diagnose

If the user named a specific operation ("extract this", "rename X"), skip to step 3.

Otherwise walk the code against the **smell baseline** in the `code-review` skill (load it with `skill_view`; the list is in its "Identify the standards sources" section). Rank what you find:

- **Blocker**: prevents understanding or safe change (a 200-line function, a god class)
- **Major**: real duplication, coupling, or complexity
- **Minor**: naming, small structure

Present the ranked list, each item with the smell, the location, and the technique you'd apply. Wait for the user to pick.

### 3. Plan

Break the agreed work into atomic steps, blockers first. Each step must leave the code compiling and the tests runnable. State the plan before touching anything:

```
1. Extract calculateDiscount() from processOrder()
2. Extract applyTaxes() from processOrder()
3. Rename processOrder() -> placeOrder() (12 call sites)
```

Flag any step that crosses a **red line** and get explicit confirmation before including it:

- a public or exported signature, name, or module path
- a serialised shape: JSON keys, DB schema, wire format, file format, env var names
- concurrency: lock order, async boundaries, where side effects sit relative to awaits
- the error contract: what is thrown or returned, and when
- a symbol whose callers you haven't fully mapped (grep first; dynamic calls and external packages won't show up)

### 4. Execute, one step at a time

For each step:

1. State the operation: "Extract Function: lines 67-89 of `processOrder` into `calculateDiscount`".
2. Apply exactly that change.
3. Run the fast static check first if the project has one (typecheck, linter, compile), then the tests. Show the command and the result.
4. **Green**: commit with the technique as the message (`refactor: extract calculateDiscount`). Move to the next step.
5. **Red**: revert the step (`git checkout -- .` or undo the edit). Say which test failed and why. Split the step smaller or reconsider it. Do not proceed until the tree is green.

Re-read a file before editing it if you haven't looked at it in a while; in long sessions the code may have moved under you.

### 5. Verify and wrap up

- Run the **full** test suite once more at the end, plus the typecheck/lint, even if every step was green (steps may have used a subset).
- Tests should pass **without having been modified**. If you had to change a test to get green, you changed behaviour: call it out.
- Summarise: each operation applied, files touched, what got shorter or simpler. Then list what you deliberately did *not* touch (remaining smells, bugs noticed, follow-on ideas) so the user can open tickets for them.

## Technique cheatsheet

| Smell | Technique |
|---|---|
| Long function | Extract Function; Replace Temp with Query; Decompose Conditional |
| Duplicated code | Extract Function / Class; Pull Up Method |
| Large class, divergent change | Extract Class along the axes of change |
| Long parameter list, data clumps | Introduce Parameter Object; Preserve Whole Object |
| Primitive obsession | Replace Primitive with Object; Replace Type Code with Class |
| Repeated switch on type | Replace Conditional with Polymorphism, or one shared map |
| Nested conditionals | Replace Nested Conditional with Guard Clauses |
| Magic numbers / strings | Replace Magic Literal with Constant |
| Feature envy, message chains | Move Function; Hide Delegate |
| Middle man, lazy class | Inline Function / Class; Remove Middle Man |
| Speculative generality, dead code | Delete it; Collapse Hierarchy; Remove Parameter |
| Refused bequest | Replace Subclass with Delegate |

Full catalogue: [refactoring.com/catalog](https://refactoring.com/catalog/).

## When not to refactor

- Code that works and nobody needs to change: no benefit, only risk.
- Code about to be deleted or rewritten.
- No tests and no way to add them, and the user won't accept manual verification.
- A deadline the user cares about more than this cleanup: note the debt and stop.

## Credits

Distilled from Martin Fowler's *Refactoring* via three MIT-licensed skills: the process and red lines from [MuhiminOsim/code-refactoring-skill](https://github.com/MuhiminOsim/code-refactoring-skill), the smell/technique tables from [Tyler-R-Kendrick/agent-skills](https://github.com/Tyler-R-Kendrick/agent-skills/blob/main/skills/dev/craftsmanship/refactoring/SKILL.md), and the rules and checklist from [github/awesome-copilot](https://github.com/github/awesome-copilot/blob/main/skills/refactor/SKILL.md).

---
name: verify
description: Evidence before claims. Use before saying anything is done, fixed, passing, or ready, and before committing, merging, or opening a PR.
---

# Verify

No completion claim without fresh evidence from this session. "Should pass", "looks right", "the agent said it worked" are not evidence; a command you ran and whose output you read is.

## The gate

Before claiming any status:

1. **Identify** the command that proves the claim (test suite, typecheck, lint, build, a reproduction of the original bug).
2. **Run** it, in full, now. Not a subset, not an earlier run.
3. **Read** the whole output: exit code, failure count, warnings that matter.
4. **Compare** with the claim. If it does not match, state the real status with the evidence. If it does, state the claim *with* the evidence.

Only then make the claim.

| Claim | Needs | Not enough |
|---|---|---|
| Tests pass | test command, 0 failures, this run | previous run, partial run |
| Typecheck / lint clean | the tool's output, 0 errors | tests passing |
| Build succeeds | build command, exit 0 | lint passing |
| Bug fixed | the original reproduction now passes, and the test failed before the fix | code changed |
| Regression test works | seen red, then green | passes once |
| Subagent finished | `git diff` / `git log` shows the change; its tests run by you | its report says "done" |
| Spec met | each acceptance criterion checked one by one | tests passing |

## Rules

- Run the project's **full** checks at least once before the final claim, even if every step along the way was green: typecheck, lint, test suite, build, whichever exist.
- A test you never saw fail proves nothing about the fix. For a bug or a review finding, write the test, watch it fail, then make it pass.
- Long output goes to a file; read its tail and grep for failures. Never truncate and assume.
- Wording does not matter: "done", "fixed", "ready", "works now", a satisfied tone, all need the same evidence.
- If you cannot run the proof (no test command, no environment), say so plainly and say what was and was not verified. Do not round up.

## Credits

Distilled from [`verification-before-completion`](https://github.com/obra/superpowers/blob/main/skills/verification-before-completion/SKILL.md) in obra/superpowers (MIT).

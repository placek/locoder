---
name: triage
description: Move issues in `.todo/` through a state machine of triage roles, categorise, verify, grill if needed, and write agent-ready briefs.
---

# Triage

Move issues in the `.todo/` directory through a small state machine of triage roles.

An issue is a markdown file: either a standalone `.todo/<slug>.md` or a file inside a feature directory `.todo/<feature-slug>/`. Its roles are plain lines under the title:

```markdown
# <Issue title>

Status: needs-triage
Type: bug
```

Discussion and triage notes are appended to the bottom of the file under a `## Comments` heading, newest last.

## Reference docs

- [AGENT-BRIEF.md](AGENT-BRIEF.md): how to write durable agent briefs
- [OUT-OF-SCOPE.md](OUT-OF-SCOPE.md): how the `.out-of-scope/` knowledge base works

## Roles

Two **category** roles, recorded on the `Type:` line:

- `bug`: something is broken
- `enhancement`: new feature or improvement

Five **state** roles, recorded on the `Status:` line:

- `needs-triage`: maintainer needs to evaluate
- `needs-info`: waiting on the reporter for more information
- `ready-for-agent`: fully specified, ready for an AFK agent
- `ready-for-human`: needs human implementation
- `wontfix`: will not be actioned

Past triage, the implementation skills move an issue on to `in-progress` and then `done`. Triage never sets those two.

Every triaged issue should carry exactly one category role and one state role. If an issue has more than one `Status:` line, flag it and ask the maintainer before doing anything else.

State transitions: an issue with no `Status:` line normally goes to `needs-triage` first; from there it moves to `needs-info`, `ready-for-agent`, `ready-for-human`, or `wontfix`. `needs-info` returns to `needs-triage` once the reporter replies. The maintainer can override at any time; flag transitions that look unusual and ask before proceeding.

## Invocation

The maintainer invokes `/triage` and describes what they want in natural language. Interpret the request and act. Examples:

- "Show me anything that needs my attention"
- "Let's look at `.todo/login-timeout.md`"
- "Move login-timeout to ready-for-agent"
- "What's ready for agents to pick up?"

## Show what needs attention

Scan every markdown file under `.todo/` (for example `grep -rL '^Status:' .todo` and `grep -rl '^Status: needs-' .todo`) and present three buckets, oldest first (by git history, or file modification time):

1. **No status**: never triaged.
2. **`needs-triage`**: evaluation in progress.
3. **`needs-info` with new comments since the last triage notes**: needs re-evaluation.

Show counts and a one-line summary per item. Let the maintainer pick.

## Triage a specific issue

1. **Gather context.** Read the full issue file (body, roles, `## Comments`, and its git history). Parse any prior triage notes so you don't re-ask resolved questions. Explore the codebase using the project's domain glossary, respecting ADRs in the area. Run two checks against the codebase: (a) **redundancy**: search for an existing implementation of the requested behavior by domain concept (not just the request's wording), and report where you looked. If found, it's an already-implemented `wontfix` (step 5). (b) **prior rejection**: read `.out-of-scope/*.md` and surface any that resembles this request.

2. **Recommend.** Tell the maintainer your category and state recommendation with reasoning, plus a brief codebase summary relevant to the request (including whether it's already implemented). Wait for direction.

3. **Verify the claim.** Before any grilling, check that the claim holds up. For a bug, reproduce it from the reporter's steps. Report what happened: confirmed (with code path), failed, or insufficient detail (a strong `needs-info` signal). A confirmed verification makes a much stronger agent brief.

4. **Grill (if needed).** If the request needs fleshing out, load two skills with `skill_view` (`grilling` and `domain-modeling`) and grill it into shape a round of questions at a time, sharpening domain terms and updating `CONTEXT.md`/ADRs inline as decisions land.

5. **Apply the outcome** by editing the `Status:` and `Type:` lines, then:
   - `ready-for-agent`: append an agent brief under `## Comments` ([AGENT-BRIEF.md](AGENT-BRIEF.md)).
   - `ready-for-human`: same structure as an agent brief, but note why it can't be delegated (judgment calls, external access, design decisions, manual testing).
   - `needs-info`: append triage notes (template below).
   - For `wontfix`, the comment depends on *why*:
     - **Already implemented**: the change already exists in the codebase. Point to where it lives; do **not** write to `.out-of-scope/` (that KB is for *rejected* requests, not built ones).
     - **Rejected (bug)**: append a short explanation.
     - **Rejected (enhancement)**: write to `.out-of-scope/` and link to it from a comment ([OUT-OF-SCOPE.md](OUT-OF-SCOPE.md)).
   - `needs-triage`: set the role. Optional comment if there's partial progress.

## Quick state override

If the maintainer says "move login-timeout to ready-for-agent", trust them and set the role directly. Confirm what you're about to do (role changes, comment), then act. Skip grilling. If moving to `ready-for-agent` without a grilling session, ask whether they want to write an agent brief.

## Needs-info template

```markdown
### Triage Notes

**What we've established so far:**

- point 1
- point 2

**What we still need from you:**

- question 1
- question 2
```

Capture everything resolved during grilling under "established so far" so the work isn't lost. Questions must be specific and actionable, not "please provide more info".

## Resuming a previous session

If prior triage notes exist in the issue file, read them, check whether the reporter has answered any outstanding questions, and present an updated picture before continuing. Don't re-ask resolved questions.

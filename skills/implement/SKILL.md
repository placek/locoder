---
name: implement
description: "Implement a piece of work based on a spec or set of tickets."
---

Implement the work described by the user in the spec or tickets. These usually live in `.todo/<feature-slug>/`: a `spec.md` plus numbered ticket files.

Work the tickets in order, taking only ones whose `Blocked by:` tickets are all `Status: done`. Set a ticket's `Status:` line to `in-progress` when you start it, tick its acceptance criteria as they pass, and set it to `done` when it is finished.

Load the `tdd` skill (`skill_view`) and use it where possible, at pre-agreed seams.

Run typechecking regularly, single test files regularly, and the full test suite once at the end.

Once done, load the `code-review` skill (`skill_view`) to review the work.

Commit your work (including the updated `.todo/` files) to the current branch.

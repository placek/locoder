---
name: to-report
description: "Turn this session (or one part of it) into a plain-language report for a non-technical reader: progress report, change summary, how-to, or explainer."
---

# To Report

Like `to-agent`, but the reader is a **person**, not an agent: a product owner, a customer, a manager, a teammate who only follows this project from a distance. You are the one responsible for the work done in this session, reporting to someone with a stake in it but not in the code.

Work from what is already in the conversation and its artifacts (commits, diffs, `.todo/` files, specs, ADRs). Do **not** interview the user about the subject: you were there. Synthesise.

## 1. Pin the send

Ask at most one round, and only for what the user's request left open:

- **Who is it for?** Name a person or role. "Stakeholders" is too vague: a customer and a product owner care about different things.
- **What shape?** One of the shapes below. If the user described the purpose instead, pick the shape and say which.
- **Which part?** The whole session, or one aspect of it (one feature, one decision, one problem).

If the user passed arguments, treat them as the answers and skip the questions.

## 2. Write the headline first

One sentence the reader would keep if they read nothing else. Then the **so-what**: why it matters to *them* (what they can now do, what changed for them, what it costs or saves). If you cannot write the headline, you do not yet know what you are reporting: reread the session.

## 3. Build the inverted pyramid

Headline, so-what, body, then risks and asks, then detail. Cut from the bottom: if the reader stops halfway, the important part has already landed.

- **Outcomes, not activities.** "Customers can now pay by invoice" beats "implemented the invoice module and its tests." What you did is supporting evidence, one line at most.
- **Plain language.** Use the project's own words from `CONTEXT.md` (the vocabulary the reader already shares) and nothing from the code: no file paths, function names, ticket numbers, branch names, tool names, or acronyms without a gloss. If a technical term is unavoidable, explain it once in a parenthesis.
- **Honest status.** State plainly what is done, what is partly done, and what is not started. If something went wrong or slipped, it goes near the top, not sandwiched between wins. Yellow that never changes is red.
- **One explicit ask, or none.** If you need a decision, information, or a review, say exactly what and by when, with a recommendation. If nothing is needed, say "no action needed."
- **No hedging, no selling.** Cut "just", "really", "exciting", "we might possibly". Calibrate: confident where the work is verified, plain about what is untested.

## 4. Shapes

**Progress report** (where things stand):

```
# <Project or feature>: progress, <date>

**Status:** On track / At risk / Off track
**Headline:** <one sentence>

## Done
- <outcome, in the reader's terms>

## In progress
- <item>: <how far, what is left>

## Not started / dropped
- <item>: <why, if it was dropped>

## Risks
- <risk>: <what it would mean for the reader>; <what we are doing about it>

## Needs from you
- <specific ask, by when, with a recommendation> (or "No action needed")

## Next
- <what happens next, and when the next update comes>
```

**Change summary** (what is different now and why):

```
# <What changed>

**Headline:** <one sentence>

## Before
<how it worked or looked for the reader>

## After
<how it works now; what they can do that they could not>

## Why
<the reason the change was made, in the reader's terms, and any trade-off accepted>

## What stays the same
<reassurance about what this did not touch>

## Anything to watch for
<known limitations, follow-ups, or "nothing">
```

**How-to** (instructions a reader can follow without help):

```
# How to <do the thing>

<One paragraph: when you would do this and what you end up with.>

## Before you start
- <what you need>

## Steps
1. <one action per step, in the reader's words, with what they should see afterwards>

## If something goes wrong
- <symptom>: <what to do>
```

**Explainer** (documentation of how something works, for reference):

```
# <Topic>

<What it is and why it exists, two or three sentences.>

## How it works
<walk through it in the order the reader meets it, plain language, no internals>

## Terms
- **<term>**: <one-line meaning>

## Common questions
- **<question>?** <answer>
```

Adapt headings to the case; the structure matters more than the exact labels.

## 5. Cut, check, save

- A first draft is usually a third too long. Remove every sentence that adds no information for *this* reader.
- Reread as the recipient: is the headline understandable without the session in your head? Is anything missing that they need? Is anything present that they do not?
- Redact secrets and personal data.
- Save to `<shape>-<slug>.md` in the current directory unless the user named a place, and report the path. Offer to reshape it for the channel (email, chat message, slides) if asked.

## Credits

Distilled from Anthropic's [`stakeholder-update`](https://github.com/anthropics/knowledge-work-plugins/blob/main/product-management/skills/stakeholder-update/SKILL.md) (Apache-2.0: audience formats, status framework, risk communication) and rampstack's [`stakeholder-communication`](https://github.com/rampstackco/claude-skills/blob/main/skills/stakeholder-communication/SKILL.md) (MIT: the five questions, inverted pyramid, cutting rules, failure patterns).

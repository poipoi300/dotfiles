---
name: council
description: Use only when the user mentions council, or when a hillclimb result cannot be verified with hard tests. Ordinary implementation and feature reviews use the relevant reviewer subagent, not council.
---

# Council

Gather independent agents' judgments about the same artifact. Council evaluates;
the caller decides and implements changes. It runs when the user asks for it, or
to judge a [Hillclimb](../hillclimb/SKILL.md) candidate that hard tests cannot
verify.

## Set the review contract

Identify the exact candidate, user goal, audience, constraints, and evidence to
inspect. Give the candidate a stable identifier: a commit, content hash, or named
snapshot. Reviewers must inspect the actual artifact; visual judgments require
visual access. Missing access means unverified, never an invented assessment.

Use the user's rubric and requested scale. Otherwise derive a short rubric from
the brief and state it. Define what weak, acceptable, and excellent mean for
each scored criterion. Only add numeric scores when useful or requested.
An evaluation can provide critique without a pass target; do not invent one.
When a verdict is needed, fix the acceptance threshold, mandatory constraints,
and aggregation rule before collecting reviews. Keep these stable across rounds.

When the user asks for a council review of code, the caller first fixes known
issues, addresses open PR comments, and runs the relevant checks, so reviewers
judge the current implementation rather than defects already known. Skip fixes
to code the user wants replaced. Inside Hillclimb, such a fix is a new
candidate and counts as a round.

For code and feature reviews, include the intended behavior, diff and surrounding
code, relevant contracts, and available check results. Assign coverage for
correctness, integration and regressions, and maintainability; include security
or UX where relevant. Passing tests are evidence, not a complete review. Council
opinions cannot override a failed required check or verified contract violation.

## Choose real reviewers

This workflow calls for independent reviewer agents. Follow the active harness's
delegation rules, user-selected providers/models, and any restriction on agents.

- In T3, discover providers, models, and options through
  `orchestrator_capabilities` before the first `delegate_task`. Use native
  delegation where T3's rules require it; use `delegate_task` for cross-provider
  selection, explicit settings, worktree isolation, or requested background work.
  Follow a delegated task's returned `next`; do not poll running tasks. Each
  re-review is a fresh task. Never use top-level threads as reviewers.
- Outside T3, use the harness's supported agent mechanism and available models.
  A shared role brief is not proof of a registered agent or connected provider.
- Default to three independent reviewers with complementary perspectives. Use
  different model families or providers when available, permitted, and useful;
  separate runs of one model are acceptable, with reduced diversity disclosed.
  Prefer the least costly competent choices. Respect supplied reviewer counts,
  concurrent work, deadline, and quota; do not hardcode model IDs or prices.
  Fix the panel before collecting judgments; any agreed smaller panel still
  needs at least two independent reviewers.
- If delegation is unavailable or prohibited, report that an independent Council
  could not be formed. A direct assessment can still help, but does not count as
  multiple reviews. Do not silently replace an unavailable requested provider.

Reviewers are read-only and must not delegate. Run them concurrently where
supported and affordable, otherwise sequentially. Send each the same candidate,
rubric, target if any, and raw evidence, plus a distinct focus such as domain
quality, user impact, or failure modes. Do not send the builder's self-rating,
effort, desired verdict, or other reviewers' opinions. A numeric target is a
boundary to assess, not a score to aim to produce.
Pass each reviewer its remaining time and allocated quota. Stop or cancel
outstanding reviews when the caller's limit or cancellation applies.

Do not wait idle. Check each review against the artifact as it arrives and
decide what to act on while the others run. Do not change the candidate until
every review has completed. Council sets each reviewer's output contract; the
`reviewer` role's default report format does not apply.

Ask each reviewer for criterion-level judgments and requested scores; concrete
evidence or locations; important strengths, defects, and uncertainties; whether
the target is met; and the highest-impact improvements. Reviewers should identify
real defects without manufacturing objections or awarding unsupported praise.

## Synthesize the evidence

Keep individual judgments visible. Separate agreement, dissent, and missing
coverage. Check factual objections against the artifact; do not vote away a
verified mandatory failure or let an unsupported objection become a veto.

Review findings are advisory. The code owner or builder decides which findings
are true, relevant, and worth acting on, and may accept, defer, or reject them
based on its own inspection and judgment. Reviewer agreement does not make a
suggestion binding. Briefly explain material rejections or deferrals, such as an
incorrect premise, an out-of-scope concern, or an unfavorable tradeoff. Guard
the scope: repeated rounds tend to grow the change one cheap-looking suggestion
at a time. Decline extra tests, rare-case guards, added compatibility, and small
performance work the goal does not need, and list them as follow-ups. Rejecting
advice does not erase a confirmed required-check failure, change the agreed
acceptance criteria, or rewrite the Council's recorded judgments.

Unless the caller specifies another rule, require at least two independent
completed reviews and a strict majority of completed reviewers meeting every
required criterion. For numeric targets, also require the median score on each
required criterion to meet its threshold. Report the score range and dissent;
an overall average cannot conceal a failed mandatory criterion. A required check
failure or substantiated blocking defect means fail. Missing required evidence,
fewer than two reviews, or unresolved material factual uncertainty means
unverified. Missing requested reviewers or perspectives must be disclosed and
make the verdict unverified unless a demonstrated mandatory failure already
establishes fail. Do not shrink the panel after seeing results to obtain a pass.

Return a compact evaluation that a human or Hillclimb can consume:

- **Candidate and coverage:** identifier, artifacts inspected, completed/requested
  reviewers and their provider/model and focus, missing evidence.
- **Rubric and results:** criterion judgments, individual scores when requested,
  medians/ranges, required-check results, agreement and dissent.
- **Verdict:** pass, fail, or unverified against the stated target, with reasons.
  If no pass target was requested, give the assessment and say acceptance was
  not evaluated.
- **Blockers and next changes:** substantiated failures and a short ranked list
  of concrete improvements.

Council finishes with the evaluation. In a Hillclimb run, return it to the loop
owner; do not start a second improvement loop or alter its limits or rubric.

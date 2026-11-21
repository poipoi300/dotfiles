---
name: hillclimb
description: Use only when the user says hillclimb.
---

# Hillclimb

Pursue a goal through repeated changes and evaluation. Keep the best supported
result and continue until the acceptance criteria are met or a supplied deadline,
round limit, or task quota is reached, whichever happens first. A plateau,
an unsuccessful attempt, or "good enough" below the target is not a stop condition.

## Establish the run

Turn the user's request into a short contract and state it before iterating:

- Goal, scope, constraints, artifact, and what counts as an improvement.
- Acceptance criteria and verification method. Use actual tests or metrics where
  they establish success. Use [Council](../council/SKILL.md) for subjective or
  otherwise unverifiable criteria. Mixed goals require both. A measured number
  (speed, latency, throughput) needs a harness vetted with the
  [benchmark-checklist](../benchmark-checklist/SKILL.md) skill before the
  baseline, and a model score one vetted with [ml-design](../ml-design/SKILL.md);
  then freeze the harness so every run prints its error and work counts.
- Supplied limits: absolute deadline with timezone, maximum rounds, and quota
  allocation with unit and accounting source. A duration starts when the run
  starts unless the user says otherwise. Keep unspecified limits unset.

Infer routine criteria from the brief and existing project contracts; ask only
when ambiguity would materially change the goal or acceptance. Do not invent a
score target, deadline, round cap, or spending allocation. An open-ended request
such as "make this as good as possible for 20 minutes" needs an improvement
rubric, not an invented success threshold; run until its supplied limit.

Record a baseline evaluation before changing the artifact. Baseline evaluation
does not consume a round, but does consume time and quota. A round is one
candidate improvement attempt and its evaluation; failed attempts count too.
Track attempts started and completed so interruption cannot hide consumed rounds.
If the baseline already meets every acceptance criterion, finish immediately.
For a benchmark number or a model score, also reserve time and quota for the
final confirmation (see Keep or reject); like the baseline, it uses no round.

For Council verification, load Council and agree on its fixed rubric, reviewers,
aggregation, and target before the first review. Each changed candidate needs a
fresh review; the builder's own rating is not verification. Keep the same review
contract across rounds so scores remain comparable.

## Make limits enforceable

Check limits before the baseline, before each round, before costly work or agent
dispatch, and after each evaluation. A zero or already-exhausted limit means no
new work. Stop starting work at the deadline or when rounds/quota are exhausted;
do not finish another round just to improve the result. Use supported timeouts
and cancellation for in-flight work where available, including Council children.
Do not start work expected to exceed the remaining allowance. Report any
unavoidable overrun rather than hiding it.

Quota includes the loop owner and every builder, verifier, and Council reviewer.
Count rejected and failed attempts too. Divide the remaining allowance among
parallel children; do not give each child the entire shared remainder.
Use current task-attributable token/cost telemetry or the user's explicit
accounting rule. Keep units and provider buckets distinct. Account-wide quota
snapshots show shared headroom; concurrent work and resets prevent treating their
delta as exact task spend. Stale or missing usage is unknown, never zero or
unlimited. Do not translate tokens, currency, or percentage points without an
established conversion. On this installation, the existing
`~/.agents/usage/budget.py` can supply native usage observations; it does not
establish exact task allocation or spend.

If a supplied hard quota cannot be measured or conservatively bounded, disclose
the missing source and request an enforceable accounting rule or alternative
limit before incurring further work under that quota. If no quota was supplied,
missing telemetry alone does not halt authorized progress. Never change providers
or extend any limit to evade an allocation, restriction, or user stop.

## Iterate

1. **Choose a change.** Use the latest evaluation to pick the most promising
   concrete improvement. The code owner or builder may accept, defer, or reject
   review findings based on its own assessment of their truth, relevance, and
   tradeoffs; it need not implement every suggestion, even if reviewers agree.
   Briefly record the reason for material rejections or deferrals without
   changing the agreed success criteria or recorded verification results.
   Keep the current best candidate recoverable without
   disturbing unrelated user work. Start each attempt from that candidate unless
   a deliberate exploration has a better reason.
2. **Build.** Make the scoped change. Hillclimb itself does not require a builder
   subagent; use the active harness's rules if delegating. Give any child the
   remaining limits and scope. The loop owner controls the run and its budget.
3. **Evaluate.** Identify the exact candidate and run the relevant checks. Use
   Council for criteria those checks cannot establish. Required checks and
   subjective acceptance must both pass on this same candidate. A skipped,
   unavailable, or failed verifier cannot establish success.
4. **Keep or reject.** Compare against the best candidate under the fixed rubric.
   Accept supported improvement without introducing mandatory regressions;
   otherwise retain the previous best and record what was learned. Do not
   average away regressions or accept an unverified claim of improvement. If
   evaluators disagree materially, investigate the evidence within the limits.
   Unmet acceptance criteria may improve incrementally: fixing some baseline
   failures can be progress even while other required checks still fail.
   A benchmark number improves only when question 5 of the
   benchmark-checklist calls the candidate better, with the error count not
   higher and the work count equal; a changed work count voids the comparison.
   A model score improves only when the gap clears ml-design's noise floor
   across seeds or folds. Council-judged criteria keep Council's aggregation.
   The best of many rounds is partly luck, so before reporting it, confirm the
   final best against the baseline in a fresh run, or re-score it on data no
   round selected on. The confirmed number is the one reported and the one that
   decides success. If the confirmation cannot run within the limits, report
   the best as unconfirmed and likely optimistic.
5. **Record and continue.** Log the round, attempted change, candidate identifier,
   results, best candidate, elapsed time, and known quota usage. Check acceptance
   and all limits again. If neither success nor a limit applies, start the next
   useful attempt without asking for permission to continue.

When progress stalls, change the approach, decomposition, or hypothesis. Do not
repeat the same failed edit unchanged, lower the target, switch metrics, or coach
Council toward higher scores. A necessary rubric change must be explicit and
requires re-evaluating the incumbent before comparing new candidates.

Keep concise progress updates during sustained work: best result, remaining gap,
attempts used, and remaining limits where known. Keep the run ledger with the
project when it must survive a context reset; preserve it rather than restarting
the round count, clock, or accounting.

## Finish honestly

On success, return the exact passing candidate with its supporting evaluation,
which is the confirmation when one applies.
At a deadline, round cap, or quota limit, preserve and return the best evaluated
candidate, say the target remains unmet if so, and identify the binding limit.
Do not spend beyond that limit to obtain a nicer final evaluation; report the
latest completed evidence and any unfinished attempt separately.

User cancellation, lost authorization, or a concrete external blocker can also
interrupt execution. Name the blocker and the input needed; do not label it
success or create a fake limit. Follow the harness's continuation and wait rules:
an asynchronous child wakeup is not task completion. Do not claim work continues
after the turn unless a supported continuation is actually active.

The final report gives the artifact, best result versus target, verification
coverage, rounds started/completed, elapsed time, measured quota or its unknown
status, and the exact reason the run stopped. Hillclimb authorizes persistence
within the task; it does not expand permission to publish, deploy, or spend.

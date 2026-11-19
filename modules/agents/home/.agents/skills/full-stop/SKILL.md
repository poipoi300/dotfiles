---
name: full-stop
description: Trigger this when the user explicitly asks to run full-stop and take the work to a ready-to-merge PR. An emphatic "Full stop." is not a request.
metadata:
  source: https://github.com/t3dotgg/tstack/tree/11e37a74d0a6868c121cd0910ab2b3bd9873b92a
---

# Full Stop

Do the same work as `full-send`, but stop with the PR ready for the user to merge.
Never merge it.

If the user did not clearly request full-stop, do not assume autonomy. If they
did, everything below applies.

Read [full-send](../full-send/SKILL.md) and follow its workflow, proof rules,
review, and babysitting steps. An explicit full-stop request authorizes that
work without a separate full-send request, including full-send's testing
permission. Follow that section for which test methods are allowed. Its merge
permission does not apply. The overrides below take precedence.

## No merging

You may commit, push, rebase, fix conflicts, and use `--force-with-lease` on
this task's branch. Never merge, enable auto-merge, enter a merge queue, push
to the default branch, bypass branch protection, or delegate those actions.

Disable existing auto-merge and remove the PR from any merge queue before
work begins; check again before each push. Report these changes. If you
cannot clear auto-merge or the merge queue, stop and ask (full-send
"Asking the user"). Do not proceed.

## Stop at the gate

Use full-send's merge gate as the readiness check. A watcher exit is not the
gate; run the live check in the `vcs` skill's babysit.md. Replace full-send's merge and final
report steps with a handoff. Proof and review must cover the final diff;
recheck behavior changes and meaningful conflict fixes before handoff.

Fetch the target branch and check the live PR's head, base, checks, reviews,
and mergeability. Confirm it is open, not a draft, and not set to merge
without the user. If someone else merged it, report that instead.

- **Ready to merge:** the merge gate passes. Leave the PR open and unmerged.
- **Waiting for required approval:** only human approval remains. Confirm
  that this is the actual blocker.
- **Blocked:** explain what prevents readiness, what you tried, and what is
  needed. Follow full-send's rules for asking the user.

In T3 Code, call `unwatch_pull_request` before the handoff or a question.
Report the PR link, final head, verdict, proof, reviewer outcome, and remaining
risks. State whether the PR is still open and unmerged.

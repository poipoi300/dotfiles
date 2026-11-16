---
name: vcs
description: Version control conventions - commit message format and commit cadence. Load when committing, writing a commit message or PR title, or deciding how to split work into commits.
---

# Version control

## Commit Messages
Imperative mood, capitalized subject, ≤50 chars, no trailing punctuation. Body only when it adds information the subject can't (wrap at 72, blank line after subject, don't restate the subject). Output only the commit message — no commentary, no diff.

Example: `Fix leaderboard ties to break by submission time`.

Pull request titles and descriptions follow the `file-pr` skill. Where a repository has no title convention of its own, use the commit subject rules above.

## Commit Cadence
- One logical change per commit. A feature spanning layers commits per layer (API, UI, tests), not as one blob.
- New projects commit the scaffold skeleton before building on it.
- A shared contract (a schema, an interface) is committed before parallel work starts against it, so every branch builds on the same version.
- Commit incrementally throughout; never one giant final commit.

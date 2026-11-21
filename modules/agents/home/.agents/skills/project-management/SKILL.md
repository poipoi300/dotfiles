---
name: project-management
description: Use when planning or tracking work in a project, which lives in GitHub issues and a GitHub Projects board. That covers breaking a feature, phase or roadmap into tasks, writing a multi-session plan, filing, updating or closing an issue, setting priorities, milestones or board status, starting or finishing an issue, or answering what is next or what is left. Also use when recording follow-up or deferred work, or a bug left for later. Use it too when tempted to write a TODO or FIXME comment, a TODO file, or a status checklist in a doc. Commits and PRs are the vcs skill.
---

# Project management

Work is tracked in GitHub. An issue is a unit of work, and a GitHub Project
board is the plan. Nothing else holds work or its status: no TODO, FIXME, XXX
or HACK comments, no TODO files, and no checklists or status tables in
`PLAN.md`, the README or notes. Docs explain design and decisions. Status
lives in issues.

## Issues

- **What to file:** work that leaves the current change undone becomes an
  issue in the repository that owns it. That includes deferred scope,
  follow-ups, review findings declined as out of scope, and missing
  infrastructure.
  - A bug in something the user owns follows the global "Fixing what you
    find broken" rule.
  - File an issue for such a bug only when that rule leaves it unfixed:
    blocked, waiting on the user, or a separate change.
- **In code:** when a code site needs a pointer to known future work, the
  comment states the constraint and links the issue
  (`See owner/repo#123.`). There is never a TODO marker, linked or not:
  forge's `no-todo` hook rejects TODO, FIXME, XXX and HACK comments and
  TODO files.
  - When you touch a file that has TODO comments, move each one to an issue.
  - Replace the comment with the link only after the issue exists.
- **Search before filing:** `gh issue list --state all --search "<terms>"`.
  Add to an existing issue instead of filing a duplicate.
- **Title:** the outcome or the problem, in plain words. The issue number is
  its identifier, so put no plan codes in titles.
- **Body:** open with the problem and why it matters. Then state the
  intended change. End with `**Done when:**` and observable acceptance
  criteria, normally the tests that will pin the behaviour. Cite evidence
  with commit permalinks, not branch links.
- **Secrets and comments:** the vcs skill's rule on secrets and private
  details applies to issue titles, bodies and attachments. Its "Comments on
  GitHub" section governs issue comments.
- **Relationships are native fields, never prose:**
  - A breakdown uses sub-issues: `gh issue create --parent <n>`, or
    `gh issue edit <n> --parent <m>`.
  - Ordering uses dependencies: `--blocked-by` and `--blocking` on create,
    `--add-blocked-by` and `--add-blocking` on edit.
  - Kind uses the issue type (`--type`) where the repository has types.
    Otherwise it uses a label.
  - A release target is a milestone (`--milestone`).
  - Never write "Depends on:" lines or checklists of issue links in a body.
- **Closing:** a change closes its issue from the PR description with
  `Fixes #<n>`, or `Fixes owner/repo#<n>` across repositories. Each issue
  needs its own keyword, and the PR must target the default branch. Close an
  issue without a change only with a comment giving the reason
  (`gh issue close <n> --reason "not planned" --comment "…"`).

## The board

- **One board per product:** create one GitHub Project (v2) owned by the
  user's account. Link it to every repository it covers
  (`gh project link <number> --owner <login> --repo <repo-name>`). Write the
  project's README to say what it tracks.
- **Fields:** use the built-in Status (Todo, In Progress, Done) for kanban
  columns. Add `Priority` (single select: P0, P1, P2) and `Size` (single
  select: S, M, L) when the work needs ranking. Add an `Iteration` field only
  when the user works in timeboxes. Keep each fact in one field, and use the
  synced issue data (assignee, labels, milestone, type) instead of
  duplicating it.
- **Views:** a Board grouped by Status, a Table sorted by Priority, and a
  Roadmap when there are dates or iterations. The hierarchy view shows
  sub-issue trees.
- **Automation:** the built-in workflows set an item to Done when its issue
  closes or its PR merges. Enable "Auto-add to project" with the filter
  `is:issue`. Each auto-add workflow watches one repository, and GitHub Free
  allows one, so point it at the busiest repository. Add issues from the
  others, and any that existed before auto-add, with `gh project item-add`.
- **Auth:** the `gh` token needs the `project` scope. If a `gh project`
  command reports a missing scope, ask the user to run
  `gh auth refresh -s project`. It opens a browser sign-in only they can
  complete.

### Commands

```sh
gh project create --owner @me --title "<product>"
gh project field-create <number> --owner @me --name Priority \
  --data-type SINGLE_SELECT --single-select-options "P0,P1,P2"
gh issue create --title "…" --body-file <file> --project "<product>" --parent <n>
gh project item-add <number> --owner @me --url <issue-url>
gh project item-list <number> --owner @me --format json
gh project item-edit --id <item-id> --project-id <project-id> \
  --field-id <field-id> --single-select-option-id <option-id>
```

`gh project field-list <number> --owner @me --format json` gives the field
and option ids. `gh project view <number> --owner @me --format json` gives
the project id. Read the ids every time; never assume them.

## Planning

- **Approval first:** when the user asks for a plan of work that will span
  more than one session, present the breakdown in the conversation. File it
  once the user agrees, or straight away when they asked for it to be filed.
- **Plan shape:** an epic issue per goal, a sub-issue per change small
  enough for one PR, `blocked-by` links for ordering, a milestone for the
  release each belongs to, and Priority on the board.
- **Plans that stay in chat:** a plan asked for only in the conversation,
  for work done in this session, stays in the conversation.
- **The V1 milestone:** forge projects read V1 progress from a GitHub
  milestone (forge skill), so the V1 milestone holds every issue V1 needs.

## Working the board

- **Starting:** before starting an issue, assign it
  (`gh issue edit <n> --add-assignee @me`) and set its Status to In Progress.
- **Finishing:** the PR's closing keyword moves it to Done. Do not move it
  by hand.
- **Reporting:** when asked what is next or what is left, read the board
  and the open issues (`gh project item-list`, and
  `gh issue list --milestone`), and answer from them, not from repository
  docs.
- **New findings:** file work found along the way as you find it, and link
  it from the current issue or PR. Don't let it stay in conversation memory.

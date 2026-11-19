"""Steer git work to where ``fix-route`` says it goes, without the agent knowing the route.

PreToolUse denies a commit, push, or ``gh pr create`` that misses the route; each reason is
the whole instruction for an agent that has never heard of routes. PostToolUse reminds the
agent to carry a change, or to ask about carrying it, after its upstream PR opens.

A repository configured ``agents.route pr`` (in its own config) is the user's own and integrates
through pull requests: the guard allows ``gh pr create`` onto that repository from a branch, and
feature-branch pushes. It denies a commit, merge, cherry-pick, rebase, ``am``, revert or pull on
the default branch, even mid-operation, and a push to it. Only a fast-forward from origin's
default branch is let through. A route that is configured explicitly but cannot be applied fails
closed: commits, pushes, merges and replays, and ``gh pr create`` are denied with the router's error.

Threat model: the guard steers well-meaning agents away from the wrong integration route. It is
not a security boundary against a deliberately crafted command, and it is not a Git or gh parser;
it recognises the realistic spellings of the commands that miss a route.

Offline only: a hook has a few seconds and must not call the network. Anything this module
cannot parse, or any repository it cannot classify without a configured route, is allowed.
"""

from __future__ import annotations

import json
import os
import re
import shlex
from dataclasses import dataclass
from pathlib import Path

import fix_route

_NOT_A_REPO = object()
_AMBIGUOUS = object()
_ENV = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
_SEPARATORS = {"&&", "||", "|", ";", "\n", "&"}
_REDIRECTS = {">", ">>", "<", ">|"}
_GIT_VALUES = {"-c", "--exec-path", "--namespace", "--config-env", "--super-prefix", "--list-cmds"}
_PUSH_VALUES = {"--repo", "--receive-pack", "--exec", "-o", "--push-option"}
_GH_VALUES = {
    "--repo", "--hostname", "--title", "--body", "--body-file", "--assignee", "--reviewer", "--label",
    "--project", "--milestone", "--head", "--base", "--template", "--recover", "--jq",
}
# Short options of ``gh pr create`` that take a value, alone or at the end of a bundle such as ``-fR``.
_GH_SHORT_VALUES = set("RtbFarlpmHBT")
# ``git rebase`` options whose value is the next word.
_REBASE_VALUES = {"--onto", "-s", "--strategy", "-X", "--strategy-option", "-x", "--exec", "--empty",
                  "--whitespace", "-C"}
_IN_PROGRESS = ("MERGE_HEAD", "SQUASH_MSG", "CHERRY_PICK_HEAD", "REVERT_HEAD", "rebase-merge", "rebase-apply")
# Subcommands that can change the checked-out branch before a later command in the same script runs.
_MOVES = {"switch", "checkout", "worktree", "branch", "rebase", "reset", "stash", "bisect"}
# Subcommands that create or replay commits onto the checked-out branch, besides ``commit``.
_REPLAYS = {"merge", "cherry-pick", "rebase", "am", "revert", "pull"}
# Flags that leave an in-progress replay or print help instead of replaying anything.
_INERT = {"--abort", "--quit", "--show-current-patch", "--edit-todo", "-h", "--help"}
_GIT_MUTATION = re.compile(r"\b(?:push|commit|merge|cherry-pick|rebase|am|revert|pull)\b")
_INHERIT = object()
_COMMIT_VALUES = {"-m", "--message", "-F", "--file", "-C", "--reuse-message", "-c", "--reedit-message",
                  "--fixup", "--squash", "--author", "--date", "-t", "--template", "--cleanup",
                  "--trailer", "--pathspec-from-file", "-S", "--gpg-sign"}
_FORK_ROUTES = ("upstream", "upstream+carry")


@dataclass(frozen=True)
class Push:
    directory: Path
    remote: str | None
    refspecs: tuple[str, ...]
    delete: bool
    tags: bool
    all_branches: bool
    dry_run: bool = False


@dataclass(frozen=True)
class GhCreate:
    repo: str | None
    head: str | None = None


@dataclass(frozen=True)
class Commit:
    directory: Path


@dataclass(frozen=True)
class Replay:
    directory: Path
    subcommand: str
    args: tuple[str, ...]


def guard(payload: dict) -> dict | None:
    """Deny decision for a Bash PreToolUse payload, or None to leave the command alone."""
    try:
        return _guard(payload)
    except Exception:
        return None


def remind(payload: dict) -> dict | None:
    """Context for a Bash PostToolUse payload after an upstream PR opened, or None."""
    try:
        return _remind(payload)
    except Exception:
        return None


def _guard(payload: dict) -> dict | None:
    if payload.get("hook_event_name") not in (None, "PreToolUse"):
        return None
    script = _script(payload)
    if script is None:
        return None
    cache: dict[str, object] = {}
    moved = False
    for cwd, argv, gh_repo in script:
        decision = _consider(cwd, argv, cache, moved, gh_repo)
        if decision:
            return decision
        # After a branch switch the current branch is unknown until the command runs.
        moved = moved or _moves_branch(argv, cwd)
    return None


def _moves_branch(argv: list[str], cwd: Path | None) -> bool:
    found = _git_subcommand(argv, cwd or Path.cwd())
    if found is _AMBIGUOUS:
        return True
    return found is not None and found[1] in _MOVES


def _remind(payload: dict) -> dict | None:
    if payload.get("hook_event_name") != "PostToolUse":
        return None
    script = _script(payload)
    if script is None:
        return None
    output = json.dumps(payload.get("tool_response"), default=str).lower()
    if "already exists" in output:
        return None
    raw_cwd = payload.get("cwd")
    base = Path(raw_cwd) if isinstance(raw_cwd, str) and raw_cwd else None
    cache: dict[str, object] = {}
    for cwd, argv, _gh_repo in script:
        created = parse_gh(argv)
        if cwd is None or not isinstance(created, GhCreate) or created.repo is None:
            continue
        route = _cached_route(cwd, cache)
        if route is _NOT_A_REPO and base is not None:
            # Some harnesses report the directory after the command's own cd.
            route = _cached_route(base, cache)
            cwd = base
        if not isinstance(route, fix_route.Route) or route.route not in _FORK_ROUTES:
            continue
        if not route.upstream_repo or not fix_route.same_repo(created.repo, route.upstream_repo):
            continue
        slug = fix_route.github_slug(f"https://github.com/{route.upstream_repo}")
        if not slug or f"github.com/{slug}/pull/".lower() not in output:
            continue
        default = _fork_default(route, cwd)
        if route.route == "upstream" and route.source == "config":
            return None
        if route.route == "upstream+carry":
            context = (f"vcs: the fork carries its own changes, so it carries this one too: "
                       f"git switch {default} && git merge <this branch> && git push origin {default}. "
                       "Do not open a PR on the fork.")
        else:
            context = (f"vcs: the fork's {default} matches upstream and doesn't get this change. "
                       "Ask the user whether the fork should carry it until upstream merges; if yes: "
                       f"git config agents.route upstream+carry, then git switch {default} && "
                       f"git merge <this branch> && git push origin {default}.")
        return {"hookSpecificOutput": {"hookEventName": "PostToolUse", "additionalContext": context}}
    return None


def _script(payload: dict) -> list[tuple[Path | None, list[str], object]] | None:
    if payload.get("tool_name") not in (None, "Bash"):
        return None
    tool_input = payload.get("tool_input")
    if not isinstance(tool_input, dict):
        return None
    command = tool_input.get("command")
    if not isinstance(command, str) or not _may_match(command):
        return None
    raw_cwd = payload.get("cwd")
    base = Path(raw_cwd) if isinstance(raw_cwd, str) and raw_cwd else Path.cwd()
    return _parse_script(command, base)


def _may_match(command: str) -> bool:
    if "git" in command and _GIT_MUTATION.search(command):
        return True
    return "gh" in command and "pr" in command and "create" in command


def _consider(cwd: Path | None, argv: list[str], cache: dict[str, object], moved: bool = False,
              gh_repo: object = _INHERIT) -> dict | None:
    if not argv or cwd is None:
        return None
    push = parse_push(argv, cwd)
    commit = parse_commit(argv, cwd) if push is None else None
    replay = parse_replay(argv, cwd) if push is None and commit is None else None
    created = None if push is not None or commit is not None or replay is not None else parse_gh(argv)
    if push is None and commit is None and replay is None and created is None:
        return None
    if _AMBIGUOUS in (push, commit, replay, created):
        return None
    directory = (push.directory if isinstance(push, Push) else commit.directory if isinstance(commit, Commit)
                 else replay.directory if isinstance(replay, Replay) else cwd)
    route = _cached_route(directory, cache)
    if route is _NOT_A_REPO:
        return None
    if isinstance(route, fix_route.DefaultBranchUnknown):
        return _unknown_default(route, push, replay)
    if isinstance(route, fix_route.ConfiguredRouteError):
        return _configured_failure(route, directory, cache, moved, push, replay)
    # Login unknown: nothing here is a clear violation. A crashed decide() is not this route,
    # so the never-push-upstream rule below still applies to that case.
    if isinstance(route, fix_route.Route) and route.route == "unknown":
        return None
    known = route if isinstance(route, fix_route.Route) else None
    if isinstance(commit, Commit):
        if moved or not _commit_misses(known, directory):
            return None
        return _deny(_commit_reason(known, directory))
    if isinstance(replay, Replay):
        if moved or known is None or known.route != "pr":
            return None
        if not _replays_commits(replay, known.default_branch):
            return None
        if not _replays_onto_default(replay, known.default_branch):
            return None
        return _deny(_replay_reason(known))
    if isinstance(push, Push):
        if push.dry_run:
            return None
        if _pushes_upstream(push, directory, moved) and not _explicit_direct(directory, cache):
            return _deny(_upstream_push_reason(known))
        if known and known.route == "upstream" and _pushes_origin_default(push, directory, moved):
            return _deny(_carry_reason(known, directory))
        if known and known.route == "pr" and _pushes_origin_default(push, directory, moved):
            repo = known.origin_repo or "this repository"
            return _deny(f"vcs: {repo} integrates through pull requests, so never push its {known.default_branch}. "
                         "Push your branch instead (git push -u origin HEAD) and open a PR: gh pr create. "
                         "The PR merges on GitHub.")
        if known and known.route == "external" and _pushes_origin_default(push, directory, moved):
            repo = known.origin_repo or "this repository"
            return _deny(f"vcs: {repo} isn't the user's, so never push its {known.default_branch}. "
                         "Push your branch instead (git push -u origin HEAD) and open a PR: gh pr create. "
                         "If you can't push to it, fork it and push the branch to the fork.")
        return None
    if not isinstance(created, GhCreate) or known is None:
        return None
    if known.route == "external":
        return None
    if known.route == "pr":
        return _pr_create_decision(known, created, directory, moved, gh_repo)
    if known.route == "direct":
        if known.source != "config" and created.repo is not None and known.origin_repo \
                and not fix_route.same_repo(created.repo, known.origin_repo):
            # Offline, a fork without an upstream remote looks like the user's own repository.
            return None
        repo = known.origin_repo or "This repository"
        return _deny(f"vcs: {repo} is the user's own and takes no PRs. Commit on {known.default_branch} "
                     f"and push it: git push origin {known.default_branch}. If it is a fork, add its parent "
                     "(git remote add upstream <url> && git fetch upstream) and try again.")
    if known.route in _FORK_ROUTES and known.upstream_repo:
        if created.repo is not None and fix_route.same_repo(created.repo, known.upstream_repo):
            return None
        return _deny("vcs: this is a fork; the PR goes to upstream, not to the fork: "
                     f"gh pr create --repo {known.upstream_repo} ...")
    return None


def _configured_failure(error: fix_route.ConfiguredRouteError, directory: Path, cache: dict[str, object],
                        moved: bool, push: object, replay: object) -> dict | None:
    """An explicit route that cannot be applied: every mutation is denied with the router's error."""
    if isinstance(push, Push):
        if push.dry_run:
            return None
        # Pushing upstream is wrong without any route, so its own instruction still applies.
        if _pushes_upstream(push, directory, moved) and not _explicit_direct(directory, cache):
            return _deny(_upstream_push_reason(None))
    if isinstance(replay, Replay) and _inert(replay):
        return None
    problem = str(error).removeprefix("fix-route: ")
    return _deny("vcs: the configured route can't be applied, so nothing is committed, pushed, merged or "
                 f"opened until it is fixed: {problem}")


def _unknown_default(error: fix_route.DefaultBranchUnknown, push: object, replay: object) -> dict | None:
    """A configured route is stated in terms of origin's default branch, which cannot be read.

    Without it a commit on the default branch looks like any other, so deny rather than allow.
    """
    if isinstance(push, Push) and push.dry_run:
        return None
    if isinstance(replay, Replay) and (error.route != "pr" or not _replays_commits(replay, None)):
        return None
    return _deny(f"vcs: the default branch of origin is unknown, so the {error.route} route can't tell it from "
                 "a feature branch. Establish it first: git fetch origin && git remote set-head origin -a "
                 "(or git remote set-head origin <branch>), then retry.")


def _pr_create_decision(route: fix_route.Route, created: GhCreate, directory: Path, moved: bool,
                        gh_repo: object) -> dict | None:
    """``gh pr create`` in a ``pr`` repository goes to origin's repository, from a branch."""
    repo = route.origin_repo or "this repository"
    env = os.environ.get("GH_REPO") if gh_repo is _INHERIT else gh_repo
    targets = _pr_targets(created, env, directory)
    for target in targets:
        if route.origin_repo and fix_route.same_repo(target, route.origin_repo):
            continue
        shown = fix_route.github_slug(target) or target
        return _deny(f"vcs: {repo} integrates through pull requests, so a PR goes to it, not to {shown}. "
                     "Drop --repo/-R, GH_REPO and the gh default (gh repo set-default --unset), "
                     "then run: gh pr create")
    if route.origin_repo and not targets and _several_github_repos(directory):
        # gh picks the PR's base from its default, else from the remotes, and origin is not always its pick.
        return _deny(f"vcs: {repo} integrates through pull requests, but this repository has several GitHub "
                     "remotes and gh may pick another one as the base. Name origin: "
                     f"gh pr create --repo {route.origin_repo} (or set the default once: "
                     f"gh repo set-default {route.origin_repo}).")
    # Without --head, gh pushes the current branch for the PR.
    if created.head is None and not moved and fix_route.local_branch(directory) == route.default_branch:
        return _deny(f"vcs: {repo} integrates through pull requests, so open the pull request from a branch, "
                     f"not from its {route.default_branch}. git switch -c <branch>, commit there, "
                     "git push -u origin HEAD, then gh pr create.")
    return None


def _pr_targets(created: GhCreate, env: object, directory: Path) -> list[str]:
    """What ``gh pr create`` targets, as gh resolves it: ``--repo``, else ``GH_REPO``, else the default."""
    if created.repo is not None:
        return [created.repo]
    if env:
        return [str(env)]
    return _gh_default_targets(directory)


def _several_github_repos(directory: Path) -> bool:
    """True when the remotes point at more than one GitHub repository."""
    result = fix_route.git(directory, "remote")
    if result.returncode != 0:
        return False
    slugs = set()
    for name in result.stdout.split():
        url = fix_route.remote_url(directory, name)
        slug = fix_route.github_slug(url) if url else None
        if slug:
            slugs.add(slug.lower())
    return len(slugs) > 1


def _gh_default_targets(directory: Path) -> list[str]:
    """``gh repo set-default`` records its choice as ``remote.<name>.gh-resolved``: ``base`` or ``owner/repo``.

    gh reads the entry only for a remote that exists, so a stale one is not a default.
    """
    result = fix_route.git(directory, "config", "--get-regexp", r"^remote\..*\.gh-resolved$")
    if result.returncode != 0:
        return []
    targets = []
    for line in result.stdout.splitlines():
        key, _, value = line.partition(" ")
        name = key.removeprefix("remote.").removesuffix(".gh-resolved")
        # ``git remote get-url`` answers with the name for a section that has no url, so ask the config.
        configured = fix_route.git(directory, "config", "--get", f"remote.{name}.url")
        if configured.returncode != 0 or not configured.stdout.strip():
            continue
        value = fix_route.remote_url(directory, name) if value.strip() == "base" else value.strip()
        if value:
            targets.append(value)
    return targets


def _commit_misses(route: fix_route.Route | None, directory: Path) -> bool:
    """True when a commit would land on the default branch of a fork, someone else's repo, or a PR repo."""
    if route is None or route.route not in (*_FORK_ROUTES, "external", "pr"):
        return False
    if route.route == "pr":
        # No in-progress exception: nothing lands on a pr repository's default branch.
        return _on_default_branch(directory, route.default_branch)
    branch = fix_route.local_branch(directory)
    if not branch or branch != fix_route.remote_default(directory, "origin"):
        return False
    found = fix_route.git(directory, "rev-parse", "--absolute-git-dir")
    if found.returncode != 0 or not found.stdout.strip():
        return False
    git_dir = Path(found.stdout.strip())
    # Resolving a merge, rebase, cherry-pick, or revert on the default branch is how a fork
    # carries a change or syncs with upstream.
    return not any((git_dir / name).exists() for name in _IN_PROGRESS)


def _on_default_branch(directory: Path, default: str) -> bool:
    """True when the checked-out branch, or the branch a rebase is replaying onto, is ``default``."""
    branch = fix_route.local_branch(directory)
    if branch is not None:
        return branch == default
    found = fix_route.git(directory, "rev-parse", "--absolute-git-dir")
    if found.returncode != 0 or not found.stdout.strip():
        return False
    for state in ("rebase-merge", "rebase-apply"):
        try:
            name = (Path(found.stdout.strip()) / state / "head-name").read_text(encoding="utf-8").strip()
        except OSError:
            continue
        if name == f"refs/heads/{default}":
            return True
    return False


def _flags(replay: Replay) -> tuple[str, ...]:
    return replay.args[:replay.args.index("--")] if "--" in replay.args else replay.args


def _inert(replay: Replay) -> bool:
    """True for aborting, quitting and help, which replay nothing."""
    return any(token in _INERT for token in _flags(replay))


def _replays_commits(replay: Replay, default: str | None) -> bool:
    """False for aborting, quitting, help, and a fast-forward from origin's ``default`` branch.

    Any other merge or pull can bring unpublished commits onto the branch, ``--ff-only`` or not:
    ``git merge --ff-only feature`` advances the default branch to a commit nobody reviewed.
    """
    if _inert(replay):
        return False
    if replay.subcommand not in ("merge", "pull") or not _fast_forward_only(_flags(replay)):
        return True
    return not _syncs_from_origin_default(replay, default)


def _operands(args: tuple[str, ...]) -> list[str]:
    """Words that are not options. An option's value counts as an operand, which only ever denies."""
    found: list[str] = []
    for index, token in enumerate(args):
        if token == "--":
            found.extend(args[index + 1:])
            break
        if not token.startswith("-"):
            found.append(token)
    return found


def _syncs_from_origin_default(replay: Replay, default: str | None) -> bool:
    """``git pull`` with no operands or ``origin <default>``; ``git merge`` of origin's default branch."""
    operands = _operands(replay.args)
    if replay.subcommand == "pull":
        return not operands or (default is not None and operands == ["origin", default])
    if default is None or len(operands) != 1:
        return False
    # Resolving the operand as git does catches a local branch named ``origin/<default>``,
    # which wins over the remote-tracking ref, and ``@{u}``, which is only origin's when it is.
    found = fix_route.git(replay.directory, "rev-parse", "--symbolic-full-name", operands[0])
    return found.returncode == 0 and found.stdout.strip() == f"refs/remotes/origin/{default}"


def _replays_onto_default(replay: Replay, default: str) -> bool:
    """True when the replay rewrites or advances ``default``: the checked-out branch, or a rebase's branch operand."""
    if replay.subcommand == "rebase":
        branch = _rebase_branch(replay.args)
        if branch is not None:
            return branch in {default, f"refs/heads/{default}"}
    return _on_default_branch(replay.directory, default)


def _rebase_branch(args: tuple[str, ...]) -> str | None:
    """The ``<branch>`` of ``git rebase [options] <upstream> <branch>``, or of ``--root <branch>``."""
    positionals: list[str] = []
    root = False
    index = 0
    while index < len(args):
        token = args[index]
        if token == "--":
            positionals.extend(args[index + 1:])
            break
        if token == "--root":
            root = True
        if token in _REBASE_VALUES:
            index += 2
            continue
        if not token.startswith("-"):
            positionals.append(token)
        index += 1
    position = 0 if root else 1
    if len(positionals) <= position or positionals[position] == "HEAD":
        return None
    return positionals[position]


def _fast_forward_only(args: tuple[str, ...]) -> bool:
    """``--ff-only`` that no later ``--ff``/``--no-ff`` or rebase flag undoes."""
    only = rebases = False
    for token in args:
        if token == "--ff-only":
            only = True
        elif token in {"--ff", "--no-ff"}:
            only = False
        elif token in {"--rebase", "-r"} or (token.startswith("--rebase=") and token != "--rebase=false"):
            rebases = True
        elif token in {"--no-rebase", "--rebase=false"}:
            rebases = False
    return only and not rebases


def _replay_reason(route: fix_route.Route) -> str:
    repo = route.origin_repo or "this repository"
    default = route.default_branch
    return (f"vcs: {repo} integrates through pull requests, so nothing is merged, cherry-picked, rebased, "
            f"applied, reverted or pulled onto its {default}. Create a branch first (git switch -c <branch>) "
            "and do it there, push it (git push -u origin HEAD), and open a PR: gh pr create. "
            f"To bring {default} up to date with origin: git pull --ff-only.")


def _fork_default(route: fix_route.Route, directory: Path) -> str:
    """The fork's own default branch; ``route.default_branch`` is upstream's for fork routes."""
    return fix_route.remote_default(directory, "origin") or route.default_branch


def _commit_reason(route: fix_route.Route, directory: Path) -> str:
    default = _fork_default(route, directory)
    if route.route == "pr":
        repo = route.origin_repo or "this repository"
        return (f"vcs: {repo} integrates through pull requests, so nothing is committed on its {default}. "
                "Create a branch first (git switch -c <branch>), commit there, push it "
                "(git push -u origin HEAD), and open a PR: gh pr create. The PR merges on GitHub.")
    if route.route == "external":
        repo = route.origin_repo or "this repository"
        return (f"vcs: {repo} isn't the user's, so nothing is committed on its {default}. "
                "Commit on a branch (git switch -c <branch>), push it (git push -u origin HEAD), "
                "and open a PR: gh pr create. If you can't push to it, fork it and push the branch there. "
                "If the user commits to this repository directly, ask them; with their yes, "
                "git config agents.route direct.")
    upstream = route.upstream_repo or "<upstream owner/repo>"
    base = fix_route.remote_default(directory, "upstream") or route.default_branch
    steps = (f"vcs: this is a fork of {upstream}; changes go to upstream as a PR, so don't commit on "
             f"the fork's {default}. git fetch upstream && git switch -c <branch> upstream/{base}, "
             f"commit there, git push -u origin HEAD, gh pr create --repo {upstream}. ")
    if route.route == "upstream+carry":
        return steps + (f"The fork carries its own changes, so then also git switch {default} && "
                        f"git merge <branch> && git push origin {default}. A change meant only for the "
                        f"fork skips the PR: commit it on a branch, merge it into {default}, and push.")
    return steps + (f"The fork's {default} matches upstream and doesn't get the change: ask the user "
                    "whether the fork should carry it until upstream merges.")


def _upstream_push_reason(route: fix_route.Route | None) -> str:
    target = route.upstream_repo if route and route.upstream_repo else "<upstream owner/repo>"
    return ("vcs: never push to upstream. Push your branch to origin (git push -u origin HEAD) "
            f"and open a PR: gh pr create --repo {target}.")


def _carry_reason(route: fix_route.Route, directory: Path) -> str:
    default = _fork_default(route, directory)
    base = fix_route.remote_default(directory, "upstream") or route.default_branch
    target = route.upstream_repo or "<upstream owner/repo>"
    return (f"vcs: the fork's {default} matches upstream and doesn't carry changes, so don't push it. "
            f"Put the change on a branch from upstream/{base}, push that branch "
            f"(git push -u origin HEAD) and open a PR: gh pr create --repo {target}. "
            "Ask the user whether the fork should carry it until upstream merges; if yes: "
            f"git config agents.route upstream+carry, then merge the branch into {default} and push it.")


def _explicit_direct(directory: Path, cache: dict[str, object]) -> bool:
    """True only for ``git config agents.route direct``. Errors are not an override."""
    key = "direct-override\0" + os.path.normpath(os.fspath(directory))
    if key in cache:
        return bool(cache[key])
    try:
        result = fix_route.git(directory, "config", "--get", "agents.route")
        found = result.returncode == 0 and result.stdout.strip() == "direct"
    except Exception:
        found = False
    cache[key] = found
    return found


def _cached_route(directory: Path, cache: dict[str, object]) -> object:
    key = os.path.normpath(os.fspath(directory))
    if key in cache:
        return cache[key]
    try:
        if not fix_route.is_work_tree(directory):
            found: object = _NOT_A_REPO
        else:
            try:
                found = fix_route.decide(directory, offline=True)
            except fix_route.DefaultBranchUnknown as error:
                # A configured route that needs the default fails closed; any other route stays open.
                found = error if error.route else None
            except fix_route.ConfiguredRouteError as error:
                # An explicit route that cannot be applied: nothing may slip past it.
                found = error
            except Exception:
                # Still a work tree: rule (c) can reject a push to upstream without a route.
                found = None
    except Exception:
        found = _NOT_A_REPO
    cache[key] = found
    return found


def _pushes_upstream(push: Push, directory: Path, moved: bool = False) -> bool:
    if push.remote is None and moved:
        return False
    remote = push.remote if push.remote is not None else fix_route.push_remote(directory)
    if not remote:
        return False
    return _same_remote(remote, "upstream", directory)


def _pushes_origin_default(push: Push, directory: Path, moved: bool = False) -> bool:
    """True only when this push would update origin's default branch. Ambiguity is False.

    ``moved``: an earlier command may have switched branches, so the current branch is unknown.
    """
    if push.delete:
        return False
    if push.remote is None and moved:
        return False
    remote = push.remote if push.remote is not None else fix_route.push_remote(directory)
    if not remote or not _same_remote(remote, "origin", directory):
        return False
    default = fix_route.remote_default(directory, "origin")
    if not default:
        # No origin HEAD, main, or master ref. The current branch is not a substitute.
        return False
    if push.all_branches:
        return True
    if not push.refspecs:
        if push.tags or moved:
            return False
        return fix_route.local_branch(directory) == default
    current = None if moved else fix_route.local_branch(directory)
    return any(_refspec(refspec, default, current) == "deny" for refspec in push.refspecs)


def _same_remote(remote: str, name: str, directory: Path) -> bool:
    if remote == name:
        return True
    expected = fix_route.remote_url(directory, name)
    if not expected:
        return False
    if fix_route.urls_equal(remote, expected):
        return True
    named = fix_route.remote_url(directory, remote)
    return bool(named and fix_route.urls_equal(named, expected))


def _refspec(spec: str, default: str, current: str | None) -> str:
    body = spec[1:] if spec.startswith("+") else spec
    if not body or body.startswith(":"):
        return "allow"
    if ":" in body:
        _src, dst = body.split(":", 1)
        if not dst:
            return "allow"
    else:
        dst = body
        if body == "HEAD":
            dst = current or ""
    if dst == "HEAD":
        dst = current or ""
    if not dst:
        return "ambiguous"
    if dst.startswith("refs/tags/") or dst.startswith("refs/notes/"):
        return "allow"
    if "*" in dst or "?" in dst:
        return "ambiguous"
    if dst in {default, f"refs/heads/{default}"}:
        return "deny"
    return "allow"


def _deny(reason: str) -> dict:
    # Codex 0.169 reads the same PreToolUse envelope as Claude, and rejects a deny with an empty reason.
    return {"hookSpecificOutput": {
        "hookEventName": "PreToolUse",
        "permissionDecision": "deny",
        "permissionDecisionReason": reason,
    }}


def parse_script(command: str, base: Path) -> list[tuple[Path | None, list[str]]] | None:
    """Simple commands and the directory each one runs in. None when the shell is not parseable.

    ``|`` runs each side in the directory from before the pipeline. ``cd`` persists across
    ``&&``, ``||``, ``;``, and newlines. A heredoc body is not a command. ``pushd`` and
    ``popd`` make the directory unknown from there on.
    """
    parsed = _parse_script(command, base)
    return None if parsed is None else [(cwd, argv) for cwd, argv, _gh_repo in parsed]


def _parse_script(command: str, base: Path) -> list[tuple[Path | None, list[str], object]] | None:
    """``parse_script`` plus ``GH_REPO`` as each command sees it: a value, None (unset), or ``_INHERIT``.

    The variable comes from an assignment in front of the command, or from an earlier
    ``export``, ``unset`` or bare assignment. A bare assignment counts as exported: that errs on the
    side of denying.
    """
    stripped = _strip_heredocs(command)
    if stripped is None:
        return None
    tokens = _tokenize(stripped)
    if tokens is None or any(token in {"(", ")", "`"} for token in tokens):
        return None
    commands: list[tuple[Path | None, list[str], object]] = []
    cwd: Path | None = base
    gh_repo: object = _INHERIT
    index = 0
    while index <= len(tokens):
        raw_pipeline: list[list[str]] = []
        separator = None
        while True:
            argv, separator, index = _read_command(tokens, index)
            if argv:
                raw_pipeline.append(argv)
            if separator != "|":
                break
        pipeline = [_normalize(argv) for argv in raw_pipeline]
        for raw, argv in zip(raw_pipeline, pipeline, strict=True):
            if not argv:
                continue
            # GIT_DIR / GIT_WORK_TREE choose a repository the cwd does not name.
            inline = _leading_assignments(raw).get("GH_REPO", gh_repo)
            commands.append((None if _env_assigns_git_location(raw) else cwd, argv, inline))
        if len(pipeline) == 1:
            gh_repo = _track_gh_repo(raw_pipeline[0], pipeline[0], gh_repo)
        if separator is None:
            break
        if len(pipeline) == 1 and pipeline[0][:1] == ["cd"]:
            cwd = None if cwd is None else _apply_cd(cwd, pipeline[0])
        elif len(pipeline) == 1 and pipeline[0][:1] in (["pushd"], ["popd"]):
            cwd = None
    return commands


def parse_push(argv: list[str], cwd: Path) -> Push | object | None:
    """A ``git push`` invocation, None when ``argv`` is not one, or ambiguous."""
    found = _git_subcommand(argv, cwd)
    if found is None or found is _AMBIGUOUS:
        return found
    directory, subcommand, rest = found
    if subcommand != "push":
        return None
    parsed = _push_args(rest)
    if parsed is _AMBIGUOUS:
        return _AMBIGUOUS
    remote, refspecs, delete, tags, all_branches, dry_run = parsed
    return Push(directory, remote, refspecs, delete, tags, all_branches, dry_run)


def parse_commit(argv: list[str], cwd: Path) -> Commit | object | None:
    """A ``git commit`` that records a commit, None for anything else (including --dry-run)."""
    found = _git_subcommand(argv, cwd)
    if found is None or found is _AMBIGUOUS:
        return found
    directory, subcommand, rest = found
    if subcommand != "commit":
        return None
    index = 0
    while index < len(rest):
        token = rest[index]
        if token == "--":
            break
        if token in {"--dry-run", "-h", "--help"}:
            return None
        index += 2 if token in _COMMIT_VALUES else 1
    return Commit(directory)


def parse_replay(argv: list[str], cwd: Path) -> Replay | object | None:
    """A git command that creates or replays commits other than ``commit``, or None."""
    found = _git_subcommand(argv, cwd)
    if found is None or found is _AMBIGUOUS:
        return found
    directory, subcommand, rest = found
    if subcommand not in _REPLAYS:
        return None
    return Replay(directory, subcommand, tuple(rest))


def _git_subcommand(argv: list[str], cwd: Path) -> tuple[Path, str, list[str]] | object | None:
    """The directory, subcommand, and its arguments of a git command line."""
    if not argv or Path(argv[0]).name != "git":
        return None
    directories: list[str] = []
    index = 1
    while index < len(argv):
        token = argv[index]
        if token in {"--git-dir", "--work-tree"} or token.startswith(("--git-dir=", "--work-tree=")):
            return _AMBIGUOUS
        if token == "-C":
            if index + 1 >= len(argv):
                return _AMBIGUOUS
            directories.append(argv[index + 1])
            index += 2
            continue
        if token.startswith("-C") and len(token) > 2:
            directories.append(token[2:])
            index += 1
            continue
        if token.startswith("--") and "=" in token:
            index += 1
            continue
        if token in _GIT_VALUES:
            if index + 1 >= len(argv):
                return _AMBIGUOUS
            index += 2
            continue
        if token.startswith("-"):
            index += 1
            continue
        break
    else:
        return None
    directory = cwd
    for item in directories:
        path = Path(item).expanduser()
        if not path.is_absolute():
            path = directory / path
        directory = path
    return directory, argv[index], argv[index + 1:]


def parse_gh(argv: list[str]) -> GhCreate | object | None:
    """``gh pr create`` and the last ``--repo``/``-R`` value, or None when it is a different command."""
    if not argv or Path(argv[0]).name != "gh":
        return None
    repo = None
    head = None
    saw_repo = False
    positionals: list[str] = []
    index = 1
    while index < len(argv):
        token = argv[index]
        if token == "--":
            positionals.extend(argv[index + 1:])
            break
        if token.startswith("-") and not token.startswith("--"):
            group = _gh_short_group(argv, index)
            if group is _AMBIGUOUS:
                return _AMBIGUOUS
            found_repo, found_head, used = group
            if found_repo is not None:
                repo = found_repo
                saw_repo = True
            if found_head is not None:
                head = found_head
            index += used
            continue
        if token == "--repo":
            if index + 1 >= len(argv):
                return _AMBIGUOUS
            repo = argv[index + 1]
            saw_repo = True
            index += 2
            continue
        if token.startswith("--repo="):
            repo = token.split("=", 1)[1]
            saw_repo = True
            index += 1
            continue
        if token == "--head":
            if index + 1 >= len(argv):
                return _AMBIGUOUS
            head = argv[index + 1]
            index += 2
            continue
        if token.startswith("--head="):
            head = token.split("=", 1)[1]
            index += 1
            continue
        if token.startswith("--") and "=" in token:
            index += 1
            continue
        if token in _GH_VALUES:
            if index + 1 >= len(argv):
                return _AMBIGUOUS
            index += 2
            continue
        if token.startswith("-"):
            index += 1
            continue
        positionals.append(token)
        index += 1
    if positionals[:2] != ["pr", "create"]:
        return None
    return GhCreate(repo if saw_repo else None, head)


def _gh_short_group(argv: list[str], index: int) -> tuple[str | None, str | None, int] | object:
    """Read the short options in ``argv[index]``: ``(repo, head, words used)``, None for a value not given.

    A value-taking letter ends the bundle: ``-fRowner/repo`` and ``-fR owner/repo`` both set the repo,
    and, as in pflag, a single ``=`` right after the letter is not part of the value.
    """
    token = argv[index]
    for position, letter in enumerate(token[1:], start=1):
        if letter not in _GH_SHORT_VALUES:
            continue
        value: str | None = token[position + 1:].removeprefix("=") if token[position + 1:] else None
        used = 1
        if value is None:
            if index + 1 >= len(argv):
                return _AMBIGUOUS
            value = argv[index + 1]
            used = 2
        return (value if letter == "R" else None), (value if letter == "H" else None), used
    return None, None, 1


def _tokenize(command: str) -> list[str] | None:
    lexer = shlex.shlex(command, posix=True)
    lexer.whitespace = " \t\r"
    lexer.commenters = ""
    # Keep refspecs, assignments, and paths in one token. Operators stay outside wordchars.
    lexer.wordchars += "-./:@+=~^,*?{}"
    try:
        raw = list(lexer)
    except ValueError:
        return None
    tokens: list[str] = []
    for token in raw:
        if token == "&" and tokens and tokens[-1] == "&":
            tokens[-1] = "&&"
        elif token == "|" and tokens and tokens[-1] == "|":
            tokens[-1] = "||"
        elif token == ">" and tokens and tokens[-1] == ">":
            tokens[-1] = ">>"
        else:
            tokens.append(token)
    return tokens


def _read_command(tokens: list[str], index: int) -> tuple[list[str], str | None, int]:
    argv: list[str] = []
    while index < len(tokens):
        token = tokens[index]
        if token.isdigit() and index + 1 < len(tokens) and tokens[index + 1] in {">", ">>", "<"}:
            index += 1
            continue
        if token in _REDIRECTS:
            index += 1
            if index < len(tokens) and tokens[index] == "&":
                index += 1
            if index < len(tokens) and tokens[index] not in _SEPARATORS and tokens[index] not in _REDIRECTS:
                index += 1
            continue
        if token in _SEPARATORS:
            return argv, token, index + 1
        argv.append(token)
        index += 1
    return argv, None, index


def _leading_assignments(argv: list[str]) -> dict[str, str]:
    """``NAME=value`` words in front of the command, before or after ``rtk``. Later ones win."""
    spans = [argv]
    for index, token in enumerate(argv):
        if token == "rtk":
            spans.append(argv[index + 1:])
            break
    found: dict[str, str] = {}
    for span in spans:
        for token in span:
            if not _ENV.match(token):
                break
            name, _, value = token.partition("=")
            found[name] = value
    return found


def _env_assigns_git_location(argv: list[str]) -> bool:
    """True when the command sets ``GIT_DIR`` or ``GIT_WORK_TREE`` for itself."""
    return bool(_leading_assignments(argv).keys() & {"GIT_DIR", "GIT_WORK_TREE"})


def _track_gh_repo(raw: list[str], argv: list[str], current: object) -> object:
    """``GH_REPO`` after a lone command: a bare assignment or ``export`` sets it, ``unset`` clears it."""
    if not argv:
        return _leading_assignments(raw).get("GH_REPO", current)
    if argv[0] == "export":
        for token in argv[1:]:
            if token.startswith("GH_REPO="):
                current = token.partition("=")[2]
        return current
    if argv[0] == "unset" and "GH_REPO" in argv[1:]:
        return None
    return current


_HEREDOC = re.compile(r"<<-?[ \t]*(?:'([^'\n]+)'|\"([^\"\n]+)\"|([A-Za-z0-9_]+))")


def _strip_heredocs(command: str) -> str | None:
    """Remove heredoc bodies. None when a delimiter never closes, which the guard allows."""
    lines = command.split("\n")
    kept: list[str] = []
    index = 0
    while index < len(lines):
        operators = _heredoc_operators(lines[index])
        if operators is None:
            return None
        kept.append(lines[index])
        index += 1
        for word, strip_tabs in operators:
            closed = False
            while index < len(lines):
                body = lines[index]
                index += 1
                candidate = body.lstrip("\t") if strip_tabs else body
                if candidate == word:
                    closed = True
                    break
            if not closed:
                return None
    return "\n".join(kept)


def _heredoc_operators(line: str) -> list[tuple[str, bool]] | None:
    """Heredoc words on ``line``, outside quotes. None when a ``<<`` is not one we can delimit."""
    operators: list[tuple[str, bool]] = []
    index = 0
    in_single = False
    in_double = False
    while index < len(line):
        character = line[index]
        if in_single:
            if character == "'":
                in_single = False
            index += 1
            continue
        if in_double:
            if character == "\\" and index + 1 < len(line):
                index += 2
                continue
            if character == '"':
                in_double = False
            index += 1
            continue
        if character == "'":
            in_single = True
            index += 1
            continue
        if character == '"':
            in_double = True
            index += 1
            continue
        if character == "\\" and index + 1 < len(line):
            index += 2
            continue
        if character == "<" and index + 1 < len(line) and line[index + 1] == "<":
            if index + 2 < len(line) and line[index + 2] == "<":
                index += 3
                continue
            match = _HEREDOC.match(line, index)
            if match is None:
                return None
            word = match.group(1) or match.group(2) or match.group(3)
            operators.append((word, line.startswith("<<-", index)))
            index = match.end()
            continue
        index += 1
    return operators


def _normalize(argv: list[str]) -> list[str]:
    index = 0
    while index < len(argv) and _ENV.match(argv[index]):
        index += 1
    argv = argv[index:]
    if argv and argv[0] == "rtk":
        argv = argv[1:]
        while argv and _ENV.match(argv[0]):
            argv = argv[1:]
    return argv


def _apply_cd(cwd: Path, argv: list[str]) -> Path | None:
    args = [arg for arg in argv[1:] if arg != "--" and arg not in {"-P", "-L"}]
    if len(args) != 1 or args[0].startswith("-"):
        return None
    path = Path(args[0]).expanduser()
    if not path.is_absolute():
        path = cwd / path
    return path


def _push_args(argv: list[str]) -> tuple | object:
    remote_flag = None
    positionals: list[str] = []
    delete = tags = all_branches = dry_run = False
    index = 0
    while index < len(argv):
        token = argv[index]
        if token == "--":
            positionals.extend(argv[index + 1:])
            break
        if token == "--delete":
            delete = True
            index += 1
            continue
        if token == "--tags":
            tags = True
            index += 1
            continue
        if token in {"--all", "--mirror", "--branches"}:
            all_branches = True
            index += 1
            continue
        if token == "--dry-run":
            dry_run = True
            index += 1
            continue
        if token == "--no-dry-run":
            dry_run = False
            index += 1
            continue
        if token in _PUSH_VALUES:
            if index + 1 >= len(argv):
                return _AMBIGUOUS
            if token == "--repo":
                remote_flag = argv[index + 1]
            index += 2
            continue
        if token.startswith("--repo="):
            remote_flag = token.split("=", 1)[1]
            index += 1
            continue
        if token.startswith("--") and "=" in token:
            index += 1
            continue
        if token.startswith("-") and not token.startswith("--"):
            # git push -n is --dry-run, including inside a short cluster such as -nu.
            if "n" in token[1:]:
                dry_run = True
            index += 1
            continue
        if token.startswith("-"):
            index += 1
            continue
        positionals.append(token)
        index += 1
    if remote_flag is not None:
        remote = remote_flag
        refspecs = positionals
    elif positionals:
        remote = positionals[0]
        refspecs = positionals[1:]
    else:
        remote = None
        refspecs = []
    return remote, _fold_tags(refspecs), delete, tags, all_branches, dry_run


def _fold_tags(refspecs: list[str]) -> tuple[str, ...]:
    folded: list[str] = []
    index = 0
    while index < len(refspecs):
        if refspecs[index] == "tag" and index + 1 < len(refspecs):
            folded.append(f"refs/tags/{refspecs[index + 1]}")
            index += 2
        else:
            folded.append(refspecs[index])
            index += 1
    return tuple(folded)

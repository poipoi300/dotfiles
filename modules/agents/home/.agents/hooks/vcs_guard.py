"""Steer git work to where ``fix-route`` says it goes, without the agent knowing the route.

PreToolUse denies a commit, push, or ``gh pr create`` that misses the route; each reason is
the whole instruction for an agent that has never heard of routes. PostToolUse reminds the
agent to carry a change, or to ask about carrying it, after its upstream PR opens.

A repository configured ``agents.route pr`` is the user's own and integrates through pull
requests: the guard allows ``gh pr create`` and feature-branch pushes, and denies a commit on
or push to the default branch.

Offline only: a hook has a few seconds and must not call the network. Anything this module
cannot parse, or any repository it cannot classify, is allowed.
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
    "-R", "--repo", "--hostname", "-t", "--title", "-b", "--body", "-F", "--body-file",
    "-a", "--assignee", "-r", "--reviewer", "-l", "--label", "-p", "--project", "-m", "--milestone",
    "-H", "--head", "-B", "--base", "-T", "--template", "--recover", "--jq",
}
_IN_PROGRESS = ("MERGE_HEAD", "SQUASH_MSG", "CHERRY_PICK_HEAD", "REVERT_HEAD", "rebase-merge", "rebase-apply")
# Subcommands that can change the checked-out branch before a later command in the same script runs.
_MOVES = {"switch", "checkout", "worktree", "branch", "rebase", "reset", "stash", "bisect"}
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


@dataclass(frozen=True)
class Commit:
    directory: Path


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
    for cwd, argv in script:
        decision = _consider(cwd, argv, cache, moved)
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
    for cwd, argv in script:
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


def _script(payload: dict) -> list[tuple[Path | None, list[str]]] | None:
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
    return parse_script(command, base)


def _may_match(command: str) -> bool:
    if "git" in command and ("push" in command or "commit" in command):
        return True
    return "gh" in command and "pr" in command and "create" in command


def _consider(cwd: Path | None, argv: list[str], cache: dict[str, object], moved: bool = False) -> dict | None:
    if not argv or cwd is None:
        return None
    push = parse_push(argv, cwd)
    commit = parse_commit(argv, cwd) if push is None else None
    created = None if push is not None or commit is not None else parse_gh(argv)
    if push is None and commit is None and created is None:
        return None
    if _AMBIGUOUS in (push, commit, created):
        return None
    directory = push.directory if isinstance(push, Push) else commit.directory if isinstance(commit, Commit) else cwd
    route = _cached_route(directory, cache)
    if route is _NOT_A_REPO:
        return None
    # Login unknown: nothing here is a clear violation. A crashed decide() is not this route,
    # so the never-push-upstream rule below still applies to that case.
    if isinstance(route, fix_route.Route) and route.route == "unknown":
        return None
    known = route if isinstance(route, fix_route.Route) else None
    if isinstance(commit, Commit):
        if moved or not _commit_misses(known, directory):
            return None
        return _deny(_commit_reason(known, directory))
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
    if known.route in ("external", "pr"):
        return None
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


def _commit_misses(route: fix_route.Route | None, directory: Path) -> bool:
    """True when a commit would land on the default branch of a fork, someone else's repo, or a PR repo."""
    if route is None or route.route not in (*_FORK_ROUTES, "external", "pr"):
        return False
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
    stripped = _strip_heredocs(command)
    if stripped is None:
        return None
    tokens = _tokenize(stripped)
    if tokens is None or any(token in {"(", ")", "`"} for token in tokens):
        return None
    commands: list[tuple[Path | None, list[str]]] = []
    cwd: Path | None = base
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
            commands.append((None if _env_assigns_git_location(raw) else cwd, argv))
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
    saw_repo = False
    positionals: list[str] = []
    index = 1
    while index < len(argv):
        token = argv[index]
        if token == "--":
            positionals.extend(argv[index + 1:])
            break
        if token in {"-R", "--repo"}:
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
        if token.startswith("-R") and len(token) > 2:
            repo = token[2:]
            saw_repo = True
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
    return GhCreate(repo if saw_repo else None)


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


def _env_assigns_git_location(argv: list[str]) -> bool:
    """True when the command sets ``GIT_DIR`` or ``GIT_WORK_TREE`` for itself."""
    spans = [argv]
    for index, token in enumerate(argv):
        if token == "rtk":
            spans.append(argv[index + 1:])
            break
    for span in spans:
        for token in span:
            if not _ENV.match(token):
                break
            if token.startswith(("GIT_DIR=", "GIT_WORK_TREE=")):
                return True
    return False


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

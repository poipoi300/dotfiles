"""Block a Bash command that does not follow ``fix-route``.

Offline only: a hook has a few seconds and must not call the network. Anything
this module cannot parse, or any repository it cannot classify, is allowed.
"""

from __future__ import annotations

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
_UPSTREAM = "fix-route: never push to upstream; push a branch to origin and open a PR."
_CARRY = ("fix-route: this fork doesn't carry changes (fork main matches upstream). "
          "Ask the user; if they want it carried: git config agents.route upstream+carry")


@dataclass(frozen=True)
class Push:
    directory: Path
    remote: str | None
    refspecs: tuple[str, ...]
    delete: bool
    tags: bool
    all_branches: bool


@dataclass(frozen=True)
class GhCreate:
    repo: str | None


def guard(payload: dict) -> dict | None:
    """Deny decision for a Bash PreToolUse payload, or None to leave the command alone."""
    try:
        return _guard(payload)
    except Exception:
        return None


def _guard(payload: dict) -> dict | None:
    if payload.get("hook_event_name") not in (None, "PreToolUse"):
        return None
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
    script = parse_script(command, base)
    if script is None:
        return None
    cache: dict[str, object] = {}
    for cwd, argv in script:
        decision = _consider(cwd, argv, cache)
        if decision:
            return decision
    return None


def _may_match(command: str) -> bool:
    if "git" in command and "push" in command:
        return True
    return "gh" in command and "pr" in command and "create" in command


def _consider(cwd: Path | None, argv: list[str], cache: dict[str, object]) -> dict | None:
    if not argv or cwd is None:
        return None
    push = parse_push(argv, cwd)
    created = None if push is not None else parse_gh(argv)
    if push is None and created is None:
        return None
    if push is _AMBIGUOUS or created is _AMBIGUOUS:
        return None
    directory = push.directory if isinstance(push, Push) else cwd
    route = _cached_route(directory, cache)
    if route is _NOT_A_REPO:
        return None
    if isinstance(push, Push):
        if _pushes_upstream(push, directory):
            return _deny(_UPSTREAM)
        if isinstance(route, fix_route.Route) and route.route == "upstream" and _pushes_origin_default(push, directory):
            return _deny(_CARRY)
        return None
    if not isinstance(created, GhCreate) or not isinstance(route, fix_route.Route):
        return None
    if route.route == "direct":
        repo = route.origin_repo or "this repo"
        return _deny(f"fix-route: {repo} takes direct pushes: commit on {route.default_branch} and push; no PR.")
    if route.route in ("upstream", "upstream+carry") and route.upstream_repo:
        if created.repo is not None and fix_route.same_repo(created.repo, route.upstream_repo):
            return None
        return _deny("fix-route: this is a fork; open the PR against upstream: "
                     f"gh pr create --repo {route.upstream_repo} ...")
    return None


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


def _pushes_upstream(push: Push, directory: Path) -> bool:
    remote = push.remote if push.remote is not None else fix_route.push_remote(directory)
    if not remote:
        return False
    return _same_remote(remote, "upstream", directory)


def _pushes_origin_default(push: Push, directory: Path) -> bool:
    """True only when this push would update origin's default branch. Ambiguity is False."""
    if push.delete:
        return False
    remote = push.remote if push.remote is not None else fix_route.push_remote(directory)
    if not remote or not _same_remote(remote, "origin", directory):
        return False
    default = fix_route.remote_default(directory, "origin") or fix_route.local_branch(directory)
    if not default:
        return False
    if push.all_branches:
        return True
    if not push.refspecs:
        if push.tags:
            return False
        return fix_route.local_branch(directory) == default
    current = fix_route.local_branch(directory)
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
    ``&&``, ``||``, ``;``, and newlines.
    """
    tokens = _tokenize(command)
    if tokens is None or any(token in {"(", ")", "`"} for token in tokens):
        return None
    commands: list[tuple[Path | None, list[str]]] = []
    cwd: Path | None = base
    index = 0
    while index <= len(tokens):
        pipeline: list[list[str]] = []
        separator = None
        while True:
            argv, separator, index = _read_command(tokens, index)
            if argv:
                pipeline.append(_normalize(argv))
            if separator != "|":
                break
        for argv in pipeline:
            if argv:
                commands.append((cwd, argv))
        if separator is None:
            break
        if cwd is not None and len(pipeline) == 1 and pipeline[0][:1] == ["cd"]:
            cwd = _apply_cd(cwd, pipeline[0])
    return commands


def parse_push(argv: list[str], cwd: Path) -> Push | object | None:
    """A ``git push`` invocation, None when ``argv`` is not one, or ambiguous."""
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
    if argv[index] != "push":
        return None
    directory = cwd
    for item in directories:
        path = Path(item).expanduser()
        if not path.is_absolute():
            path = directory / path
        directory = path
    parsed = _push_args(argv[index + 1:])
    if parsed is _AMBIGUOUS:
        return _AMBIGUOUS
    remote, refspecs, delete, tags, all_branches = parsed
    return Push(directory, remote, refspecs, delete, tags, all_branches)


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
    delete = tags = all_branches = False
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
    return remote, _fold_tags(refspecs), delete, tags, all_branches


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

"""Decide where a change in a git repository goes, and the steps for that route.

Routes, first match wins:

- ``agents.route`` is ``direct``, ``upstream``, or ``upstream+carry``
- a remote named ``upstream`` (local remote-tracking refs only; this never fetches)
- otherwise GitHub, unless offline: a fork without that remote is an error,
  anything else (including a failed ``gh`` lookup) is ``direct``
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

ROUTES = ("direct", "upstream", "upstream+carry")
TIMEOUT = 5

_SLUG = re.compile(
    r"^(?:https?://(?:[^@/]+@)?github\.com/"
    r"|ssh://(?:[^@/]+@)?github\.com/"
    r"|git://(?:[^@/]+@)?github\.com/"
    r"|git@github\.com:"
    r")(?P<owner>[A-Za-z0-9_.-]+)/(?P<repo>[A-Za-z0-9_.-]+?)(?:\.git)?/?$"
)


class RouteError(Exception):
    """A route cannot be decided. ``code`` is the process status."""

    def __init__(self, message: str, code: int = 2) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class Route:
    route: str
    source: str
    default_branch: str
    upstream_repo: str | None
    origin_repo: str | None
    steps: tuple[str, ...]

    def as_dict(self) -> dict:
        return {
            "route": self.route,
            "source": self.source,
            "default_branch": self.default_branch,
            "upstream_repo": self.upstream_repo,
            "origin_repo": self.origin_repo,
            "steps": list(self.steps),
        }

    def format_text(self) -> str:
        headline = self.route
        if self.source == "config":
            headline += " (set by git config agents.route)"
        lines = [headline]
        if self.source == "remotes" and self.route in ("upstream", "upstream+carry"):
            lines.append("Using local remote-tracking refs; run `git fetch --all` first if they may be stale.")
        if self.origin_repo:
            lines.append(f"origin: {self.origin_repo}")
        if self.upstream_repo:
            lines.append(f"upstream: {self.upstream_repo}")
        lines.append(f"default branch: {self.default_branch}")
        lines.append("")
        lines.append("steps:")
        lines.extend(f"{index}. {step}" for index, step in enumerate(self.steps, start=1))
        return "\n".join(lines)


def run(argv: list[str], *, timeout: float = TIMEOUT) -> subprocess.CompletedProcess[str]:
    """Run a command without inheriting the caller's stdin.

    A hook has already consumed stdin, and neither git nor gh may block on a prompt.
    """
    env = os.environ.copy()
    env["GIT_TERMINAL_PROMPT"] = "0"
    return subprocess.run(
        argv, check=False, capture_output=True, text=True, encoding="utf-8",
        stdin=subprocess.DEVNULL, env=env, timeout=timeout,
    )


def git(directory: Path, *args: str, timeout: float = TIMEOUT) -> subprocess.CompletedProcess[str]:
    return run(["git", "-C", os.fspath(directory), *args], timeout=timeout)


def github_slug(url: str) -> str | None:
    """``owner/repo`` from an https or ssh GitHub remote, or None."""
    match = _SLUG.match(url.strip())
    if not match:
        return None
    return f"{match['owner']}/{match['repo']}"


def urls_equal(left: str, right: str) -> bool:
    """True when two remote strings name the same GitHub repository or the same URL."""
    if _canon(left) == _canon(right):
        return True
    a, b = github_slug(left), github_slug(right)
    return bool(a and b and a.lower() == b.lower())


def same_repo(value: str, slug: str) -> bool:
    """True when ``value`` is ``slug`` or a GitHub URL for it."""
    candidate = value.strip().removesuffix(".git").rstrip("/")
    if candidate.lower() == slug.lower():
        return True
    parsed = github_slug(value)
    return parsed is not None and parsed.lower() == slug.lower()


def _canon(url: str) -> str:
    return url.strip().rstrip("/")


def is_work_tree(directory: Path) -> bool:
    try:
        result = git(directory, "rev-parse", "--is-inside-work-tree")
    except (OSError, subprocess.TimeoutExpired):
        return False
    return result.returncode == 0 and result.stdout.strip() == "true"


def remote_url(directory: Path, name: str) -> str | None:
    try:
        result = git(directory, "remote", "get-url", name)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    return result.stdout.strip() or None


def remote_default(directory: Path, remote: str) -> str | None:
    """Default branch of a remote: its HEAD symref, else ``main``, else ``master``."""
    try:
        head = git(directory, "symbolic-ref", "--short", f"refs/remotes/{remote}/HEAD")
    except (OSError, subprocess.TimeoutExpired):
        return None
    if head.returncode == 0:
        text = head.stdout.strip()
        for prefix in (f"{remote}/", f"refs/remotes/{remote}/"):
            if text.startswith(prefix):
                return text[len(prefix):]
    for branch in ("main", "master"):
        try:
            found = git(directory, "show-ref", "--verify", "--quiet", f"refs/remotes/{remote}/{branch}")
        except (OSError, subprocess.TimeoutExpired):
            return None
        if found.returncode == 0:
            return branch
    return None


def local_branch(directory: Path) -> str | None:
    try:
        head = git(directory, "symbolic-ref", "--short", "HEAD")
    except (OSError, subprocess.TimeoutExpired):
        return None
    if head.returncode != 0:
        return None
    return head.stdout.strip() or None


def push_remote(directory: Path) -> str | None:
    """Remote a bare ``git push`` would use: branch pushRemote, then branch remote, else origin."""
    branch = local_branch(directory)
    if branch:
        for key in (f"branch.{branch}.pushRemote", f"branch.{branch}.remote"):
            try:
                result = git(directory, "config", "--get", key)
            except (OSError, subprocess.TimeoutExpired):
                return None
            if result.returncode == 0 and result.stdout.strip():
                return result.stdout.strip()
    return "origin" if remote_url(directory, "origin") else None


def route_steps(route: str, origin_default: str, upstream_default: str | None,
                upstream_target: str | None) -> tuple[str, ...]:
    """Exact steps for a route. ``upstream_target`` is ``owner/repo`` or a remote URL."""
    if route == "direct":
        return (
            f"Commit on {origin_default}.",
            f"git push origin {origin_default}",
            "Do not open a pull request.",
        )
    base = upstream_default or origin_default
    target = upstream_target or "<upstream owner/repo>"
    shared = (
        f"git switch -c <branch> upstream/{base}",
        "Commit on that branch.",
        "git push -u origin HEAD",
        f"gh pr create --repo {target}",
    )
    if route == "upstream":
        return shared + (
            f"Do not merge into the fork's {origin_default}.",
            "Ask the user whether the fork should carry this until upstream merges; "
            "if yes: git config agents.route upstream+carry",
        )
    return shared + (
        f"git switch {origin_default}",
        "git merge <branch>",
        f"git push origin {origin_default}",
        "Do not open a pull request on the fork.",
    )


def decide(directory: Path, *, offline: bool = False) -> Route:
    """Return the route for ``directory``. Raises ``RouteError`` when it cannot."""
    if not is_work_tree(directory):
        raise RouteError(f"fix-route: not a git repository: {directory}")
    origin = remote_url(directory, "origin")
    if origin is None:
        raise RouteError(f"fix-route: no origin remote: {directory}")
    origin_repo = github_slug(origin)
    configured = _configured_route(directory)
    upstream = remote_url(directory, "upstream")
    if configured is not None:
        return _finish(directory, configured, "config", origin_repo, upstream)
    if upstream is not None:
        route = "upstream+carry" if _ahead(directory) else "upstream"
        return _finish(directory, route, "remotes", origin_repo, upstream)
    if not offline and origin_repo is not None:
        parent = _fork_parent(origin_repo)
        if parent:
            raise RouteError(
                "fork without an `upstream` remote: "
                f"git remote add upstream https://github.com/{parent}.git && git fetch upstream, then rerun",
                code=3,
            )
        if parent is False:
            branch = _origin_default(directory)
            return Route("direct", "github", branch, None, origin_repo, route_steps("direct", branch, None, None))
    branch = _origin_default(directory)
    return Route("direct", "remotes", branch, None, origin_repo, route_steps("direct", branch, None, None))


def _configured_route(directory: Path) -> str | None:
    try:
        result = git(directory, "config", "--get", "agents.route")
    except (OSError, subprocess.TimeoutExpired) as error:
        raise RouteError("fix-route: cannot read git config agents.route") from error
    if result.returncode == 1 or (result.returncode == 0 and not result.stdout.strip()):
        return None
    if result.returncode != 0:
        raise RouteError("fix-route: cannot read git config agents.route")
    value = result.stdout.strip()
    if value not in ROUTES:
        raise RouteError(
            "fix-route: agents.route must be direct, upstream, or upstream+carry "
            f"(got {value!r})"
        )
    return value


def _ahead(directory: Path) -> bool:
    upstream_default = remote_default(directory, "upstream")
    origin_default = remote_default(directory, "origin")
    if upstream_default is None or origin_default is None:
        raise RouteError(
            "fix-route: cannot compare origin and upstream from local remote-tracking refs; "
            "run git fetch --all, then rerun"
        )
    try:
        result = git(directory, "rev-list", "--count", f"upstream/{upstream_default}..origin/{origin_default}")
    except (OSError, subprocess.TimeoutExpired) as error:
        raise RouteError("fix-route: cannot compare origin and upstream; run git fetch --all, then rerun") from error
    if result.returncode != 0:
        raise RouteError("fix-route: cannot compare origin and upstream; run git fetch --all, then rerun")
    try:
        return int(result.stdout.strip()) > 0
    except ValueError as error:
        raise RouteError("fix-route: cannot compare origin and upstream; run git fetch --all, then rerun") from error


def _origin_default(directory: Path) -> str:
    branch = remote_default(directory, "origin") or local_branch(directory)
    if not branch:
        raise RouteError("fix-route: cannot determine the default branch of origin")
    return branch


def _finish(directory: Path, route: str, source: str, origin_repo: str | None,
            upstream: str | None) -> Route:
    origin_default = _origin_default(directory)
    if route == "direct":
        return Route(route, source, origin_default, None, origin_repo,
                     route_steps(route, origin_default, None, None))
    if upstream is None:
        raise RouteError(f"fix-route: agents.route is {route} but this repository has no upstream remote")
    upstream_default = remote_default(directory, "upstream")
    if upstream_default is None:
        raise RouteError(
            "fix-route: cannot determine the default branch of upstream; run git fetch --all, then rerun"
        )
    target = github_slug(upstream) or upstream
    # The branch a fork change is based on is upstream's default, not the fork's.
    return Route(route, source, upstream_default, github_slug(upstream), origin_repo,
                 route_steps(route, origin_default, upstream_default, target))


def _fork_parent(origin_repo: str) -> str | None | bool:
    """Parent ``owner/repo``, False when GitHub says this is not a fork, None when ``gh`` fails."""
    try:
        result = run(["gh", "repo", "view", origin_repo, "--json", "isFork,parent"], timeout=TIMEOUT)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0 or not result.stdout.strip():
        return None
    try:
        payload = json.loads(result.stdout)
    except ValueError:
        return None
    if not isinstance(payload, dict):
        return None
    if payload.get("isFork") is not True:
        return False
    parent = _parent_slug(payload.get("parent"))
    if not parent:
        raise RouteError("fix-route: origin is a fork but gh did not name the parent repository")
    return parent


def _parent_slug(parent: object) -> str | None:
    if isinstance(parent, str):
        return github_slug(parent) or (parent if parent.count("/") == 1 and " " not in parent else None)
    if isinstance(parent, dict):
        name_with_owner = parent.get("nameWithOwner")
        if isinstance(name_with_owner, str) and name_with_owner.count("/") == 1:
            return name_with_owner
        name = parent.get("name")
        owner = parent.get("owner")
        login = owner.get("login") if isinstance(owner, dict) else owner if isinstance(owner, str) else None
        if isinstance(login, str) and isinstance(name, str) and login and name:
            return f"{login}/{name}"
    return None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="fix-route", description="Print where a change in this repository goes.")
    parser.add_argument("-C", dest="directory", default=".")
    parser.add_argument("--offline", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    try:
        route = decide(Path(args.directory), offline=args.offline)
    except RouteError as error:
        print(error, file=sys.stderr)
        return error.code
    if args.json:
        print(json.dumps(route.as_dict(), ensure_ascii=True))
    else:
        print(route.format_text())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

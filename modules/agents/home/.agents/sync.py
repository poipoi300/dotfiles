"""Maintain shared MCP definitions without replacing unrelated provider configuration.

Python 3.11+, standard library only. Run `python sync.py check`, `sync --dry-run`, or `sync`.
"""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import re
import sys
import tempfile
import time
import tomllib
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

BEGIN = "# BEGIN AGENT-COMMON MANAGED MCP\n"
END = "# END AGENT-COMMON MANAGED MCP\n"
NAME = re.compile(r"^[A-Za-z0-9_.-]+$")
VARIABLE = re.compile(r"^\$\{([A-Za-z_][A-Za-z0-9_]*)\}$")


class SyncError(Exception):
    """Configuration cannot be changed without an explicit resolution."""


def fingerprint(value: object) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def read_json(path: Path, default: dict | None = None) -> dict:
    value = json.loads(path.read_text(encoding="utf-8-sig")) if path.exists() else (default or {})
    if not isinstance(value, dict):
        raise SyncError(f"Expected an object: {path}")
    return value


def json_bytes(value: dict) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def native_servers(registry: dict, provider: str) -> dict:
    """Translate the supported common subset; reject fields that would be lost."""
    result = {}
    for name, original in registry.get("mcpServers", {}).items():
        if not NAME.fullmatch(name) or name == "t3-code":
            raise SyncError(f"Invalid or reserved MCP server name: {name}")
        if not isinstance(original, dict):
            raise SyncError(f"Server {name} must be an object")
        server = dict(original)
        allowed = {"type", "command", "args", "env", "url", "headers", "providerEnv"}
        if set(server) - allowed:
            raise SyncError(f"Unsupported common MCP fields for {name}: {sorted(set(server) - allowed)}")
        # Provider-specific environment, e.g. a server mode only one client supports.
        provider_env = server.pop("providerEnv", {})
        if not isinstance(provider_env, dict) or set(provider_env) - {"claude", "codex"} or not all(
            isinstance(values, dict) and all(isinstance(k, str) and isinstance(v, str) for k, v in values.items())
            for values in provider_env.values()
        ):
            raise SyncError(f"providerEnv must map claude/codex to string maps for {name}")
        if provider_env.get(provider):
            server["env"] = {**server.get("env", {}), **provider_env[provider]}
        kind = server.get("type", "stdio")
        if kind not in {"stdio", "http"}:
            raise SyncError(f"Unsupported MCP transport for {name}: {kind}")
        if kind == "stdio":
            if not isinstance(server.get("command"), str) or not server["command"]:
                raise SyncError(f"Missing command for {name}")
            if "url" in server or "headers" in server:
                raise SyncError(f"HTTP fields on stdio server {name}")
            if not isinstance(server.get("args", []), list) or not all(isinstance(x, str) for x in server.get("args", [])):
                raise SyncError(f"Arguments must be strings for {name}")
        elif not isinstance(server.get("url"), str) or not server["url"] or any(k in server for k in ("command", "args", "env")):
            raise SyncError(f"Invalid HTTP configuration for {name}")
        for key in ("env", "headers"):
            if key in server and (not isinstance(server[key], dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in server[key].items())):
                raise SyncError(f"{key} must map strings to strings for {name}")
        if provider == "claude":
            server["type"] = kind
            result[name] = server
            continue
        native = {key: server[key] for key in ("command", "args", "url") if key in server}
        # Codex does not expand Claude's ${VAR} syntax. Its environment forwarding fields do.
        for key in ("command", "url"):
            if "${" in native.get(key, ""):
                raise SyncError(f"Environment references in {key} are not portable for {name}")
        if any("${" in arg for arg in native.get("args", [])):
            raise SyncError(f"Environment references in args are not portable for {name}")
        constants, forwarded = {}, []
        for key, value in server.get("env", {}).items():
            variable = VARIABLE.fullmatch(value)
            if variable:
                if variable[1] != key:
                    raise SyncError(f"Codex cannot rename forwarded environment variable {key} for {name}")
                forwarded.append(key)
            elif "${" in value:
                raise SyncError(f"Only a whole ${{VAR}} reference is portable for {name}.{key}")
            else:
                constants[key] = value
        if constants:
            native["env"] = constants
        if forwarded:
            native["env_vars"] = forwarded
        headers, environment_headers = {}, {}
        for key, value in server.get("headers", {}).items():
            variable = VARIABLE.fullmatch(value)
            if variable:
                environment_headers[key] = variable[1]
            elif "${" in value:
                raise SyncError(f"Only a whole ${{VAR}} header reference is portable for {name}.{key}")
            else:
                headers[key] = value
        if headers:
            native["http_headers"] = headers
        if environment_headers:
            native["env_http_headers"] = environment_headers
        result[name] = native
    return result


def reconcile(current: dict, wanted: dict, prior: dict, label: str) -> dict:
    """Own server entries, never provider metadata or other users' server entries."""
    output = dict(current)
    for name in sorted(set(prior) | set(wanted)):
        exists = name in current
        previous_hash = prior.get(name)
        desired = wanted.get(name)
        if previous_hash is not None:
            if not exists or fingerprint(current[name]) != previous_hash:
                raise SyncError(f"Drift in managed {label} entry {name}; resolve the native edit before syncing")
        elif exists and (name not in wanted or current[name] != desired):
            raise SyncError(f"Unmanaged {label} entry collides with {name}; nothing changed")
        if name in wanted:
            output[name] = desired
        else:
            output.pop(name, None)
    return output


def split_managed_toml(text: str) -> tuple[str, dict]:
    # Markers delimit our own block only; tomllib parses all actual TOML syntax.
    starts = list(re.finditer(r"(?m)^" + re.escape(BEGIN.rstrip()) + r"\r?\n", text))
    ends = list(re.finditer(r"(?m)^" + re.escape(END.rstrip()) + r"\r?\n", text))
    if not starts and not ends:
        return text, {}
    if len(starts) != 1 or len(ends) != 1 or ends[0].start() < starts[0].end():
        raise SyncError("Codex managed block markers are missing or repeated")
    before, block, after = text[:starts[0].start()], text[starts[0].end():ends[0].start()], text[ends[0].end():]
    parsed = tomllib.loads(block)
    if set(parsed) - {"mcp_servers"}:
        raise SyncError("Unexpected settings in the Codex managed MCP block")
    return before + after, parsed.get("mcp_servers", {})


def toml_block(servers: dict) -> str:
    lines = [BEGIN.rstrip()]
    for name, server in sorted(servers.items()):
        lines.append(f"[mcp_servers.{json.dumps(name)}]")
        for key, value in server.items():
            if isinstance(value, dict):
                fields = ", ".join(f"{json.dumps(k)} = {json.dumps(v, ensure_ascii=False)}" for k, v in value.items())
                lines.append(f"{key} = {{ {fields} }}")
            else:
                lines.append(f"{key} = {json.dumps(value, ensure_ascii=False)}")
        lines.append("")
    lines.append(END.rstrip())
    return "\n".join(lines) + "\n"


@dataclass
class Change:
    path: Path
    before: bytes | None
    after: bytes


@dataclass
class SkillLink:
    path: Path
    target: Path


def plan_skill_links(home: Path) -> list[SkillLink]:
    source = home / ".agents/skills"
    if not source.exists():
        return []
    links = []
    for skill in sorted(source.iterdir()):
        if not skill.is_dir() or not (skill / "SKILL.md").is_file():
            continue
        if not NAME.fullmatch(skill.name) or skill.name in {"synced", "skill-creator", "skill-installer"}:
            raise SyncError(f"Shared skill name conflicts with runtime-managed skills: {skill.name}")
        destination = home / ".claude/skills" / skill.name
        if os.path.lexists(destination):
            if not destination.exists() or not os.path.samefile(destination, skill):
                raise SyncError(f"Claude skill collision: {skill.name}; archive or reconcile the existing folder first")
        else:
            links.append(SkillLink(destination, skill.resolve()))
    return links


def create_skill_link(link: SkillLink) -> None:
    link.path.parent.mkdir(parents=True, exist_ok=True)
    link.path.symlink_to(link.target, target_is_directory=True)


def remove_created_link(link: SkillLink) -> None:
    # Only called to undo the exact link just created; never recursively remove its target.
    if link.path.exists() and os.path.samefile(link.path, link.target):
        link.path.unlink()


def prepare(home: Path, registry_path: Path) -> tuple[list[Change], dict, Path]:
    # Without a registry (public module only) there are no servers to manage; skill links still apply.
    registry = read_json(registry_path, {"mcpServers": {}})
    if set(registry) - {"$schema", "$comment", "mcpServers", "claudePluginOverrides"}:
        raise SyncError("Unknown registry keys")
    if not isinstance(registry.get("mcpServers"), dict):
        raise SyncError("Registry must contain mcpServers")
    state_path = home / ".agents/state/sync-state.json"
    state = read_json(state_path)
    if state and state.get("version") != 1:
        raise SyncError("Unsupported sync-state version")
    changes = []

    def stage(path: Path, content: bytes) -> None:
        before = path.read_bytes() if path.exists() else None
        if before != content:
            changes.append(Change(path, before, content))

    claude_path = home / ".claude.json"
    claude = read_json(claude_path)
    desired_claude = native_servers(registry, "claude")
    # Legacy Claude accepts an omitted stdio type. Normalize only for comparison/adoption.
    current_claude = claude.get("mcpServers", {})
    if not isinstance(current_claude, dict) or any(not isinstance(value, dict) for value in current_claude.values()):
        raise SyncError("Claude mcpServers must map names to server objects")
    prior_claude = state.get("claude", {})
    compare_claude = {name: ({"type": "stdio", **value} if "command" in value else value) for name, value in current_claude.items()}
    merged = reconcile(compare_claude, desired_claude, prior_claude, "Claude MCP")
    # Preserve unmanaged entries byte-for-value, including omitted optional fields.
    for name in set(current_claude) - set(desired_claude) - set(prior_claude):
        merged[name] = current_claude[name]
    if merged != current_claude:
        claude["mcpServers"] = merged
        stage(claude_path, json_bytes(claude))

    codex_path = home / ".codex/config.toml"
    codex_text = codex_path.read_bytes().decode("utf-8-sig") if codex_path.exists() else ""
    original_toml = tomllib.loads(codex_text)
    base, owned = split_managed_toml(codex_text)
    desired_codex = native_servers(registry, "codex")
    reconciled = reconcile(owned, desired_codex, state.get("codex", {}), "Codex MCP")
    unmanaged = tomllib.loads(base).get("mcp_servers", {})
    if set(unmanaged) & set(reconciled):
        raise SyncError(f"Unmanaged Codex MCP name collision: {', '.join(sorted(set(unmanaged) & set(reconciled)))}")
    # Preserve every byte outside our block on normal LF files; never serialize native settings.
    rendered = base.rstrip("\r\n") + "\n\n" + toml_block(reconciled)
    checked = tomllib.loads(rendered)
    if {k: v for k, v in checked.items() if k != "mcp_servers"} != {k: v for k, v in original_toml.items() if k != "mcp_servers"}:
        raise SyncError("Rendering would change unrelated Codex settings")
    if {k: v for k, v in checked.get("mcp_servers", {}).items() if k not in reconciled} != unmanaged:
        raise SyncError("Rendering would change unmanaged Codex MCP entries")
    if reconciled or owned:
        stage(codex_path, rendered.encode("utf-8"))

    settings_path = home / ".claude/settings.json"
    plugins = registry.get("claudePluginOverrides", {})
    if not isinstance(plugins, dict) or any(not isinstance(v, bool) for v in plugins.values()):
        raise SyncError("claudePluginOverrides must map plugin names to booleans")
    settings = read_json(settings_path)
    current_plugins = settings.get("enabledPlugins", {})
    prior_plugins = dict(state.get("claudePlugins", {}))
    # This explicit registry field requests an initial plugin toggle (e.g. replacing a bundled
    # MCP server). Subsequent changes to the same native flag are drift, like managed servers.
    for name in plugins:
        if name not in prior_plugins and name in current_plugins:
            prior_plugins[name] = fingerprint(current_plugins[name])
    new_plugins = reconcile(current_plugins, plugins, prior_plugins, "Claude plugin")
    if new_plugins != current_plugins:
        settings["enabledPlugins"] = new_plugins
        stage(settings_path, json_bytes(settings))
    next_state = {
        "version": 1,
        "claude": {name: fingerprint(value) for name, value in desired_claude.items()},
        "codex": {name: fingerprint(value) for name, value in reconciled.items()},
        "claudePlugins": {name: fingerprint(value) for name, value in plugins.items()},
    }
    stage(state_path, json_bytes(next_state))
    return changes, next_state, state_path


def atomic_write(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=".agent-common-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(content)
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


@contextmanager
def sync_lock(home: Path):
    """flock(2) lock, automatically released after a crash; concurrent launches wait briefly."""
    state_dir = home / ".agents/state"
    state_dir.mkdir(parents=True, exist_ok=True)
    with (state_dir / "sync.lock").open("a+b") as handle:
        if handle.tell() == 0:
            handle.write(b"0")
            handle.flush()
        deadline = time.monotonic() + 30
        while True:
            try:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError as error:
                if time.monotonic() >= deadline:
                    raise SyncError("Timed out waiting for another common configuration sync") from error
                time.sleep(0.05)
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def apply(home: Path, registry_path: Path) -> list[Change | SkillLink]:
    with sync_lock(home):
        changes, _, _ = prepare(home, registry_path)
        links = plan_skill_links(home)
        if not changes and not links:
            return []
        backup = home / ".agents/backups" / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
        backup.mkdir(parents=True)
        manifest = []
        for index, change in enumerate(changes):
            if (change.path.read_bytes() if change.path.exists() else None) != change.before:
                raise SyncError(f"Concurrent edit to {change.path}; nothing applied")
            saved = f"{index}-{change.path.name}"
            if change.before is not None:
                (backup / saved).write_bytes(change.before)
            manifest.append({"path": str(change.path), "backup": saved if change.before is not None else None})
        (backup / "manifest.json").write_bytes(json_bytes({"files": manifest, "createdLinks": [{"path": str(link.path), "target": str(link.target)} for link in links]}))
        written = []
        created_links = []
        try:
            for change in changes:
                # Recheck immediately before replacement as native harnesses may save settings.
                if (change.path.read_bytes() if change.path.exists() else None) != change.before:
                    raise SyncError(f"Concurrent edit to {change.path}")
                atomic_write(change.path, change.after)
                written.append(change)
            for link in links:
                create_skill_link(link)
                created_links.append(link)
        except Exception as error:
            for link in reversed(created_links):
                remove_created_link(link)
            concurrent = []
            for change in reversed(written):
                if (change.path.read_bytes() if change.path.exists() else None) != change.after:
                    concurrent.append(str(change.path))
                    continue
                if change.before is None:
                    change.path.unlink(missing_ok=True)
                else:
                    atomic_write(change.path, change.before)
            if concurrent:
                raise SyncError(f"Write failed; concurrent edits left untouched in {', '.join(concurrent)}. Original backups: {backup}") from error
            raise
        return [*changes, *links]
def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("check", "sync"))
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--home", type=Path, default=Path.home())
    parser.add_argument("--registry", type=Path)
    args = parser.parse_args(argv)
    home = args.home.resolve()
    registry = args.registry or home / ".agents/mcp.json"
    try:
        changes = [*prepare(home, registry)[0], *plan_skill_links(home)] if args.command == "check" or args.dry_run else apply(home, registry)
        for change in changes:
            print(f"{'would update' if args.command == 'check' or args.dry_run else 'updated'}: {change.path}")
        if not changes:
            print("Shared MCP configuration and skill links are current.")
        return 1 if changes and args.command == "check" else 0
    except (SyncError, ValueError, OSError) as error:
        # Do not include native definitions, raw content, URLs, env values, or parser excerpts.
        if isinstance(error, (json.JSONDecodeError, tomllib.TOMLDecodeError)):
            print("Invalid configuration syntax; inspect the local file. Nothing applied.", file=sys.stderr)
        else:
            print(f"Sync refused: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

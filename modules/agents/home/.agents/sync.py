"""Maintain shared MCP definitions without replacing unrelated provider configuration.

Python 3.11+. Run `python sync.py check`, `sync --dry-run`, or `sync`. Claude and Codex need only
the standard library; Grok's config.toml is edited with python-tomlkit, which keeps the rest of
the file as it was.
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
PROVIDERS = ("claude", "codex", "antigravity", "grok")
# Provider homes; Antigravity and Grok are managed only once their CLI has created its folder.
PROVIDER_HOMES = {"antigravity": ".gemini", "grok": ".grok"}
ANTIGRAVITY_MCP = Path(".gemini/config/mcp_config.json")
GROK_CONFIG = Path(".grok/config.toml")
# Grok's Claude compatibility scan reads the home and project Claude files. Two home-level parts
# are turned off because the native files replace them: the ~/.claude.json MCP entries (the cell
# covers only that file) and ~/.claude/skills, which holds the Claude-only account skills; that one
# is ignored by path, so project .claude/skills still load. ~/.claude/CLAUDE.md stays on (Grok
# loads it once with ~/.grok/rules/AGENTS.md, the same file) and so do Claude hooks, which keeps
# project .claude/settings.json hooks; hooks/native.py ignores the home ones under Grok.
GROK_CLAUDE_COMPAT = {"mcps": False}
GROK_IGNORED_SKILLS = "~/.claude/skills"


class SyncError(Exception):
    """Configuration cannot be changed without an explicit resolution."""


def fingerprint(value: object) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def provider_enabled(home: Path, provider: str) -> bool:
    folder = PROVIDER_HOMES.get(provider)
    return folder is None or (home / folder).is_dir()


def read_json(path: Path, default: dict | None = None) -> dict:
    text = path.read_text(encoding="utf-8-sig") if path.exists() else ""
    # Antigravity creates an empty mcp_config.json; an empty file holds no settings.
    value = json.loads(text) if text.strip() else (default or {})
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
        if not isinstance(provider_env, dict) or set(provider_env) - set(PROVIDERS) or not all(
            isinstance(values, dict) and all(isinstance(k, str) and isinstance(v, str) for k, v in values.items())
            for values in provider_env.values()
        ):
            raise SyncError(f"providerEnv must map {'/'.join(PROVIDERS)} to string maps for {name}")
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
        elif provider == "grok":
            result[name] = grok_server(name, server)
        elif provider == "antigravity":
            result[name] = antigravity_server(name, server)
        elif provider == "codex":
            result[name] = codex_server(name, server)
        else:
            raise SyncError(f"Unknown provider: {provider}")
    return result


def reject_inline_references(name: str, native: dict, client: str) -> None:
    for key in ("command", "url"):
        if "${" in native.get(key, ""):
            raise SyncError(f"{client} does not expand environment references in {key} for {name}")
    if any("${" in arg for arg in native.get("args", [])):
        raise SyncError(f"{client} does not expand environment references in args for {name}")


GROK_SERVER_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_-]*$")


def grok_server(name: str, server: dict) -> dict:
    """Grok expands ${VAR} and ${VAR:-default} in every string field, as Claude does."""
    # Grok keys tools as server__tool and skips any key with a second `__` or a `___`.
    if not GROK_SERVER_NAME.fullmatch(name) or "__" in name or name.endswith("_"):
        raise SyncError(f"Grok skips the tools of an MCP server named {name}: start with a letter or _, "
                        "use letters, digits, - and _, with no __ and no trailing _")
    return {key: server[key] for key in ("command", "args", "env", "url", "headers") if key in server}


def antigravity_server(name: str, server: dict) -> dict:
    """mcp_config.json takes command/args/env or url/headers and expands no environment references."""
    native = {key: server[key] for key in ("command", "args", "url") if key in server}
    reject_inline_references(name, native, "Antigravity")
    env = {}
    for key, value in server.get("env", {}).items():
        variable = VARIABLE.fullmatch(value)
        if variable:
            if variable[1] != key:
                raise SyncError(f"Antigravity cannot rename forwarded environment variable {key} for {name}")
            continue  # the server inherits the variable from the agent's own environment
        if "${" in value:
            raise SyncError(f"Only a whole ${{VAR}} reference is portable for {name}.{key}")
        env[key] = value
    if env:
        native["env"] = env
    headers = server.get("headers", {})
    if any("${" in value for value in headers.values()):
        raise SyncError(f"Antigravity does not expand environment references in headers for {name}")
    if headers:
        native["headers"] = dict(headers)
    return native


def codex_server(name: str, server: dict) -> dict:
    native = {key: server[key] for key in ("command", "args", "url") if key in server}
    # Codex does not expand Claude's ${VAR} syntax. Its environment forwarding fields do.
    reject_inline_references(name, native, "Codex")
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
    return native


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


def fingerprints(values: dict) -> dict:
    return {name: fingerprint(value) for name, value in values.items()}


def load_tomlkit():
    try:
        import tomlkit
    except ImportError as error:
        raise SyncError("Grok's config.toml needs python-tomlkit, which the agents module installs") from error
    return tomlkit


def render_grok(text: str, current: dict, servers: dict, compat: dict) -> str:
    """Edit only the owned [mcp_servers.<name>] tables and compat.claude cells.

    Grok rewrites config.toml itself, so a marked block like Codex's would not survive. tomlkit
    keeps every other key, comment and table where it was; the result is parsed again and must
    differ from the original in nothing else."""
    tomlkit = load_tomlkit()
    from tomlkit.items import InlineTable
    document = tomlkit.parse(text)

    def table_in(container, key: str, *, super_table: bool = False):
        """The table at `key`, created in the style of its container (inline stays inline)."""
        if key not in container:
            inline = isinstance(container, InlineTable)
            container[key] = tomlkit.inline_table() if inline else tomlkit.table(is_super_table=super_table)
        return container[key]

    previous = current.get("mcp_servers", {})
    if servers:
        table_in(document, "mcp_servers", super_table=True)
    for name in sorted(previous.keys() - servers.keys()):
        del document["mcp_servers"][name]
    for name, server in sorted(servers.items()):
        if previous.get(name) == server:
            continue
        parent = document["mcp_servers"]
        table = tomlkit.inline_table() if isinstance(parent, InlineTable) else tomlkit.table()
        for key, value in server.items():
            if isinstance(value, dict):
                inline = tomlkit.inline_table()
                inline.update(value)
                table[key] = inline
            else:
                table[key] = value
        parent[name] = table
    claude = table_in(table_in(document, "compat", super_table=True), "claude")
    for cell, value in compat.items():
        claude[cell] = value
    skills = table_in(document, "skills")
    if "ignore" not in skills:
        skills["ignore"] = tomlkit.array()
    if GROK_IGNORED_SKILLS not in skills["ignore"]:
        skills["ignore"].append(GROK_IGNORED_SKILLS)
    rendered = tomlkit.dumps(document)

    def comparable(settings: dict) -> dict:
        result = {key: value for key, value in settings.items() if key != "mcp_servers"}
        if settings.get("mcp_servers"):
            result["mcp_servers"] = settings["mcp_servers"]
        return result

    ignore = current.get("skills", {}).get("ignore", [])
    expected = {**current, "mcp_servers": servers,
                "compat": {**current.get("compat", {}), "claude": compat},
                "skills": {**current.get("skills", {}),
                           "ignore": ignore if GROK_IGNORED_SKILLS in ignore else [*ignore, GROK_IGNORED_SKILLS]}}
    if comparable(tomllib.loads(rendered)) != comparable(expected):
        raise SyncError("Editing Grok's config.toml would change unrelated settings; nothing applied")
    return rendered


@dataclass
class Change:
    path: Path
    before: bytes | None
    after: bytes
    provider: str | None = None  # None for the sync state itself


@dataclass
class SkillLink:
    path: Path
    target: Path
    provider: str = "claude"


def skill_link_roots(home: Path) -> dict[str, Path]:
    """Where shared skills are linked, by provider. Codex and Grok scan ~/.agents/skills themselves.

    Antigravity's global skills folder is also the one T3 links into its Antigravity profile."""
    roots = {"claude": home / ".claude/skills"}
    if provider_enabled(home, "antigravity"):
        roots["antigravity"] = home / ".gemini/config/skills"
    return roots


def plan_skill_links(home: Path, problems: dict | None = None) -> list[SkillLink]:
    """Missing links; with `problems`, a provider's collision is recorded there instead of raised."""
    source = home / ".agents/skills"
    if not source.exists():
        return []
    links = []
    roots = skill_link_roots(home)
    for provider, root in list(roots.items()):
        # A file (or a link to one) where the skills folder belongs would fail every link.
        if os.path.lexists(root) and not root.is_dir():
            error = SyncError(f"{provider.capitalize()} skills folder {root} is not a folder; reconcile it first")
            if problems is None:
                raise error
            problems.setdefault(provider, error)
            del roots[provider]
    for skill in sorted(source.iterdir()):
        if not skill.is_dir() or not (skill / "SKILL.md").is_file():
            continue
        if not NAME.fullmatch(skill.name) or skill.name in {"synced", "skill-creator", "skill-installer"}:
            raise SyncError(f"Shared skill name conflicts with runtime-managed skills: {skill.name}")
        for provider, root in roots.items():
            destination = root / skill.name
            if os.path.lexists(destination):
                if not destination.exists() or not os.path.samefile(destination, skill):
                    error = SyncError(f"{provider.capitalize()} skill collision: {skill.name}; "
                                      "archive or reconcile the existing folder first")
                    if problems is None:
                        raise error
                    problems.setdefault(provider, error)
            else:
                links.append(SkillLink(destination, skill.resolve(), provider))
    return links


def create_skill_link(link: SkillLink) -> None:
    link.path.parent.mkdir(parents=True, exist_ok=True)
    link.path.symlink_to(link.target, target_is_directory=True)


def remove_created_link(link: SkillLink) -> None:
    # Only called to undo the exact link just created; never recursively remove its target.
    if link.path.exists() and os.path.samefile(link.path, link.target):
        link.path.unlink()


def prepare(home: Path, registry_path: Path, problems: dict | None = None) -> tuple[list[Change], dict, Path]:
    """Stage every provider's native changes. With `problems`, a provider whose files refuse a
    change is recorded there and left untouched while the others proceed; otherwise it raises."""
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
    # A provider that is absent or refused keeps its ownership record for the next sync.
    owned = {key: value for key, value in state.items() if key != "version"}

    def section(provider: str, stage_provider) -> None:
        if problems is not None and provider in problems:
            return  # already refused (a skill link collision); nothing of it is applied
        staged: list[Change] = []

        def stage(path: Path, content: bytes) -> None:
            before = path.read_bytes() if path.exists() else None
            if before != content:
                staged.append(Change(path, before, content, provider))

        try:
            records = stage_provider(stage)
        except (SyncError, ValueError, OSError, TypeError, AttributeError, KeyError) as error:
            # Read and parse failures stay inside the provider too (a directory at config.toml).
            if problems is None:
                raise
            problems[provider] = error
            return
        changes.extend(staged)
        owned.update(records)

    def claude(stage) -> dict:
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
        return {"claude": fingerprints(desired_claude), "claudePlugins": fingerprints(plugins)}

    def codex(stage) -> dict:
        codex_path = home / ".codex/config.toml"
        codex_text = codex_path.read_bytes().decode("utf-8-sig") if codex_path.exists() else ""
        original_toml = tomllib.loads(codex_text)
        base, current = split_managed_toml(codex_text)
        desired_codex = native_servers(registry, "codex")
        reconciled = reconcile(current, desired_codex, state.get("codex", {}), "Codex MCP")
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
        if reconciled or current:
            stage(codex_path, rendered.encode("utf-8"))
        return {"codex": fingerprints(reconciled)}

    def antigravity(stage) -> dict:
        antigravity_path = home / ANTIGRAVITY_MCP
        config = read_json(antigravity_path)
        current = config.get("mcpServers", {})
        if not isinstance(current, dict) or any(not isinstance(v, dict) for v in current.values()):
            raise SyncError("Antigravity mcpServers must map names to server objects")
        desired = native_servers(registry, "antigravity")
        merged = reconcile(current, desired, state.get("antigravity", {}), "Antigravity MCP")
        if merged != current:
            config["mcpServers"] = merged
            stage(antigravity_path, json_bytes(config))
        return {"antigravity": fingerprints(desired)}

    def grok(stage) -> dict:
        grok_path = home / GROK_CONFIG
        grok_text = grok_path.read_bytes().decode("utf-8-sig") if grok_path.exists() else ""
        config = tomllib.loads(grok_text)
        current = config.get("mcp_servers", {})
        if not isinstance(current, dict) or any(not isinstance(v, dict) for v in current.values()):
            raise SyncError("Grok mcp_servers must map names to tables")
        compat = config.get("compat", {}).get("claude", {}) if isinstance(config.get("compat", {}), dict) else None
        skills = config.get("skills", {})
        ignore = skills.get("ignore", []) if isinstance(skills, dict) else None
        if not isinstance(compat, dict) or not isinstance(ignore, list):
            raise SyncError("Grok compat.claude must be a table and skills.ignore a list")
        desired = native_servers(registry, "grok")
        merged = reconcile(current, desired, state.get("grok", {}), "Grok MCP")
        merged_compat = reconcile(compat, GROK_CLAUDE_COMPAT, state.get("grokClaudeCompat", {}),
                                  "Grok Claude compatibility")
        # Membership of one entry in a list the user may extend.
        listed = {GROK_IGNORED_SKILLS: True} if GROK_IGNORED_SKILLS in ignore else {}
        reconcile(listed, {GROK_IGNORED_SKILLS: True}, state.get("grokSkillsIgnore", {}), "Grok skills.ignore")
        if merged != current or merged_compat != compat or not listed:
            stage(grok_path, render_grok(grok_text, config, merged, merged_compat).encode("utf-8"))
        return {"grok": fingerprints(desired), "grokClaudeCompat": fingerprints(GROK_CLAUDE_COMPAT),
                "grokSkillsIgnore": fingerprints({GROK_IGNORED_SKILLS: True})}

    section("claude", claude)
    section("codex", codex)
    if provider_enabled(home, "antigravity"):
        section("antigravity", antigravity)
    if provider_enabled(home, "grok"):
        section("grok", grok)
    order = ("claude", "codex", "claudePlugins", "antigravity", "grok", "grokClaudeCompat", "grokSkillsIgnore")
    next_state = {"version": 1, **{key: owned[key] for key in order if key in owned and (owned[key] or key in
                                                                                       ("claude", "codex", "claudePlugins"))}}
    before = state_path.read_bytes() if state_path.exists() else None
    if before != json_bytes(next_state):
        changes.append(Change(state_path, before, json_bytes(next_state)))
    return changes, next_state, state_path


def atomic_write(path: Path, content: bytes) -> None:
    # Through a symlink to its target, so a native file managed elsewhere stays a link.
    path = Path(os.path.realpath(path))
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


def write_private(path: Path, content: bytes) -> None:
    """Backups can hold credentials from 0600 native files; never let the umask widen them."""
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "wb") as handle:
        handle.write(content)


def private_backup_folder(root: Path, name: str) -> Path:
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    root.chmod(0o700)
    folder = root / name
    folder.mkdir(mode=0o700)
    folder.chmod(0o700)  # mkdir's mode is masked by the umask, which can drop even the owner's x
    return folder


# The sync-state keys each provider's section records.
STATE_KEYS = {"claude": ("claude", "claudePlugins"), "codex": ("codex",), "antigravity": ("antigravity",),
              "grok": ("grok", "grokClaudeCompat", "grokSkillsIgnore")}


def undo(changes: list[Change], links: list[SkillLink]) -> list[str]:
    """Reverse completed writes and links; returns paths left alone because they changed since."""
    left = []
    for link in reversed(links):
        try:
            remove_created_link(link)
        except OSError:
            left.append(str(link.path))
    for change in reversed(changes):
        try:
            if (change.path.read_bytes() if change.path.exists() else None) != change.after:
                left.append(str(change.path))
                continue
            if change.before is None:
                # The file was written through any link at the path; remove what was written.
                Path(os.path.realpath(change.path)).unlink(missing_ok=True)
            else:
                atomic_write(change.path, change.before)
        except OSError:
            left.append(str(change.path))
    return left


def apply(home: Path, registry_path: Path, problems: dict | None = None) -> list[Change | SkillLink]:
    """Apply staged changes. With `problems`, each provider's files and links are one transaction:
    a provider that refuses or fails to write is recorded there, undone and keeps its previous
    state while the others are applied (provider startup). Without it any failure undoes all."""
    with sync_lock(home):
        # Links first: a provider whose links collide is skipped whole by prepare.
        links = plan_skill_links(home, problems)
        changes, next_state, state_path = prepare(home, registry_path, problems)
        if problems:
            links = [link for link in links if link.provider not in problems]
        if not changes and not links:
            return []
        backup = private_backup_folder(home / ".agents/backups", datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ"))
        manifest = []
        for index, change in enumerate(changes):
            if (change.path.read_bytes() if change.path.exists() else None) != change.before:
                raise SyncError(f"Concurrent edit to {change.path}; nothing applied")
            saved = f"{index}-{change.path.name}"
            if change.before is not None:
                write_private(backup / saved, change.before)
            manifest.append({"path": str(change.path), "backup": saved if change.before is not None else None})
        write_private(backup / "manifest.json", json_bytes({"files": manifest, "createdLinks": [{"path": str(link.path), "target": str(link.target)} for link in links]}))
        native = [change for change in changes if change.provider is not None]
        prepared_state = state_path.read_bytes() if state_path.exists() else None
        prior_state = read_json(state_path)

        def refuse(provider: str, error: Exception) -> None:
            problems[provider] = error
            for key in STATE_KEYS[provider]:
                if key in prior_state:
                    next_state[key] = prior_state[key]
                else:
                    next_state.pop(key, None)

        owners: dict[str, set[str]] = {}
        for change in native:
            owners.setdefault(os.path.realpath(change.path), set()).add(change.provider)
        shared = sorted({provider for providers in owners.values() if len(providers) > 1 for provider in providers})
        if shared:
            # Separate transactions cannot share a file: one provider's write would change the other's.
            error = SyncError(f"{', '.join(shared)} write the same file through links; give each its own file")
            if problems is None:
                raise error
            for provider in shared:
                refuse(provider, error)
            native = [change for change in native if change.provider not in shared]
            links = [link for link in links if link.provider not in shared]
        written: list[Change] = []
        created: list[SkillLink] = []
        for provider in dict.fromkeys([change.provider for change in native] + [link.provider for link in links]):
            done: list[Change] = []
            done_links: list[SkillLink] = []
            try:
                for change in (change for change in native if change.provider == provider):
                    # Recheck immediately before replacement as native harnesses may save settings.
                    if (change.path.read_bytes() if change.path.exists() else None) != change.before:
                        raise SyncError(f"Concurrent edit to {change.path}")
                    atomic_write(change.path, change.after)
                    done.append(change)
                for link in (link for link in links if link.provider == provider):
                    create_skill_link(link)  # a read-only skills folder fails here
                    done_links.append(link)
            except (SyncError, ValueError, OSError) as error:
                left = undo(done, done_links)
                if problems is None:
                    left += undo(written, created)
                    if left:
                        raise SyncError(f"Write failed; concurrent edits left untouched in {', '.join(left)}. Original backups: {backup}") from error
                    raise
                refuse(provider, SyncError(f"{error}; changed since and left alone: {', '.join(left)}") if left else error)
                continue
            written += done
            created += done_links
        try:
            before = state_path.read_bytes() if state_path.exists() else None
            if before != prepared_state:
                raise SyncError(f"Concurrent edit to {state_path}; nothing applied")
            if before != json_bytes(next_state):
                atomic_write(state_path, json_bytes(next_state))
                written.append(Change(state_path, before, json_bytes(next_state)))
        except Exception:
            undo([change for change in written if change.path != state_path], created)
            raise
        return [*written, *created]


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

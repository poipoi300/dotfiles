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
SKILLS_BEGIN = "# BEGIN AGENT-COMMON MANAGED SKILLS\n"
SKILLS_END = "# END AGENT-COMMON MANAGED SKILLS\n"
CLAUDE_SKILL_CREATOR_KEY = "anthropic-skills:skill-creator"
CODEX_SKILL_CREATOR_PATH = "~/.codex/skills/.system/skill-creator/SKILL.md"
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
    # The block is written last, so Codex appends its own tables (such as hooks.state) inside it,
    # before END. Split at column-0 table headers and hand every non-MCP table back to the file.
    heads = [m.start() for m in re.finditer(r"(?m)^\[", block)]
    ours, foreign = [block[:heads[0]] if heads else block], []
    try:
        if tomllib.loads(ours[0]):
            raise SyncError("Unexpected settings in the Codex managed MCP block")
        for start, end in zip(heads, heads[1:] + [len(block)]):
            chunk = block[start:end]
            # A chunk normally holds one top-level key; a mixed one (an indented header) lands in
            # foreign, and the equality check below refuses it.
            (ours if set(tomllib.loads(chunk)) == {"mcp_servers"} else foreign).append(chunk)
        servers = tomllib.loads("".join(ours)).get("mcp_servers", {})
        whole = tomllib.loads(block)
    except tomllib.TOMLDecodeError as error:
        raise SyncError(f"Cannot split the Codex managed MCP block: {error}") from None
    if servers != whole.get("mcp_servers", {}):
        raise SyncError("Unexpected settings in the Codex managed MCP block")
    if not foreign:
        return before + after, servers
    moved = "".join(chunk if chunk.endswith("\n") else chunk + "\n" for chunk in foreign)
    return before.rstrip("\r\n") + "\n\n" + moved + after, servers


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


def _norm_codex_skill_path(raw: str, home: Path) -> str:
    if raw.startswith("~/"):
        return os.path.normpath(str(home / raw[2:]))
    return os.path.normpath(raw)


def split_managed_skills_toml(text: str) -> tuple[str, dict[str, dict], bool]:
    """Split the optional Codex managed skills block, moving any Codex-appended tables back out."""
    starts = list(re.finditer(r"(?m)^" + re.escape(SKILLS_BEGIN.rstrip()) + r"\r?\n", text))
    ends = list(re.finditer(r"(?m)^" + re.escape(SKILLS_END.rstrip()) + r"\r?\n", text))
    if not starts and not ends:
        return text, {}, False
    if len(starts) != 1 or len(ends) != 1 or ends[0].start() < starts[0].end():
        raise SyncError("Codex managed skills block markers are missing or repeated")
    before, block, after = text[:starts[0].start()], text[starts[0].end():ends[0].start()], text[ends[0].end():]
    heads = [m.start() for m in re.finditer(r"(?m)^\[", block)]
    ours, foreign = [block[:heads[0]] if heads else block], []
    try:
        if tomllib.loads(ours[0]):
            raise SyncError("Unexpected settings in the Codex managed skills block")
        for start, end in zip(heads, heads[1:] + [len(block)]):
            chunk = block[start:end]
            parsed_chunk = tomllib.loads(chunk)
            if set(parsed_chunk) == {"skills"} and isinstance(parsed_chunk["skills"], dict) and set(parsed_chunk["skills"]) <= {"config"}:
                ours.append(chunk)
            else:
                foreign.append(chunk)
        parsed_ours = tomllib.loads("".join(ours))
        whole = tomllib.loads(block)
    except tomllib.TOMLDecodeError as error:
        raise SyncError(f"Cannot split the Codex managed skills block: {error}") from None
    ours_entries = parsed_ours.get("skills", {}).get("config", [])
    whole_entries = whole.get("skills", {}).get("config", [])
    if not isinstance(ours_entries, list) or ours_entries != whole_entries:
        raise SyncError("Unexpected settings in the Codex managed skills block")
    entries: dict[str, dict] = {}
    for entry in ours_entries:
        if not isinstance(entry, dict) or not isinstance(entry.get("path"), str) or not entry["path"].strip():
            raise SyncError("Unexpected entry in the Codex managed skills block")
        if entry["path"] in entries:
            raise SyncError(f"Duplicate entry in the Codex managed skills block: {entry['path']}")
        entries[entry["path"]] = entry
    if not foreign:
        return before + after, entries, True
    moved = "".join(chunk if chunk.endswith("\n") else chunk + "\n" for chunk in foreign)
    return before.rstrip("\r\n") + "\n\n" + moved + after, entries, True


def codex_skills_toml_block(entries: dict[str, dict]) -> str:
    lines = [SKILLS_BEGIN.rstrip()]
    for path in sorted(entries):
        entry = entries[path]
        lines.append("[[skills.config]]")
        for key, value in entry.items():
            lines.append(f"{key} = {json.dumps(value, ensure_ascii=False)}")
        lines.append("")
    lines.append(SKILLS_END.rstrip())
    return "\n".join(lines) + "\n"


def _strip_managed_codex_skills(parsed: dict, managed_entries: list[dict], unmanaged: dict) -> dict:
    """Return a copy of `parsed` with only `managed_entries` removed from `skills.config`."""
    result = {k: v for k, v in parsed.items() if k not in ("mcp_servers", "skills")}
    if "skills" not in parsed:
        return result
    if not isinstance(parsed["skills"], dict):
        result["skills"] = parsed["skills"]
        return result
    skills = dict(parsed["skills"])
    if isinstance(skills.get("config"), list):
        remaining = list(skills["config"])
        for managed in managed_entries:
            try:
                remaining.remove(managed)
            except ValueError:
                pass
        unmanaged_skills = unmanaged.get("skills") if isinstance(unmanaged.get("skills"), dict) else {}
        if remaining or "config" in unmanaged_skills:
            skills["config"] = remaining
        else:
            skills.pop("config", None)
    if skills or "skills" in unmanaged:
        result["skills"] = skills
    return result


def has_shared_skill_creator(home: Path) -> bool:
    return (home / ".agents/skills/skill-creator/SKILL.md").is_file()


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


@dataclass
class StaleSkillLink:
    path: Path
    target: Path
    provider: str = "claude"


def skill_link_roots(home: Path) -> dict[str, Path]:
    """Where shared skills are linked, by provider.

    Grok scans ~/.agents/skills itself (including per-file symlinks). Codex also scans
    ~/.agents/skills, but ignores symlinked SKILL.md files (such as arcus's per-file
    links), so those are linked as directory symlinks into ~/.codex/skills pointing at
    the real skill directory where SKILL.md is a regular file.

    Antigravity's global skills folder is also the one T3 links into its Antigravity profile."""
    roots = {"claude": home / ".claude/skills", "codex": home / ".codex/skills"}
    if provider_enabled(home, "antigravity"):
        roots["antigravity"] = home / ".gemini/config/skills"
    return roots


def _codex_symlink_skill_target(skill: Path) -> Path | None:
    """Return the real skill directory for Codex when SKILL.md is a per-file symlink."""
    skill_md = skill / "SKILL.md"
    if skill.is_symlink() or not skill_md.is_symlink():
        return None
    target = skill_md.resolve().parent
    return target if (target / "SKILL.md").is_file() and not (target / "SKILL.md").is_symlink() else None


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
        if skill.name.startswith(".") or not NAME.fullmatch(skill.name) or skill.name in {"synced", "skill-installer"}:
            raise SyncError(f"Shared skill name conflicts with runtime-managed skills: {skill.name}")
        codex_target = _codex_symlink_skill_target(skill)
        for provider, root in roots.items():
            if provider == "codex":
                if codex_target is None:
                    continue
                target = codex_target
            else:
                target = skill.resolve()
            destination = root / skill.name
            if os.path.lexists(destination):
                if not destination.exists() or not os.path.samefile(destination, target):
                    error = SyncError(f"{provider.capitalize()} skill collision: {skill.name}; "
                                      "archive or reconcile the existing folder first")
                    if problems is None:
                        raise error
                    problems.setdefault(provider, error)
            else:
                links.append(SkillLink(destination, target, provider))
    return links


def plan_stale_skill_links(
    home: Path,
    problems: dict | None = None,
    *,
    claimed_paths: set[Path] | None = None,
) -> list[StaleSkillLink]:
    """Dangling symlinks in provider skill folders whose target sits directly inside ~/.agents/skills
    or one of its resolved per-file-symlink skill roots."""
    source = home / ".agents/skills"
    if not source.is_dir():
        return []
    allowed_parents = {Path(os.path.normpath(source)), source.resolve()}
    for skill in source.iterdir():
        if skill.is_dir():
            codex_target = _codex_symlink_skill_target(skill)
            if codex_target is not None:
                allowed_parents.add(codex_target.parent)
    stale: list[StaleSkillLink] = []
    for provider, root in skill_link_roots(home).items():
        if problems is not None and provider in problems:
            continue
        if not root.is_dir():
            continue
        resolved_parent = root.resolve()
        for entry in sorted(root.iterdir()):
            if claimed_paths and entry in claimed_paths:
                continue
            if not entry.is_symlink() or entry.exists():
                continue
            try:
                raw_target = os.readlink(entry)
            except OSError:
                continue
            cand_raw = Path(os.path.normpath(entry.parent / raw_target))
            cand_res = Path(os.path.normpath(resolved_parent / raw_target))
            if cand_raw.parent in allowed_parents or cand_res.parent in allowed_parents:
                stale.append(StaleSkillLink(entry, Path(raw_target), provider))
    return stale


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

        # Disable Anthropic's account-synced skill-creator when our unified shared skill-creator exists.
        desired_overrides = {CLAUDE_SKILL_CREATOR_KEY: "off"} if has_shared_skill_creator(home) else {}
        current_overrides = settings.get("skillOverrides", {})
        if not isinstance(current_overrides, dict) or any(not isinstance(v, str) for v in current_overrides.values()):
            raise SyncError("Claude skillOverrides must map skill names to strings")
        prior_overrides = dict(state.get("claudeSkillOverrides", {}))
        for name, desired_val in desired_overrides.items():
            if name not in prior_overrides and current_overrides.get(name) == desired_val:
                prior_overrides[name] = fingerprint(desired_val)
        new_overrides = reconcile(current_overrides, desired_overrides, prior_overrides, "Claude skill override")

        settings_changed = False
        if new_plugins != current_plugins:
            settings["enabledPlugins"] = new_plugins
            settings_changed = True
        if new_overrides != current_overrides:
            if new_overrides:
                settings["skillOverrides"] = new_overrides
            else:
                settings.pop("skillOverrides", None)
            settings_changed = True
        if settings_changed:
            stage(settings_path, json_bytes(settings))
        return {
            "claude": fingerprints(desired_claude),
            "claudePlugins": fingerprints(plugins),
            "claudeSkillOverrides": fingerprints(desired_overrides),
        }

    def codex(stage) -> dict:
        codex_path = home / ".codex/config.toml"
        codex_bytes = codex_path.read_bytes() if codex_path.exists() else None
        codex_text = codex_bytes.decode("utf-8-sig") if codex_bytes is not None else ""
        original_toml = tomllib.loads(codex_text)
        base_no_mcp, current_mcp = split_managed_toml(codex_text)
        mcp_marker_present = bool(
            re.search(r"(?m)^" + re.escape(BEGIN.rstrip()) + r"\r?\n", codex_text)
            or re.search(r"(?m)^" + re.escape(END.rstrip()) + r"\r?\n", codex_text)
        )
        base, current_skills, skills_marker_present = split_managed_skills_toml(base_no_mcp)

        desired_codex = native_servers(registry, "codex")
        reconciled_mcp = reconcile(current_mcp, desired_codex, state.get("codex", {}), "Codex MCP")
        unmanaged_toml = tomllib.loads(base)
        unmanaged_mcp = unmanaged_toml.get("mcp_servers", {})
        if set(unmanaged_mcp) & set(reconciled_mcp):
            raise SyncError(f"Unmanaged Codex MCP name collision: {', '.join(sorted(set(unmanaged_mcp) & set(reconciled_mcp)))}")

        desired_skills: dict[str, dict] = (
            {CODEX_SKILL_CREATOR_PATH: {"path": CODEX_SKILL_CREATOR_PATH, "enabled": False}}
            if has_shared_skill_creator(home)
            else {}
        )
        unmanaged_skills_raw = unmanaged_toml.get("skills", {}).get("config", []) if isinstance(unmanaged_toml.get("skills"), dict) else []
        if unmanaged_skills_raw and not isinstance(unmanaged_skills_raw, list):
            raise SyncError("Codex skills.config must be an array of tables")
        # Adopt an identical unmanaged disable entry (skip duplicating); refuse conflicting ones.
        effective_desired_skills = dict(desired_skills)
        for path, wanted_entry in list(effective_desired_skills.items()):
            wanted_norm = _norm_codex_skill_path(path, home)
            for existing in unmanaged_skills_raw:
                if not isinstance(existing, dict) or not isinstance(existing.get("path"), str):
                    continue
                if _norm_codex_skill_path(existing["path"], home) != wanted_norm:
                    continue
                if existing.get("enabled") is False and set(existing) == {"path", "enabled"}:
                    effective_desired_skills.pop(path, None)
                else:
                    raise SyncError(f"Unmanaged Codex skills.config collision: {existing['path']}")
        reconciled_skills = reconcile(
            current_skills, effective_desired_skills, state.get("codexDisabledSkills", {}), "Codex skills config"
        )

        # Preserve every byte outside our blocks on normal LF files; never serialize native settings.
        blocks = []
        if reconciled_skills:
            blocks.append(codex_skills_toml_block(reconciled_skills))
        if reconciled_mcp:
            blocks.append(toml_block(reconciled_mcp))
        stripped_base = base.rstrip("\r\n")
        if blocks:
            prefix = (stripped_base + "\n\n") if stripped_base else ""
            rendered = prefix + "\n".join(blocks)
        else:
            rendered = (stripped_base + "\n") if stripped_base else ""

        try:
            checked = tomllib.loads(rendered)
        except tomllib.TOMLDecodeError as error:
            raise SyncError(f"Rendered Codex config is invalid TOML: {error}") from None
        checked_unmanaged = _strip_managed_codex_skills(checked, list(reconciled_skills.values()), unmanaged_toml)
        original_unmanaged = _strip_managed_codex_skills(original_toml, list(current_skills.values()), unmanaged_toml)
        if checked_unmanaged != original_unmanaged:
            raise SyncError("Rendering would change unrelated Codex settings")
        if {k: v for k, v in checked.get("mcp_servers", {}).items() if k not in reconciled_mcp} != unmanaged_mcp:
            raise SyncError("Rendering would change unmanaged Codex MCP entries")
        should_write = bool(
            reconciled_mcp or current_mcp or mcp_marker_present
            or reconciled_skills or current_skills or skills_marker_present
        )
        if should_write:
            stage(codex_path, rendered.encode("utf-8"))
        return {
            "codex": fingerprints(reconciled_mcp),
            "codexDisabledSkills": fingerprints(reconciled_skills),
        }

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
    order = ("claude", "codex", "claudePlugins", "claudeSkillOverrides", "codexDisabledSkills",
             "antigravity", "grok", "grokClaudeCompat", "grokSkillsIgnore")
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
STATE_KEYS = {
    "claude": ("claude", "claudePlugins", "claudeSkillOverrides"),
    "codex": ("codex", "codexDisabledSkills"),
    "antigravity": ("antigravity",),
    "grok": ("grok", "grokClaudeCompat", "grokSkillsIgnore"),
}


def undo(
    changes: list[Change],
    links: list[SkillLink],
    removed_links: list[StaleSkillLink] = (),
) -> list[str]:
    """Reverse completed writes, created links, and removed dangling links."""
    left = []
    for link in reversed(links):
        try:
            remove_created_link(link)
        except OSError:
            left.append(str(link.path))
    for stale in reversed(removed_links):
        try:
            if not os.path.lexists(stale.path):
                stale.path.symlink_to(stale.target, target_is_directory=True)
        except OSError:
            left.append(str(stale.path))
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


def apply(home: Path, registry_path: Path, problems: dict | None = None) -> list[Change | SkillLink | StaleSkillLink]:
    """Apply staged changes. With `problems`, each provider's files and links are one transaction:
    a provider that refuses or fails to write is recorded there, undone and keeps its previous
    state while the others are applied (provider startup). Without it any failure undoes all."""
    with sync_lock(home):
        # Links first: a provider whose links collide is skipped whole by prepare.
        links = plan_skill_links(home, problems)
        stale_links = plan_stale_skill_links(home, problems, claimed_paths={link.path for link in links})
        changes, next_state, state_path = prepare(home, registry_path, problems)
        if problems:
            links = [link for link in links if link.provider not in problems]
            stale_links = [link for link in stale_links if link.provider not in problems]
        if not changes and not links and not stale_links:
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
        write_private(
            backup / "manifest.json",
            json_bytes({
                "files": manifest,
                "createdLinks": [{"path": str(link.path), "target": str(link.target)} for link in links],
                "removedLinks": [{"path": str(link.path), "target": str(link.target)} for link in stale_links],
            }),
        )
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
            stale_links = [link for link in stale_links if link.provider not in shared]
        written: list[Change] = []
        created: list[SkillLink] = []
        removed: list[StaleSkillLink] = []
        providers_order = dict.fromkeys(
            [change.provider for change in native]
            + [link.provider for link in links]
            + [link.provider for link in stale_links]
        )
        for provider in providers_order:
            done: list[Change] = []
            done_links: list[SkillLink] = []
            done_removed: list[StaleSkillLink] = []
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
                for stale in (link for link in stale_links if link.provider == provider):
                    if stale.path.is_symlink() and not stale.path.exists():
                        stale.path.unlink()
                        done_removed.append(stale)
            except (SyncError, ValueError, OSError) as error:
                left = undo(done, done_links, done_removed)
                if problems is None:
                    left += undo(written, created, removed)
                    if left:
                        raise SyncError(f"Write failed; concurrent edits left untouched in {', '.join(left)}. Original backups: {backup}") from error
                    raise
                refuse(provider, SyncError(f"{error}; changed since and left alone: {', '.join(left)}") if left else error)
                continue
            written += done
            created += done_links
            removed += done_removed
        try:
            before = state_path.read_bytes() if state_path.exists() else None
            if before != prepared_state:
                raise SyncError(f"Concurrent edit to {state_path}; nothing applied")
            if before != json_bytes(next_state):
                atomic_write(state_path, json_bytes(next_state))
                written.append(Change(state_path, before, json_bytes(next_state)))
        except Exception:
            undo([change for change in written if change.path != state_path], created, removed)
            raise
        return [*written, *created, *removed]


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
        if args.command == "check" or args.dry_run:
            planned_links = plan_skill_links(home)
            planned_stale = plan_stale_skill_links(home, claimed_paths={link.path for link in planned_links})
            changes = [*prepare(home, registry)[0], *planned_links, *planned_stale]
        else:
            changes = apply(home, registry)
        for change in changes:
            if isinstance(change, StaleSkillLink):
                verb = "would remove dangling link" if args.command == "check" or args.dry_run else "removed dangling link"
            else:
                verb = "would update" if args.command == "check" or args.dry_run else "updated"
            print(f"{verb}: {change.path}")
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

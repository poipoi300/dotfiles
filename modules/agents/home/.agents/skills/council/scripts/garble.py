#!/usr/bin/env python3
# /// script
# requires-python = ">=3.12"
# dependencies = [
#     "tree-sitter==0.26.1",
#     "tree-sitter-python==0.25.0",
#     "tree-sitter-javascript==0.25.0",
#     "tree-sitter-typescript==0.23.2",
#     "tree-sitter-rust==0.24.2",
# ]
# ///
"""Deterministic code garbler for blind structural code review.

Rewrites supported source trees (Python, JavaScript/JSX, TypeScript/TSX, Rust)
so reviewer agents evaluate structure and control flow without anchoring on
names, comments, or string literals, then maps reported findings back to real
symbols and paths via ``reveal``.

Subcommands:
    garble  Garble a candidate checkout (and optional base checkout for diffs).
    reveal  Map garbled identifier, path, and string tokens back to originals.

Known limitations:
    - Renaming is by identifier name, not by static type or scope. A
      project-defined name such as ``get`` also renames ``.get`` on external
      objects across both trees.
    - All supported languages in a checkout share one identifier namespace and
      one protected built-in/keyword set, so a name protected in one supported
      language stays unrenamed in all of them.
    - Dynamic string-based dispatch (``getattr(obj, "method")``, reflection,
      format-string captured identifiers in Rust) replaces string contents with
      ``sN`` tokens rather than linking them to identifier tokens.
    - Macro-generated identifiers that never appear literally in source are not
      discovered.
    - Path components use the never-rename set (built-ins, keywords, conventional
      stems such as ``index``), so a directory or file named like one stays
      readable in paths and Python module paths still match their module
      identifiers.
    - Rust char literals (``'x'``) are left as written.
    - Bare JS/TS specifiers of non-code assets under a ``baseUrl`` (for example
      ``"assets/logo.svg"``) cannot be told from package names and stay verbatim.
    - ``module.exports = ...`` does not define ``exports``, so CommonJS stays readable.
"""

from __future__ import annotations

import argparse
import builtins
import difflib
import hashlib
import json
import keyword
import re
import subprocess
import sys
from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from functools import partial
from pathlib import Path, PurePosixPath
from urllib.parse import urlparse

import tree_sitter as ts
import tree_sitter_javascript as ts_javascript
import tree_sitter_python as ts_python
import tree_sitter_rust as ts_rust
import tree_sitter_typescript as ts_typescript

for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

# ---------------------------------------------------------------------------
# Extension classification & protected identifier tables
# ---------------------------------------------------------------------------

SUPPORTED_EXTENSIONS: dict[str, str] = {
    ".py": "python",
    ".pyi": "python",
    ".js": "javascript",
    ".mjs": "javascript",
    ".cjs": "javascript",
    ".jsx": "javascript",
    ".ts": "typescript",
    ".mts": "typescript",
    ".cts": "typescript",
    ".tsx": "tsx",
    ".rs": "rust",
}

UNSUPPORTED_EXTENSIONS: dict[str, str] = {
    ".go": "Go",
    ".java": "Java",
    ".kt": "Kotlin",
    ".kts": "Kotlin",
    ".swift": "Swift",
    ".c": "C",
    ".h": "C",
    ".cc": "C++",
    ".cpp": "C++",
    ".cxx": "C++",
    ".hh": "C++",
    ".hpp": "C++",
    ".hxx": "C++",
    ".ipp": "C++",
    ".tpp": "C++",
    ".cs": "C#",
    ".m": "Objective-C",
    ".mm": "Objective-C",
    ".rb": "Ruby",
    ".rake": "Ruby",
    ".gemspec": "Ruby",
    ".php": "PHP",
    ".phtml": "PHP",
    ".lua": "Lua",
    ".sh": "Shell",
    ".bash": "Shell",
    ".zsh": "Shell",
    ".ksh": "Shell",
    ".fish": "Fish",
    ".ps1": "PowerShell",
    ".psm1": "PowerShell",
    ".psd1": "PowerShell",
    ".vue": "Vue",
    ".svelte": "Svelte",
    ".qml": "QML",
    ".nix": "Nix",
    ".zig": "Zig",
    ".hs": "Haskell",
    ".lhs": "Haskell",
    ".ml": "OCaml",
    ".mli": "OCaml",
    ".ex": "Elixir",
    ".exs": "Elixir",
    ".erl": "Erlang",
    ".hrl": "Erlang",
    ".scala": "Scala",
    ".sc": "Scala",
    ".dart": "Dart",
    ".r": "R",
    ".jl": "Julia",
    ".pl": "Perl",
    ".pm": "Perl",
    ".sql": "SQL",
    ".glsl": "GLSL",
    ".vert": "GLSL",
    ".frag": "GLSL",
    ".geom": "GLSL",
    ".tesc": "GLSL",
    ".tese": "GLSL",
    ".comp": "GLSL",
    ".wgsl": "WGSL",
}

CONVENTIONAL_STEMS: frozenset[str] = frozenset({
    "__init__",
    "__main__",
    "mod",
    "lib",
    "main",
    "index",
})

JS_TS_KEYWORDS: frozenset[str] = frozenset({
    "abstract",
    "any",
    "as",
    "asserts",
    "async",
    "await",
    "bigint",
    "boolean",
    "break",
    "case",
    "catch",
    "class",
    "const",
    "continue",
    "debugger",
    "declare",
    "default",
    "delete",
    "do",
    "else",
    "enum",
    "export",
    "extends",
    "false",
    "finally",
    "for",
    "function",
    "if",
    "implements",
    "import",
    "in",
    "infer",
    "instanceof",
    "interface",
    "is",
    "keyof",
    "let",
    "namespace",
    "never",
    "new",
    "null",
    "number",
    "object",
    "package",
    "private",
    "protected",
    "public",
    "readonly",
    "return",
    "satisfies",
    "static",
    "string",
    "super",
    "switch",
    "symbol",
    "this",
    "throw",
    "true",
    "try",
    "typeof",
    "unknown",
    "var",
    "void",
    "while",
    "with",
    "yield",
})

JS_TS_GLOBALS: frozenset[str] = frozenset({
    "AbortController",
    "AbortSignal",
    "AggregateError",
    "Array",
    "ArrayBuffer",
    "Atomics",
    "Awaited",
    "BigInt",
    "BigInt64Array",
    "BigUint64Array",
    "Blob",
    "Boolean",
    "Buffer",
    "Capitalize",
    "ConstructorParameters",
    "CustomEvent",
    "DataView",
    "Date",
    "Error",
    "EvalError",
    "Event",
    "EventTarget",
    "Exclude",
    "Extract",
    "File",
    "Float32Array",
    "Float64Array",
    "FormData",
    "Function",
    "Headers",
    "Infinity",
    "InstanceType",
    "Int16Array",
    "Int32Array",
    "Int8Array",
    "Intl",
    "JSON",
    "Lowercase",
    "Map",
    "Math",
    "NaN",
    "NonNullable",
    "Number",
    "Object",
    "Omit",
    "Parameters",
    "Partial",
    "Pick",
    "Promise",
    "Proxy",
    "RangeError",
    "ReadableStream",
    "Readonly",
    "Record",
    "ReferenceError",
    "Reflect",
    "RegExp",
    "Request",
    "Required",
    "Response",
    "ReturnType",
    "Set",
    "SharedArrayBuffer",
    "String",
    "Symbol",
    "SyntaxError",
    "TextDecoder",
    "TextEncoder",
    "ThisType",
    "TransformStream",
    "TypeError",
    "URIError",
    "URL",
    "URLSearchParams",
    "Uint16Array",
    "Uint32Array",
    "Uint8Array",
    "Uint8ClampedArray",
    "Uncapitalize",
    "Uppercase",
    "WeakMap",
    "WeakSet",
    "WebAssembly",
    "WritableStream",
    "arguments",
    "atob",
    "btoa",
    "clearInterval",
    "clearTimeout",
    "console",
    "constructor",
    "decodeURI",
    "decodeURIComponent",
    "document",
    "encodeURI",
    "encodeURIComponent",
    "fetch",
    "globalThis",
    "history",
    "isFinite",
    "isNaN",
    "length",
    "localStorage",
    "location",
    "navigator",
    "parseFloat",
    "parseInt",
    "process",
    "prototype",
    "queueMicrotask",
    "sessionStorage",
    "setInterval",
    "setTimeout",
    "structuredClone",
    "undefined",
    "window",
})

RUST_KEYWORDS: frozenset[str] = frozenset({
    "Self",
    "abstract",
    "as",
    "async",
    "await",
    "become",
    "box",
    "break",
    "const",
    "continue",
    "crate",
    "do",
    "dyn",
    "else",
    "enum",
    "extern",
    "false",
    "final",
    "fn",
    "for",
    "if",
    "impl",
    "in",
    "let",
    "loop",
    "macro",
    "match",
    "mod",
    "move",
    "mut",
    "override",
    "priv",
    "pub",
    "ref",
    "return",
    "self",
    "static",
    "struct",
    "super",
    "trait",
    "true",
    "try",
    "type",
    "typeof",
    "unsafe",
    "unsized",
    "use",
    "virtual",
    "where",
    "while",
    "yield",
})

RUST_PRIMITIVES_AND_PRELUDE: frozenset[str] = frozenset({
    "AsMut",
    "AsRef",
    "AsyncFn",
    "AsyncFnMut",
    "AsyncFnOnce",
    "Box",
    "Clone",
    "Copy",
    "Default",
    "DoubleEndedIterator",
    "Drop",
    "Eq",
    "Err",
    "ExactSizeIterator",
    "Extend",
    "Fn",
    "FnMut",
    "FnOnce",
    "From",
    "FromIterator",
    "Future",
    "Into",
    "IntoFuture",
    "IntoIterator",
    "Iterator",
    "None",
    "Ok",
    "Option",
    "Ord",
    "PartialEq",
    "PartialOrd",
    "Result",
    "Send",
    "Sized",
    "Some",
    "String",
    "Sync",
    "ToOwned",
    "ToString",
    "TryFrom",
    "TryInto",
    "Unpin",
    "Vec",
    "bool",
    "char",
    "f128",
    "f16",
    "f32",
    "f64",
    "i128",
    "i16",
    "i32",
    "i64",
    "i8",
    "isize",
    "str",
    "u128",
    "u16",
    "u32",
    "u64",
    "u8",
    "usize",
})

SPECIAL_NAMES: frozenset[str] = frozenset({
    "_",
    "self",
    "Self",
    "this",
    "super",
    "crate",
    "main",
})

NEVER_RENAME: frozenset[str] = frozenset(
    set(dir(builtins))
    | set(keyword.kwlist)
    | set(keyword.softkwlist)
    | JS_TS_KEYWORDS
    | JS_TS_GLOBALS
    | RUST_KEYWORDS
    | RUST_PRIMITIVES_AND_PRELUDE
    | SPECIAL_NAMES
    | CONVENTIONAL_STEMS
)

JS_TS_LANGUAGES: frozenset[str] = frozenset({"javascript", "typescript", "tsx"})
JS_TS_SPECIFIER_EXTENSIONS: frozenset[str] = frozenset({
    ".js",
    ".mjs",
    ".cjs",
    ".jsx",
    ".ts",
    ".mts",
    ".cts",
    ".tsx",
})

IDENT_NODE_TYPES: frozenset[str] = frozenset({
    "identifier",
    "property_identifier",
    "shorthand_property_identifier",
    "shorthand_property_identifier_pattern",
    "type_identifier",
    "field_identifier",
    "shorthand_field_identifier",
    "private_property_identifier",
    "statement_identifier",
    "metavariable",
})

COMMENT_NODE_TYPES: frozenset[str] = frozenset({
    "comment",
    "line_comment",
    "block_comment",
    "html_comment",
})

TOKEN_RE = re.compile(r"\b([ftvs][1-9][0-9]*)\b")
PY_COMMON_SRC_ROOTS: frozenset[str] = frozenset({"src", "lib", "scripts", "tests"})
RUST_EXTERNAL_CRATES: frozenset[str] = frozenset({"std", "core", "alloc", "proc_macro", "test"})

_PARSERS: dict[str, ts.Parser] = {}


def is_never_renamed(name: str) -> bool:
    """Return True if an identifier or path component must stay untouched."""
    if not name or name in NEVER_RENAME:
        return True
    return len(name) >= 5 and name.startswith("__") and name.endswith("__")


def get_parser(language: str) -> ts.Parser:
    """Return a cached tree-sitter Parser for one supported language key."""
    parser = _PARSERS.get(language)
    if parser is not None:
        return parser
    language_ptrs = {
        "python": ts_python.language,
        "javascript": ts_javascript.language,
        "typescript": ts_typescript.language_typescript,
        "tsx": ts_typescript.language_tsx,
        "rust": ts_rust.language,
    }
    parser = ts.Parser(ts.Language(language_ptrs[language]()))
    _PARSERS[language] = parser
    return parser


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------


class InputError(Exception):
    """Raised on CLI usage or input validation errors (exit code 2)."""


@dataclass(frozen=True)
class SourceFile:
    """A supported source file read from a git checkout."""

    rel_path: str
    language: str
    content: bytes


@dataclass(frozen=True)
class ParsedFile:
    """A parsed supported source file."""

    source: SourceFile
    tree: ts.Tree
    had_parse_error: bool


@dataclass
class TreeScan:
    """Classification summary and supported files collected from one checkout."""

    supported_files: dict[str, SourceFile]
    supported_counts: dict[str, int]
    unsupported_files: dict[str, UnsupportedFile]
    omitted_non_code: int
    skipped_symlinks: int


@dataclass(frozen=True)
class UnsupportedFile:
    """An unsupported-language file, identified by language, extension and content digest."""

    language: str
    extension: str
    digest: str


@dataclass(frozen=True)
class Definition:
    """A project-defined identifier discovered in source order."""

    name: str
    category: str  # "f", "t", or "v"
    strong: bool


# ---------------------------------------------------------------------------
# Git checkout inspection & file classification
# ---------------------------------------------------------------------------


def ensure_git_checkout(directory: Path, flag_name: str) -> None:
    """Verify that ``directory`` is an existing directory inside a git worktree."""
    if not directory.exists() or not directory.is_dir():
        raise InputError(f"{flag_name} is not an existing directory: {directory}")
    proc = subprocess.run(
        ["git", "-C", str(directory), "rev-parse", "--is-inside-work-tree"],
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0 or proc.stdout.strip() != "true":
        raise InputError(f"{flag_name} is not a git checkout: {directory}")


def list_git_files(directory: Path) -> list[str]:
    """Return sorted relative POSIX paths of tracked and untracked non-ignored files."""
    proc = subprocess.run(
        ["git", "-C", str(directory), "ls-files", "-co", "--exclude-standard", "-z"],
        capture_output=True,
        check=False,
    )
    if proc.returncode != 0:
        stderr = proc.stderr.decode("utf-8", errors="replace").strip()
        raise InputError(f"git ls-files failed for {directory}: {stderr}")
    entries = {
        raw.decode("utf-8", errors="surrogateescape")
        for raw in proc.stdout.split(b"\0")
        if raw
    }
    return sorted(entries)


def _has_symlink_component(root: Path, rel_path: str) -> bool:
    """Return True if ``rel_path`` or any intermediate component under ``root`` is a symlink."""
    current = root
    for part in PurePosixPath(rel_path).parts:
        current = current / part
        if current.is_symlink():
            return True
    return False


def scan_tree(root: Path) -> TreeScan:
    """Classify all git-visible files under ``root`` and read supported sources."""
    supported_files: dict[str, SourceFile] = {}
    supported_counts: dict[str, int] = {}
    unsupported_files: dict[str, UnsupportedFile] = {}
    omitted_non_code = 0
    skipped_symlinks = 0

    for rel_path in list_git_files(root):
        if _has_symlink_component(root, rel_path):
            skipped_symlinks += 1
            continue
        full_path = root / rel_path
        if not full_path.is_file():
            continue
        suffix = PurePosixPath(rel_path).suffix.lower()
        if suffix in SUPPORTED_EXTENSIONS:
            language = SUPPORTED_EXTENSIONS[suffix]
            supported_files[rel_path] = SourceFile(
                rel_path=rel_path,
                language=language,
                content=full_path.read_bytes(),
            )
            supported_counts[language] = supported_counts.get(language, 0) + 1
        elif suffix in UNSUPPORTED_EXTENSIONS:
            unsupported_files[rel_path] = UnsupportedFile(
                language=UNSUPPORTED_EXTENSIONS[suffix],
                extension=suffix,
                digest=hashlib.sha256(full_path.read_bytes()).hexdigest(),
            )
        else:
            omitted_non_code += 1

    sorted_supported = {lang: supported_counts[lang] for lang in sorted(supported_counts)}
    return TreeScan(
        supported_files=supported_files,
        supported_counts=sorted_supported,
        unsupported_files=unsupported_files,
        omitted_non_code=omitted_non_code,
        skipped_symlinks=skipped_symlinks,
    )


def summarize_unsupported(files: Iterable[UnsupportedFile]) -> dict[str, dict[str, object]]:
    """Group unsupported files by language as ``{language: {"extensions", "files"}}``."""
    extensions: dict[str, set[str]] = {}
    counts: dict[str, int] = {}
    for item in files:
        extensions.setdefault(item.language, set()).add(item.extension)
        counts[item.language] = counts.get(item.language, 0) + 1
    return {
        language: {"extensions": sorted(extensions[language]), "files": counts[language]}
        for language in sorted(counts)
    }


def changed_unsupported(
    candidate: Mapping[str, UnsupportedFile],
    base: Mapping[str, UnsupportedFile] | None,
) -> dict[str, dict[str, object]]:
    """Summarize unsupported files the change adds, deletes or modifies.

    Without a base every candidate file counts, since nothing marks it unchanged.
    """
    if base is None:
        return summarize_unsupported(candidate.values())
    changed = [item for path, item in candidate.items() if base.get(path) != item]
    changed += [item for path, item in base.items() if path not in candidate]
    return summarize_unsupported(changed)


# ---------------------------------------------------------------------------
# Internal module detection across candidate and base trees
# ---------------------------------------------------------------------------


class InternalModules:
    """Lookup table for deciding whether an import refers to project code."""

    def __init__(self, supported_paths: Iterable[str]) -> None:
        self.python_modules: set[tuple[str, ...]] = set()
        self.js_ts_suffixes: set[str] = set()
        self.rust_roots: set[str] = set()

        for rel_path in sorted(set(supported_paths)):
            posix = PurePosixPath(rel_path)
            suffix = posix.suffix.lower()
            parts = posix.parts
            if suffix in (".py", ".pyi"):
                self._register_python(posix)
            if suffix == ".rs":
                self._register_rust(posix, parts)
            if SUPPORTED_EXTENSIONS.get(suffix) in JS_TS_LANGUAGES:
                self._register_js_ts(posix)

    def _register_python(self, posix: PurePosixPath) -> None:
        parent_parts = list(posix.parent.parts)
        mod_parts = (
            parent_parts
            if posix.stem in ("__init__", "__main__")
            else [*parent_parts, posix.stem]
        )
        for prefix_len in range(1, len(mod_parts) + 1):
            self.python_modules.add(tuple(mod_parts[:prefix_len]))
        if len(mod_parts) > 1 and mod_parts[0] in PY_COMMON_SRC_ROOTS:
            trimmed = mod_parts[1:]
            for prefix_len in range(1, len(trimmed) + 1):
                self.python_modules.add(tuple(trimmed[:prefix_len]))
        if mod_parts and mod_parts[-1] not in sys.stdlib_module_names:
            self.python_modules.add((mod_parts[-1],))

    def _register_js_ts(self, posix: PurePosixPath) -> None:
        """Register every path-segment-aligned suffix of a file and its directories.

        Covers relative-free resolution through ``baseUrl``/``paths``, where
        ``"components/Button"`` names ``src/components/Button.tsx``.
        """
        no_ext = posix.with_suffix("").as_posix()
        self._add_suffixes(no_ext.split("/"))
        if no_ext.endswith(".d"):  # ``types.d.ts`` is imported as ``types``
            self._add_suffixes(no_ext.removesuffix(".d").split("/"))
        dir_parts = posix.parent.parts
        for end in range(1, len(dir_parts) + 1):
            self._add_suffixes(list(dir_parts[:end]))

    def _add_suffixes(self, parts: list[str]) -> None:
        for start in range(len(parts)):
            self.js_ts_suffixes.add("/".join(parts[start:]))

    def _register_rust(self, posix: PurePosixPath, parts: tuple[str, ...]) -> None:
        if posix.stem not in ("lib", "main", "mod"):
            self.rust_roots.add(posix.stem)
        for part in parts[:-1]:
            if part not in ("src", "tests", "benches", "examples"):
                self.rust_roots.add(part)

    def is_internal_python_module(self, dotted_parts: tuple[str, ...]) -> bool:
        """Return True if a Python dotted import path matches a project module."""
        return dotted_parts in self.python_modules

    def is_internal_js_ts_specifier(self, specifier: str) -> bool:
        """Return True if a JS/TS module specifier is relative or project-internal.

        A bare specifier is internal when, without a query, hash, trailing slash,
        extension and trailing ``/index``, it equals a path-segment-aligned suffix of a project file
        (without extension) or of a project directory.
        """
        if not specifier:
            return False
        if specifier in (".", "..") or specifier.startswith(("./", "../", "/", "@/", "~/", "#")):
            return True
        bare = specifier.split("?", 1)[0].split("#", 1)[0].rstrip("/")
        if PurePosixPath(bare).suffix.lower() in JS_TS_SPECIFIER_EXTENSIONS:
            bare = str(PurePosixPath(bare).with_suffix(""))
        bare = bare.removesuffix("/index")
        return bare in self.js_ts_suffixes

    def is_internal_rust_root(self, root_ident: str) -> bool:
        """Return True if the leading segment of a Rust ``use`` path is internal."""
        if root_ident in ("crate", "self", "super"):
            return True
        if root_ident in RUST_EXTERNAL_CRATES:
            return False
        return root_ident in self.rust_roots


# ---------------------------------------------------------------------------
# Symbol table & token allocation
# ---------------------------------------------------------------------------


class SymbolTable:
    """Shared deterministic token allocator for identifiers and string literals."""

    def __init__(self, kept_names: set[str]) -> None:
        self.kept_names = kept_names
        self.identifiers: dict[str, str] = {}
        self.strings: dict[str, str] = {}
        self._counters: dict[str, int] = {"f": 1, "t": 1, "v": 1, "s": 1}

    def _next_token(self, prefix: str) -> str:
        num = self._counters[prefix]
        while f"{prefix}{num}" in self.kept_names:
            num += 1
        self._counters[prefix] = num + 1
        return f"{prefix}{num}"

    def assign_identifier(self, name: str, category: str) -> str:
        """Return existing token for ``name``, or allocate one in ``category``."""
        existing = self.identifiers.get(name)
        if existing is not None:
            return existing
        token = self._next_token(category)
        self.identifiers[name] = token
        return token

    def assign_string(self, content: str) -> str:
        """Return existing ``sN`` token for ``content``, or allocate the next one."""
        existing = self.strings.get(content)
        if existing is not None:
            return existing
        token = self._next_token("s")
        self.strings[content] = token
        return token


# ---------------------------------------------------------------------------
# AST helpers & definition collection
# ---------------------------------------------------------------------------


def _node_text(source: bytes, node: ts.Node) -> str:
    return source[node.start_byte:node.end_byte].decode("utf-8", errors="replace")


def _preorder(root: ts.Node) -> Iterator[ts.Node]:
    """Yield ``root`` and its descendants in source order without recursion.

    Deep expression chains (thousands of ``a + b + ...`` terms) nest one AST level
    per operand, which would exhaust Python's call stack under a recursive walk.
    """
    stack: list[ts.Node] = [root]
    while stack:
        node = stack.pop()
        yield node
        stack.extend(reversed(node.children))


def _logical_ident_name(node: ts.Node, raw_text: str) -> str:
    """Strip syntactic sigils (#priv, $meta) so tokens round-trip via reveal."""
    if node.type == "private_property_identifier" and raw_text.startswith("#"):
        return raw_text[1:]
    if node.type == "metavariable" and raw_text.startswith("$"):
        return raw_text[1:]
    return raw_text


def _format_ident_replacement(node: ts.Node, raw_text: str, token: str) -> str:
    if node.type == "private_property_identifier" and raw_text.startswith("#"):
        return f"#{token}"
    if node.type == "metavariable" and raw_text.startswith("$"):
        return f"${token}"
    return token


def _emit_def(
    out: list[Definition],
    source: bytes,
    node: ts.Node | None,
    category: str,
    strong: bool = True,
) -> None:
    if node is None:
        return
    raw = _node_text(source, node)
    name = _logical_ident_name(node, raw)
    if is_never_renamed(name):
        return
    out.append(Definition(name=name, category=category, strong=strong))


def _unquote_js_ts_string(source: bytes, node: ts.Node) -> str | None:
    """Return raw inner text of a JS/TS ``string`` node, or None if malformed."""
    if node.type != "string" or len(node.children) < 2:
        return None
    start = node.children[0].end_byte
    end = node.children[-1].start_byte
    if end < start:
        return None
    return source[start:end].decode("utf-8", errors="replace")


def collect_all_identifiers(parsed: ParsedFile) -> set[str]:
    """Collect every identifier-like logical name present in a parsed file."""
    source = parsed.source.content
    seen: set[str] = set()
    stack: list[ts.Node] = [parsed.tree.root_node]
    while stack:
        node = stack.pop()
        if node.type in COMMENT_NODE_TYPES or node.type in ("meta_property", "char_literal"):
            continue
        if node.type in IDENT_NODE_TYPES:
            raw = _node_text(source, node)
            seen.add(_logical_ident_name(node, raw))
            continue
        stack.extend(reversed(node.children))
    return seen


def collect_definitions(parsed: ParsedFile, internal: InternalModules) -> list[Definition]:
    """Collect all project-defined names in source order for one parsed file."""
    language = parsed.source.language
    if language == "python":
        return _collect_python_defs(parsed.source.content, parsed.tree.root_node, internal)
    if language in JS_TS_LANGUAGES:
        return _collect_js_ts_defs(parsed.source.content, parsed.tree.root_node, internal)
    if language == "rust":
        return _collect_rust_defs(parsed.source.content, parsed.tree.root_node, internal)
    return []


# --- Python definition collection ---


def _py_collect_pattern(out: list[Definition], source: bytes, node: ts.Node | None) -> None:
    if node is None:
        return
    ntype = node.type
    if ntype == "identifier":
        _emit_def(out, source, node, "v")
        return
    if ntype == "attribute":
        _emit_def(out, source, node.child_by_field_name("attribute"), "v")
        return
    if ntype in (
        "pattern_list",
        "tuple_pattern",
        "list_pattern",
        "list_splat_pattern",
        "dictionary_splat_pattern",
        "as_pattern_target",
        "splat_pattern",
    ):
        for child in node.children:
            if child.is_named:
                _py_collect_pattern(out, source, child)
        return
    if ntype == "as_pattern":
        _py_collect_pattern(out, source, node.child_by_field_name("alias"))


def _py_collect_params(out: list[Definition], source: bytes, params_node: ts.Node | None) -> None:
    if params_node is None:
        return
    for child in params_node.children:
        if not child.is_named:
            continue
        ctype = child.type
        if ctype == "identifier":
            _emit_def(out, source, child, "v")
        elif ctype in ("default_parameter", "typed_default_parameter"):
            _emit_def(out, source, child.child_by_field_name("name"), "v")
        elif ctype == "typed_parameter":
            for sub in child.children:
                if sub.is_named and sub.type != "type":
                    _py_collect_pattern(out, source, sub)
                    break
        elif ctype in ("list_splat_pattern", "dictionary_splat_pattern", "tuple_pattern"):
            _py_collect_pattern(out, source, child)


def _py_collect_type_params(out: list[Definition], source: bytes, node: ts.Node | None) -> None:
    if node is None:
        return
    for child in node.children:
        if not child.is_named:
            continue
        if child.type == "type":
            for sub in child.children:
                if sub.type == "identifier":
                    _emit_def(out, source, sub, "t")
                elif sub.type == "constrained_type":
                    # First type child is the parameter name; second is the bound.
                    first_type = next((c for c in sub.children if c.type == "type"), None)
                    if first_type is not None:
                        ident = next((c for c in first_type.children if c.type == "identifier"), None)
                        _emit_def(out, source, ident, "t")
                elif sub.type == "splat_type":
                    ident = next((c for c in sub.children if c.type == "identifier"), None)
                    _emit_def(out, source, ident, "t")
        elif child.type == "identifier":
            _emit_def(out, source, child, "t")


def _py_collect_case_pattern(out: list[Definition], source: bytes, node: ts.Node) -> None:
    ntype = node.type
    if ntype == "case_pattern":
        for child in node.children:
            if child.is_named:
                _py_collect_case_pattern(out, source, child)
        return
    if ntype == "dotted_name":
        # A single-component dotted_name in a case pattern binds a capture variable;
        # a multi-part name like Enum.VALUE is a constant value pattern.
        idents = [c for c in node.children if c.type == "identifier"]
        if len(idents) == 1:
            _emit_def(out, source, idents[0], "v")
        return
    if ntype == "as_pattern":
        for child in node.children:
            if child.type == "identifier":
                _emit_def(out, source, child, "v")
            elif child.is_named:
                _py_collect_case_pattern(out, source, child)
        return
    if ntype == "class_pattern":
        # Skip the leading class dotted_name; recurse only into argument patterns.
        skipped_head = False
        for child in node.children:
            if not child.is_named:
                continue
            if not skipped_head and child.type == "dotted_name":
                skipped_head = True
                continue
            _py_collect_case_pattern(out, source, child)
        return
    if ntype == "keyword_pattern":
        children = [c for c in node.children if c.is_named]
        if len(children) >= 2:
            _py_collect_case_pattern(out, source, children[-1])
        return
    if ntype in ("splat_pattern", "list_pattern", "tuple_pattern", "union_pattern", "dict_pattern"):
        for child in node.children:
            if child.type == "identifier":
                _emit_def(out, source, child, "v")
            elif child.is_named:
                _py_collect_case_pattern(out, source, child)


def _collect_python_defs(
    source: bytes,
    root: ts.Node,
    internal: InternalModules,
) -> list[Definition]:
    out: list[Definition] = []

    def visit(node: ts.Node) -> None:
        ntype = node.type
        if ntype == "function_definition":
            _emit_def(out, source, node.child_by_field_name("name"), "f")
            _py_collect_type_params(out, source, node.child_by_field_name("type_parameters"))
            _py_collect_params(out, source, node.child_by_field_name("parameters"))
        elif ntype == "class_definition":
            _emit_def(out, source, node.child_by_field_name("name"), "t")
            _py_collect_type_params(out, source, node.child_by_field_name("type_parameters"))
        elif ntype == "type_alias_statement":
            left = node.child_by_field_name("left")
            if left is not None:
                for child in left.children:
                    if child.type == "identifier":
                        _emit_def(out, source, child, "t")
                    elif child.type == "generic_type":
                        for sub in child.children:
                            if sub.type == "identifier":
                                _emit_def(out, source, sub, "t")
                            elif sub.type == "type_parameter":
                                _py_collect_type_params(out, source, sub)
        elif ntype == "lambda":
            _py_collect_params(out, source, node.child_by_field_name("parameters"))
        elif ntype in ("assignment", "augmented_assignment"):
            _py_collect_pattern(out, source, node.child_by_field_name("left"))
        elif ntype == "named_expression":
            _emit_def(out, source, node.child_by_field_name("name"), "v")
        elif ntype in ("for_statement", "for_in_clause"):
            _py_collect_pattern(out, source, node.child_by_field_name("left"))
        elif ntype == "with_item":
            val = node.child_by_field_name("value")
            if val is not None and val.type == "as_pattern":
                _py_collect_pattern(out, source, val.child_by_field_name("alias"))
        elif ntype in ("except_clause", "except_group_clause"):
            for child in node.children:
                if child.type == "as_pattern":
                    _py_collect_pattern(out, source, child.child_by_field_name("alias"))
        elif ntype in ("global_statement", "nonlocal_statement"):
            for child in node.children:
                if child.type == "identifier":
                    _emit_def(out, source, child, "v")
        elif ntype == "case_clause":
            for child in node.children:
                if child.type == "case_pattern":
                    _py_collect_case_pattern(out, source, child)
        elif ntype == "import_statement":
            for child in node.children:
                if child.type == "aliased_import":
                    _emit_def(out, source, child.child_by_field_name("alias"), "v")
                elif child.type == "dotted_name":
                    parts = tuple(
                        _node_text(source, c) for c in child.children if c.type == "identifier"
                    )
                    if internal.is_internal_python_module(parts):
                        for ident in child.children:
                            if ident.type == "identifier":
                                _emit_def(out, source, ident, "v", strong=False)
        elif ntype == "import_from_statement":
            mod = node.child_by_field_name("module_name")
            is_internal = False
            if mod is not None:
                if mod.type == "relative_import":
                    is_internal = True
                elif mod.type == "dotted_name":
                    parts = tuple(
                        _node_text(source, c) for c in mod.children if c.type == "identifier"
                    )
                    is_internal = internal.is_internal_python_module(parts)
            for idx, child in enumerate(node.children):
                if node.field_name_for_child(idx) != "name":
                    continue
                if child.type == "aliased_import":
                    if is_internal:
                        orig = child.child_by_field_name("name")
                        if orig is not None:
                            idents = [c for c in orig.children if c.type == "identifier"]
                            if idents:
                                _emit_def(out, source, idents[-1], "v", strong=False)
                    _emit_def(out, source, child.child_by_field_name("alias"), "v")
                elif child.type == "dotted_name" and is_internal:
                    idents = [c for c in child.children if c.type == "identifier"]
                    if idents:
                        _emit_def(out, source, idents[-1], "v", strong=False)

    for node in _preorder(root):
        visit(node)
    return out


# --- JS / TS / TSX definition collection ---


def _jsts_collect_pattern(
    out: list[Definition],
    source: bytes,
    node: ts.Node | None,
    category: str = "v",
) -> None:
    if node is None:
        return
    ntype = node.type
    if ntype in ("identifier", "shorthand_property_identifier_pattern"):
        _emit_def(out, source, node, category)
        return
    if ntype in ("assignment_pattern", "object_assignment_pattern"):
        _jsts_collect_pattern(out, source, node.child_by_field_name("left"), category)
        return
    if ntype == "pair_pattern":
        # Only the bound target value is a local variable definition; the key may
        # belong to an external object shape.
        _jsts_collect_pattern(out, source, node.child_by_field_name("value"), "v")
        return
    if ntype in ("array_pattern", "object_pattern", "rest_pattern"):
        for child in node.children:
            if child.is_named:
                _jsts_collect_pattern(out, source, child, "v")


def _jsts_collect_formal_params(
    out: list[Definition],
    source: bytes,
    params_node: ts.Node | None,
) -> None:
    if params_node is None:
        return
    for child in params_node.children:
        if not child.is_named:
            continue
        if child.type in ("required_parameter", "optional_parameter"):
            _jsts_collect_pattern(out, source, child.child_by_field_name("pattern"), "v")
        else:
            _jsts_collect_pattern(out, source, child, "v")


def _is_module_exports(source: bytes, member: ts.Node) -> bool:
    """Return True for the CommonJS ``module.exports`` target itself."""
    obj = member.child_by_field_name("object")
    prop = member.child_by_field_name("property")
    return (
        obj is not None
        and prop is not None
        and _node_text(source, obj) == "module"
        and _node_text(source, prop) == "exports"
    )


def _collect_js_ts_defs(
    source: bytes,
    root: ts.Node,
    internal: InternalModules,
) -> list[Definition]:
    out: list[Definition] = []

    def visit(node: ts.Node) -> None:
        ntype = node.type
        if ntype in (
            "function_declaration",
            "generator_function_declaration",
            "function_expression",
            "generator_function",
            "function_signature",
        ):
            _emit_def(out, source, node.child_by_field_name("name"), "f")
        elif ntype in ("method_definition", "method_signature", "abstract_method_signature"):
            name_node = node.child_by_field_name("name")
            if name_node is not None and name_node.type in (
                "property_identifier",
                "private_property_identifier",
            ):
                _emit_def(out, source, name_node, "f")
        elif ntype == "formal_parameters":
            # Every parameter list, including type-only ones (function types,
            # call/construct signatures, constructor types, overloads).
            _jsts_collect_formal_params(out, source, node)
        elif ntype == "tuple_type":
            for member in node.children:
                if member.type in ("required_parameter", "optional_parameter"):
                    _jsts_collect_pattern(out, source, member.child_by_field_name("name"), "v")
        elif ntype == "index_signature":
            _emit_def(out, source, node.child_by_field_name("name"), "v")
        elif ntype == "arrow_function":
            param = node.child_by_field_name("parameter")
            if param is not None:
                _jsts_collect_pattern(out, source, param, "v")
        elif ntype in (
            "class_declaration",
            "abstract_class_declaration",
            "class",
            "interface_declaration",
            "type_alias_declaration",
            "enum_declaration",
            "type_parameter",
            "mapped_type_clause",
        ):
            _emit_def(out, source, node.child_by_field_name("name"), "t")
        elif ntype == "infer_type":
            for child in node.children:
                if child.type == "type_identifier":
                    _emit_def(out, source, child, "t")
        elif ntype == "variable_declarator":
            name_node = node.child_by_field_name("name")
            val_node = node.child_by_field_name("value")
            cat = (
                "f"
                if name_node is not None
                and name_node.type == "identifier"
                and val_node is not None
                and val_node.type in ("arrow_function", "function_expression", "generator_function")
                else "v"
            )
            _jsts_collect_pattern(out, source, name_node, cat)
        elif ntype in ("field_definition", "public_field_definition", "property_signature"):
            # JS field_definition names its target "property"; TS uses "name".
            name_node = node.child_by_field_name("name") or node.child_by_field_name("property")
            if name_node is not None and name_node.type in (
                "property_identifier",
                "private_property_identifier",
            ):
                _emit_def(out, source, name_node, "v")
        elif ntype == "pair":
            key_node = node.child_by_field_name("key")
            if key_node is not None and key_node.type == "property_identifier":
                _emit_def(out, source, key_node, "v")
        elif ntype == "shorthand_property_identifier":
            _emit_def(out, source, node, "v")
        elif ntype == "enum_body":
            for child in node.children:
                if child.type == "property_identifier":
                    _emit_def(out, source, child, "v")
                elif child.type == "enum_assignment":
                    _emit_def(out, source, child.child_by_field_name("name"), "v")
        elif ntype in ("assignment_expression", "augmented_assignment_expression"):
            left = node.child_by_field_name("left")
            if left is not None and left.type == "identifier":
                _emit_def(out, source, left, "v")
            elif (
                left is not None
                and left.type == "member_expression"
                and not _is_module_exports(source, left)
            ):
                _emit_def(out, source, left.child_by_field_name("property"), "v")
        elif ntype == "for_in_statement":
            _jsts_collect_pattern(out, source, node.child_by_field_name("left"), "v")
        elif ntype == "catch_clause":
            _jsts_collect_pattern(out, source, node.child_by_field_name("parameter"), "v")
        elif ntype == "labeled_statement":
            _emit_def(out, source, node.child_by_field_name("label"), "v")
        elif ntype in ("internal_module", "module"):
            name_node = node.child_by_field_name("name")
            if name_node is not None and name_node.type == "identifier":
                _emit_def(out, source, name_node, "v")
        elif ntype == "import_statement":
            _collect_js_ts_import(out, source, node, internal)
        elif ntype == "export_statement":
            _collect_js_ts_export(out, source, node, internal)
        elif ntype == "import_alias":
            for child in node.children:
                if child.type == "identifier":
                    _emit_def(out, source, child, "v")
                    break

    for node in _preorder(root):
        visit(node)
    return out


def _collect_js_ts_import(
    out: list[Definition],
    source: bytes,
    node: ts.Node,
    internal: InternalModules,
) -> None:
    req_clause = next((c for c in node.children if c.type == "import_require_clause"), None)
    if req_clause is not None:
        ident = next((c for c in req_clause.children if c.type == "identifier"), None)
        spec_node = req_clause.child_by_field_name("source")
        spec = _unquote_js_ts_string(source, spec_node) if spec_node is not None else None
        if spec is not None and internal.is_internal_js_ts_specifier(spec):
            _emit_def(out, source, ident, "v", strong=False)
        return

    src_node = node.child_by_field_name("source")
    spec = _unquote_js_ts_string(source, src_node) if src_node is not None else None
    is_internal = spec is not None and internal.is_internal_js_ts_specifier(spec)

    clause = next((c for c in node.children if c.type == "import_clause"), None)
    if clause is None:
        return
    for child in clause.children:
        if child.type == "identifier" and is_internal:
            _emit_def(out, source, child, "v", strong=False)
        elif child.type == "namespace_import":
            ident = next((c for c in child.children if c.type == "identifier"), None)
            _emit_def(out, source, ident, "v")
        elif child.type == "named_imports":
            for item in child.children:
                if item.type != "import_specifier":
                    continue
                alias = item.child_by_field_name("alias")
                name = item.child_by_field_name("name")
                if is_internal:
                    _emit_def(out, source, name, "v", strong=False)
                if alias is not None:
                    _emit_def(out, source, alias, "v")


def _collect_js_ts_export(
    out: list[Definition],
    source: bytes,
    node: ts.Node,
    internal: InternalModules,
) -> None:
    src_node = node.child_by_field_name("source")
    if src_node is not None:
        spec = _unquote_js_ts_string(source, src_node)
        is_internal = spec is not None and internal.is_internal_js_ts_specifier(spec)
    else:
        is_internal = True

    for child in node.children:
        if child.type == "namespace_export":
            ident = next((c for c in child.children if c.type == "identifier"), None)
            _emit_def(out, source, ident, "v")
        elif child.type == "export_clause":
            for item in child.children:
                if item.type != "export_specifier":
                    continue
                alias = item.child_by_field_name("alias")
                name = item.child_by_field_name("name")
                if is_internal:
                    _emit_def(out, source, name, "v", strong=False)
                if alias is not None:
                    _emit_def(out, source, alias, "v")


# --- Rust definition collection ---


def _rust_collect_pattern(
    out: list[Definition],
    source: bytes,
    node: ts.Node | None,
    *,
    in_match: bool = False,
) -> None:
    if node is None:
        return
    ntype = node.type
    if ntype == "identifier":
        text = _node_text(source, node)
        # In match arms, bare PascalCase identifiers are usually unit enum variants
        # or constants; project variants/consts are already collected at definition.
        if in_match and text[:1].isupper():
            return
        _emit_def(out, source, node, "v")
        return
    if ntype == "shorthand_field_identifier":
        _emit_def(out, source, node, "v")
        return
    if ntype == "tuple_struct_pattern":
        type_node = node.child_by_field_name("type")
        for child in node.children:
            if child.is_named and child != type_node:
                _rust_collect_pattern(out, source, child, in_match=in_match)
        return
    if ntype == "struct_pattern":
        for child in node.children:
            if child.type == "field_pattern":
                pat = child.child_by_field_name("pattern")
                if pat is not None:
                    _rust_collect_pattern(out, source, pat, in_match=in_match)
                else:
                    name_node = child.child_by_field_name("name")
                    if name_node is not None and name_node.type == "shorthand_field_identifier":
                        _emit_def(out, source, name_node, "v")
        return
    if ntype == "captured_pattern":
        for child in node.children:
            if child.type == "identifier":
                _emit_def(out, source, child, "v")
            elif child.is_named:
                _rust_collect_pattern(out, source, child, in_match=in_match)
        return
    if ntype in (
        "mut_pattern",
        "ref_pattern",
        "reference_pattern",
        "tuple_pattern",
        "slice_pattern",
        "or_pattern",
        "match_pattern",
    ):
        for child in node.children:
            if child.is_named:
                _rust_collect_pattern(out, source, child, in_match=in_match)


def _rust_use_root(source: bytes, node: ts.Node) -> str | None:
    cur = node
    while True:
        if cur.type in ("scoped_identifier", "scoped_use_list", "use_as_clause", "use_wildcard"):
            nxt = cur.child_by_field_name("path")
            if nxt is None:
                return None
            cur = nxt
            continue
        if cur.type in ("identifier", "crate", "self", "super"):
            return _node_text(source, cur)
        return None


def _rust_collect_use_tree(
    out: list[Definition],
    source: bytes,
    node: ts.Node,
    is_internal: bool,
) -> None:
    ntype = node.type
    if ntype == "use_as_clause":
        alias = node.child_by_field_name("alias")
        if is_internal:
            path = node.child_by_field_name("path")
            if path is not None:
                _rust_collect_use_tree(out, source, path, is_internal=True)
        _emit_def(out, source, alias, "v")
        return
    if ntype == "scoped_use_list":
        use_list = node.child_by_field_name("list")
        if use_list is not None:
            for child in use_list.children:
                if child.is_named:
                    _rust_collect_use_tree(out, source, child, is_internal)
        return
    if ntype == "use_list":
        for child in node.children:
            if child.is_named:
                _rust_collect_use_tree(out, source, child, is_internal)
        return
    if ntype == "scoped_identifier" and is_internal:
        _emit_def(out, source, node.child_by_field_name("name"), "v", strong=False)
        return
    if ntype == "identifier" and is_internal:
        _emit_def(out, source, node, "v", strong=False)


def _collect_rust_defs(
    source: bytes,
    root: ts.Node,
    internal: InternalModules,
) -> list[Definition]:
    out: list[Definition] = []

    def visit(node: ts.Node) -> None:
        ntype = node.type
        if ntype in ("function_item", "function_signature_item", "macro_definition"):
            _emit_def(out, source, node.child_by_field_name("name"), "f")
        elif ntype in (
            "struct_item",
            "enum_item",
            "union_item",
            "trait_item",
            "type_item",
            "associated_type",
            "type_parameter",
        ):
            _emit_def(out, source, node.child_by_field_name("name"), "t")
        elif ntype in (
            "mod_item",
            "const_item",
            "static_item",
            "const_parameter",
            "enum_variant",
            "field_declaration",
        ):
            _emit_def(out, source, node.child_by_field_name("name"), "v")
        elif ntype == "parameter":
            _rust_collect_pattern(out, source, node.child_by_field_name("pattern"))
        elif ntype == "closure_parameters":
            for child in node.children:
                if child.is_named and child.type != "parameter":
                    _rust_collect_pattern(out, source, child)
        elif ntype in ("let_declaration", "let_condition"):
            _rust_collect_pattern(out, source, node.child_by_field_name("pattern"))
        elif ntype == "for_expression":
            _rust_collect_pattern(out, source, node.child_by_field_name("pattern"))
            for child in node.children:
                if child.type == "label":
                    ident = next((c for c in child.children if c.type == "identifier"), None)
                    _emit_def(out, source, ident, "v")
        elif ntype in ("loop_expression", "while_expression", "block"):
            for child in node.children:
                if child.type == "label":
                    ident = next((c for c in child.children if c.type == "identifier"), None)
                    _emit_def(out, source, ident, "v")
        elif ntype == "lifetime_parameter":
            lifetime = next((c for c in node.children if c.type == "lifetime"), None)
            if lifetime is not None:
                ident = next((c for c in lifetime.children if c.type == "identifier"), None)
                _emit_def(out, source, ident, "v")
        elif ntype == "match_arm":
            _rust_collect_pattern(out, source, node.child_by_field_name("pattern"), in_match=True)
        elif ntype == "token_binding_pattern":
            meta = next((c for c in node.children if c.type == "metavariable"), None)
            _emit_def(out, source, meta, "v")
        elif ntype == "use_declaration":
            arg = node.child_by_field_name("argument")
            if arg is not None:
                root_ident = _rust_use_root(source, arg)
                is_internal = root_ident is not None and internal.is_internal_rust_root(root_ident)
                _rust_collect_use_tree(out, source, arg, is_internal)

    for node in _preorder(root):
        visit(node)
    return out


# ---------------------------------------------------------------------------
# AST rewriting (garbling)
# ---------------------------------------------------------------------------


def _newline_pad(raw_slice: bytes, *, line_continuation: bool = False) -> bytes:
    """Return newline padding matching the line break count of ``raw_slice``."""
    count = raw_slice.count(b"\n")
    if count == 0:
        return b""
    unit = b"\\\n" if line_continuation else b"\n"
    return unit * count


def _garble_js_ts_internal_specifier(
    specifier: str,
    symbols: SymbolTable,
) -> str:
    """Garble a relative/internal JS/TS import specifier with path tokens or ``sN``."""
    if specifier in (".", ".."):
        return specifier
    prefix = ""
    rest = specifier
    for candidate_prefix in ("@/", "~/", "/", "#"):
        if rest.startswith(candidate_prefix):
            prefix = candidate_prefix
            rest = rest[len(candidate_prefix):]
            break

    parts = rest.split("/")
    garbled_parts: list[str] = []
    for idx, part in enumerate(parts):
        if part in ("", ".", ".."):
            garbled_parts.append(part)
            continue
        is_last = idx == len(parts) - 1
        ext = ""
        stem = part
        if is_last:
            suffix = PurePosixPath(part).suffix
            if suffix.lower() in JS_TS_SPECIFIER_EXTENSIONS:
                ext = suffix
                stem = part[: -len(suffix)]
        if stem in CONVENTIONAL_STEMS:
            garbled_parts.append(f"{stem}{ext}")
            continue
        mapped = symbols.identifiers.get(stem)
        if mapped is None:
            # Unknown segment (e.g. non-code asset like ./styles.css): hide with sN.
            return symbols.assign_string(specifier)
        garbled_parts.append(f"{mapped}{ext}")

    return prefix + "/".join(garbled_parts)


JSX_DATA_PREFIX = "data-"
JSX_TAG_TYPES: frozenset[str] = frozenset({
    "jsx_opening_element",
    "jsx_closing_element",
    "jsx_self_closing_element",
})
# ``(object, method)`` call shapes whose first string argument names a module.
JS_TS_MODULE_CALLS: frozenset[tuple[str, str]] = frozenset({("jest", "mock"), ("vi", "mock")})

Step = ts.Node | Callable[[], None]


def _is_intrinsic_jsx_tag_name(node: ts.Node, raw_text: str) -> bool:
    """Return True for a lowercase, non-member JSX tag name such as ``<form>``."""
    parent = node.parent
    return (
        parent is not None
        and parent.type in JSX_TAG_TYPES
        and raw_text[:1].islower()
        and parent.child_by_field_name("name") == node
    )


def _is_jsx_data_attribute(node: ts.Node, raw_text: str) -> bool:
    """Return True for a ``data-*`` JSX attribute name that carries a custom suffix."""
    parent = node.parent
    return (
        len(raw_text) > len(JSX_DATA_PREFIX)
        and raw_text[: len(JSX_DATA_PREFIX)].lower() == JSX_DATA_PREFIX
        and parent is not None
        and parent.type == "jsx_attribute"
    )


def _is_js_ts_module_specifier(node: ts.Node, source: bytes) -> bool:
    """Return True if a JS/TS ``string`` node is a module import/export/require target."""
    parent = node.parent
    if parent is None:
        return False
    if parent.type in ("import_statement", "export_statement", "import_require_clause"):
        return parent.child_by_field_name("source") == node
    if parent.type == "arguments" and parent.parent is not None:
        call = parent.parent
        if call.type == "call_expression":
            func = call.child_by_field_name("function")
            if func is not None and _is_module_call(func, source):
                first_arg = next((c for c in parent.children if c.is_named), None)
                return first_arg == node
    return False


def _is_module_call(func: ts.Node, source: bytes) -> bool:
    """Return True for ``import(...)``, ``require(...)``, ``jest.mock(...)``, ``vi.mock(...)``."""
    if func.type == "import":
        return True
    if func.type == "identifier":
        return _node_text(source, func) == "require"
    if func.type == "member_expression":
        obj = func.child_by_field_name("object")
        prop = func.child_by_field_name("property")
        if obj is None or prop is None or obj.type != "identifier":
            return False
        return (_node_text(source, obj), _node_text(source, prop)) in JS_TS_MODULE_CALLS
    return False


def _replace_comment(source: bytes, node: ts.Node) -> bytes:
    raw = source[node.start_byte:node.end_byte]
    newlines = raw.count(b"\n")
    if newlines > 0:
        return b"\n" * newlines
    # Single-line block comments (/* ... */) may sit between adjacent tokens; keep one space.
    if raw.startswith(b"/*"):
        return b" "
    return b""


def _garble_source(
    parsed: ParsedFile,
    symbols: SymbolTable,
    internal: InternalModules,
) -> tuple[bytes, bool]:
    """Garble one parsed file and validate its syntax and line count.

    Returns:
        A ``(garbled_bytes, has_garble_error)`` tuple.
    """
    source = parsed.source.content
    language = parsed.source.language
    edits: list[tuple[int, int, bytes]] = []

    def replace_literal_span(
        start: int,
        end: int,
        *,
        line_continuation: bool = False,
    ) -> None:
        if end <= start:
            return
        raw = source[start:end]
        content = raw.decode("utf-8", errors="replace")
        token = symbols.assign_string(content)
        pad = _newline_pad(raw, line_continuation=line_continuation)
        edits.append((start, end, token.encode("utf-8") + pad))

    def literal_step(start: int, end: int, *, line_continuation: bool = False) -> Step:
        """Defer a literal replacement so it runs in order relative to child nodes."""
        return partial(replace_literal_span, start, end, line_continuation=line_continuation)

    def python_string_steps(node: ts.Node) -> list[Step]:
        start_node = next((c for c in node.children if c.type == "string_start"), None)
        end_node = next((c for c in node.children if c.type == "string_end"), None)
        if start_node is None or end_node is None:
            return []
        start_text = _node_text(source, start_node)
        continuation = not start_text.endswith(('"""', "'''"))
        steps: list[Step] = []
        cursor = start_node.end_byte
        for child in node.children:
            if child.type == "interpolation":
                steps.append(literal_step(cursor, child.start_byte, line_continuation=continuation))
                steps.append(child)
                cursor = child.end_byte
        steps.append(literal_step(cursor, end_node.start_byte, line_continuation=continuation))
        return steps

    def python_format_specifier_steps(node: ts.Node) -> list[Step]:
        """Replace the literal text of an f-string format spec; keep nested ``{expr}``."""
        steps: list[Step] = []
        cursor = node.start_byte
        for child in node.children:
            if child.type == ":" and child.start_byte == cursor:
                cursor = child.end_byte
            elif child.type == "format_expression":
                steps.append(literal_step(cursor, child.start_byte))
                steps.append(child)
                cursor = child.end_byte
        steps.append(literal_step(cursor, node.end_byte))
        return steps

    def js_ts_string_steps(node: ts.Node) -> list[Step]:
        if len(node.children) < 2:
            return []
        start = node.children[0].end_byte
        end = node.children[-1].start_byte
        if end <= start:
            return []
        if _is_js_ts_module_specifier(node, source):
            spec = source[start:end].decode("utf-8", errors="replace")
            if not internal.is_internal_js_ts_specifier(spec):
                return []
            replaced = _garble_js_ts_internal_specifier(spec, symbols)
            pad = _newline_pad(source[start:end], line_continuation=True)
            edits.append((start, end, replaced.encode("utf-8") + pad))
            return []
        return [literal_step(start, end, line_continuation=True)]

    def js_ts_template_steps(node: ts.Node) -> list[Step]:
        if len(node.children) < 2:
            return []
        steps: list[Step] = []
        cursor = node.children[0].end_byte
        end_byte = node.children[-1].start_byte
        for child in node.children[1:-1]:
            if child.type in ("template_substitution", "template_type"):
                steps.append(literal_step(cursor, child.start_byte))
                steps.append(child)
                cursor = child.end_byte
        steps.append(literal_step(cursor, end_byte))
        return steps

    def rust_string_steps(node: ts.Node) -> list[Step]:
        if node.type == "raw_string_literal":
            content_node = next((c for c in node.children if c.type == "string_content"), None)
            if content_node is None:
                return []
            return [literal_step(content_node.start_byte, content_node.end_byte)]
        if len(node.children) < 2:
            return []
        return [literal_step(node.children[0].end_byte, node.children[-1].start_byte)]

    def handle_jsx_text_span(start: int, end: int) -> None:
        raw = source[start:end]
        text = raw.decode("utf-8", errors="replace")
        stripped = text.strip()
        if not stripped:
            return
        # Keep surrounding whitespace/newlines intact so indentation and line numbers hold.
        lead = len(text) - len(text.lstrip())
        trail = len(text) - len(text.rstrip())
        inner_start = start + len(text[:lead].encode("utf-8"))
        inner_end = end - len(text[len(text) - trail :].encode("utf-8")) if trail else end
        if inner_end <= inner_start:
            return
        inner_raw = source[inner_start:inner_end]
        token = symbols.assign_string(stripped)
        pad = _newline_pad(inner_raw, line_continuation=False)
        edits.append((inner_start, inner_end, token.encode("utf-8") + pad))

    def jsx_element_steps(node: ts.Node) -> list[Step]:
        steps: list[Step] = []
        children = node.children
        idx = 0
        while idx < len(children):
            child = children[idx]
            if child.type in ("jsx_text", "html_character_reference"):
                span_start = child.start_byte
                span_end = child.end_byte
                while idx + 1 < len(children) and children[idx + 1].type in (
                    "jsx_text",
                    "html_character_reference",
                ):
                    idx += 1
                    span_end = children[idx].end_byte
                steps.append(partial(handle_jsx_text_span, span_start, span_end))
            else:
                steps.append(child)
            idx += 1
        return steps

    def visit(node: ts.Node) -> Sequence[Step]:
        """Garble one node; return the steps (child nodes or deferred edits) to run next."""
        ntype = node.type

        if ntype in ("shebang", "hash_bang_line", "char_literal", "meta_property"):
            return ()

        if ntype in COMMENT_NODE_TYPES:
            if (
                language == "python"
                and node.start_byte == 0
                and source[node.start_byte:node.end_byte].startswith(b"#!")
            ):
                return ()
            edits.append((node.start_byte, node.end_byte, _replace_comment(source, node)))
            return ()

        if language == "python":
            if ntype == "string":
                return python_string_steps(node)
            if ntype == "format_specifier":
                return python_format_specifier_steps(node)

        if language in JS_TS_LANGUAGES:
            if ntype == "string":
                return js_ts_string_steps(node)
            if ntype in ("template_string", "template_literal_type"):
                return js_ts_template_steps(node)
            if ntype == "regex":
                pattern_node = node.child_by_field_name("pattern")
                if pattern_node is None:
                    return ()
                return [literal_step(pattern_node.start_byte, pattern_node.end_byte)]
            if ntype == "jsx_element":
                return jsx_element_steps(node)
            if ntype == "jsx_text":
                handle_jsx_text_span(node.start_byte, node.end_byte)
                return ()

        if language == "rust" and ntype in ("string_literal", "raw_string_literal"):
            return rust_string_steps(node)

        if ntype in IDENT_NODE_TYPES:
            raw = _node_text(source, node)
            if language in JS_TS_LANGUAGES:
                if ntype == "identifier" and _is_intrinsic_jsx_tag_name(node, raw):
                    return ()
                if (
                    ntype == "property_identifier"
                    and node.parent is not None
                    and node.parent.type == "member_expression"
                    and _is_module_exports(source, node.parent)
                ):
                    return ()
                if ntype == "property_identifier" and _is_jsx_data_attribute(node, raw):
                    rest = raw[len(JSX_DATA_PREFIX):]
                    token = symbols.assign_string(rest)
                    start = node.start_byte + len(JSX_DATA_PREFIX)
                    edits.append((start, node.end_byte, token.encode("utf-8")))
                    return ()
            logical = _logical_ident_name(node, raw)
            token = symbols.identifiers.get(logical)
            if token is not None:
                replacement = _format_ident_replacement(node, raw, token).encode("utf-8")
                edits.append((node.start_byte, node.end_byte, replacement))
            return ()

        return node.children

    # Explicit stack: steps run in source order, so token allocation (and therefore
    # output) matches a recursive walk without the recursion-depth limit.
    stack: list[Step] = [parsed.tree.root_node]
    while stack:
        step = stack.pop()
        if isinstance(step, ts.Node):
            stack.extend(reversed(visit(step)))
        else:
            step()

    # Apply edits left-to-right.
    edits.sort(key=lambda item: (item[0], item[1]))
    chunks: list[bytes] = []
    pos = 0
    for start, end, replacement in edits:
        if start < pos:
            continue
        chunks.append(source[pos:start])
        chunks.append(replacement)
        pos = end
    chunks.append(source[pos:])
    garbled = b"".join(chunks)

    # Preserve splitlines() count if a file lacked a trailing newline and ended in a comment.
    orig_lines = len(source.splitlines())
    if (
        orig_lines != len(garbled.splitlines())
        and not source.endswith(b"\n")
        and (garbled == b"" or garbled.endswith(b"\n"))
    ):
        garbled += b" "

    line_ok = (
        source.count(b"\n") == garbled.count(b"\n")
        and orig_lines == len(garbled.splitlines())
    )
    reparse = get_parser(language).parse(garbled)
    syntax_bug = (not parsed.had_parse_error) and reparse.root_node.has_error
    return garbled, (syntax_bug or not line_ok)


# ---------------------------------------------------------------------------
# Path garbling & unified diff generation
# ---------------------------------------------------------------------------


def garble_rel_path(rel_path: str, ident_map: dict[str, str]) -> str:
    """Replace each path component of ``rel_path`` with its identifier token."""
    posix = PurePosixPath(rel_path)
    dir_parts = [ident_map.get(part, part) for part in posix.parent.parts]
    stem = ident_map.get(posix.stem, posix.stem)
    filename = f"{stem}{posix.suffix}"
    return PurePosixPath(*dir_parts, filename).as_posix()


def _path_components(rel_path: str) -> list[str]:
    """Return directory components followed by file stem in top-down order."""
    posix = PurePosixPath(rel_path)
    return [*posix.parent.parts, posix.stem]


def _format_diff_lines(lines: Iterable[str]) -> str:
    out: list[str] = []
    for line in lines:
        if line.endswith("\n"):
            out.append(line)
        else:
            out.append(f"{line}\n\\ No newline at end of file\n")
    return "".join(out)


def build_change_diff(
    base_garbled: dict[str, bytes],
    candidate_garbled: dict[str, bytes],
) -> str:
    """Build a unified diff (3 context lines) sorted by garbled relative path."""
    sections: list[str] = []
    all_paths = sorted(set(base_garbled) | set(candidate_garbled))
    for gpath in all_paths:
        base_bytes = base_garbled.get(gpath)
        cand_bytes = candidate_garbled.get(gpath)
        if base_bytes == cand_bytes:
            continue
        base_lines = (
            []
            if base_bytes is None
            else base_bytes.decode("utf-8", errors="replace").splitlines(keepends=True)
        )
        cand_lines = (
            []
            if cand_bytes is None
            else cand_bytes.decode("utf-8", errors="replace").splitlines(keepends=True)
        )
        fromfile = "/dev/null" if base_bytes is None else f"a/{gpath}"
        tofile = "/dev/null" if cand_bytes is None else f"b/{gpath}"
        diff_iter = difflib.unified_diff(
            base_lines,
            cand_lines,
            fromfile=fromfile,
            tofile=tofile,
            n=3,
        )
        section = _format_diff_lines(diff_iter)
        if section:
            sections.append(section)
    return "".join(sections)


# ---------------------------------------------------------------------------
# GitHub issue filing for unsupported languages
# ---------------------------------------------------------------------------


GITHUB_HOST = "github.com"
_GITHUB_NAME_RE = re.compile(r"[A-Za-z0-9_.-]+")
_SCP_REMOTE_RE = re.compile(r"^(?:[^@/:]+@)?(?P<host>[^/:]+):(?P<path>[^:]*)$")


def parse_github_repo(remote_url: str) -> str:
    """Extract ``owner/name`` from a GitHub remote URL, rejecting every other host.

    Accepts ``https://github.com/owner/name(.git)``, ``git@github.com:owner/name(.git)``
    and ``ssh://git@github.com/owner/name(.git)``, with exactly an owner and a name.

    Raises:
        ValueError: The host is not github.com or the path is not ``owner/name``.
    """
    url = remote_url.strip()
    if not url:
        raise ValueError("empty remote URL")
    if "://" in url:
        parsed = urlparse(url)
        if parsed.scheme not in ("https", "ssh"):
            raise ValueError(f"unsupported git remote scheme {parsed.scheme!r}; only github.com is supported")
        host, path_str = parsed.hostname or "", parsed.path
    else:
        match = _SCP_REMOTE_RE.match(url)
        if match is None:
            raise ValueError("unrecognized git remote URL; expected a github.com remote")
        host, path_str = match["host"], match["path"]
    if host.lower() != GITHUB_HOST:
        raise ValueError(f"git remote host {host!r} is not {GITHUB_HOST}; refusing to guess a repo")

    parts = path_str.strip("/").removesuffix(".git").split("/")
    if len(parts) != 2 or not all(_GITHUB_NAME_RE.fullmatch(part) for part in parts):
        raise ValueError("git remote path is not exactly owner/name")
    if any(part in (".", "..") for part in parts):
        raise ValueError("git remote path is not exactly owner/name")
    return "/".join(parts)


def resolve_script_repo() -> str:
    """Derive ``owner/name`` from the git repo hosting this script's real path."""
    script_dir = Path(__file__).resolve().parent
    proc = subprocess.run(
        ["git", "-C", str(script_dir), "remote", "get-url", "origin"],
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        stderr = proc.stderr.strip() or "git remote get-url origin failed"
        raise RuntimeError(stderr)
    return parse_github_repo(proc.stdout)


def _issue_body(language: str, extensions: list[str], file_count: int) -> str:
    ext_list = ", ".join(extensions)
    return (
        f"The council garbler encountered an unsupported language: {language}.\n\n"
        f"- Observed extensions: {ext_list}\n"
        f"- File count: {file_count}\n\n"
        f"How to add support for {language}:\n"
        "1. Add the official tree-sitter grammar package (pinned to an ABI-compatible "
        "version) to the PEP 723 dependencies in `scripts/garble.py` and `tests/test_garble.py`.\n"
        "2. Move these extensions from `UNSUPPORTED_EXTENSIONS` to `SUPPORTED_EXTENSIONS` "
        "and register the grammar parser.\n"
        "3. Implement definition collection, protected built-ins/keywords, and string/comment "
        "garbling rules for the grammar.\n"
        "4. Add unit test coverage in `tests/test_garble.py`.\n"
    )


def file_unsupported_issues(
    unsupported: dict[str, dict[str, object]],
) -> tuple[list[dict[str, str]], str | None]:
    """Ensure a GitHub issue exists for each unsupported language."""
    if not unsupported:
        return [], None
    try:
        repo = resolve_script_repo()
    except (OSError, RuntimeError, ValueError) as exc:
        return [], str(exc)

    results: list[dict[str, str]] = []
    for language in sorted(unsupported):
        info = unsupported[language]
        extensions = list(info["extensions"])  # type: ignore[arg-type]
        file_count = int(info["files"])  # type: ignore[arg-type]
        title = f"council garbler: support {language}"

        try:
            list_proc = subprocess.run(
                [
                    "gh",
                    "issue",
                    "list",
                    "--repo",
                    repo,
                    "--state",
                    "all",
                    "--search",
                    f'"{title}" in:title',
                    "--json",
                    "number,title,url,state",
                    "--limit",
                    "20",
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            if list_proc.returncode != 0:
                err = list_proc.stderr.strip() or f"gh issue list exited {list_proc.returncode}"
                return results, err
            items = json.loads(list_proc.stdout or "[]")
            exact = next(
                (item for item in items if isinstance(item, dict) and item.get("title") == title),
                None,
            )
            if exact is not None:
                results.append({
                    "language": language,
                    "action": "exists",
                    "url": str(exact.get("url", "")),
                })
                continue

            body = _issue_body(language, extensions, file_count)
            create_proc = subprocess.run(
                [
                    "gh",
                    "issue",
                    "create",
                    "--repo",
                    repo,
                    "--title",
                    title,
                    "--body",
                    body,
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            if create_proc.returncode != 0:
                err = create_proc.stderr.strip() or f"gh issue create exited {create_proc.returncode}"
                return results, err
            lines = [line.strip() for line in create_proc.stdout.splitlines() if line.strip()]
            url = lines[-1] if lines else ""
            results.append({
                "language": language,
                "action": "created",
                "url": url,
            })
        except (OSError, json.JSONDecodeError) as exc:
            return results, str(exc)

    return results, None


# ---------------------------------------------------------------------------
# Reveal subcommand
# ---------------------------------------------------------------------------


LINE_BREAK_RE = re.compile("\r\n|[\n\r\v\f\x1c\x1d\x1e\x85\u2028\u2029]")


def reveal_text(text: str, map_data: dict[str, object]) -> str:
    """Map garbled identifier, path, and string tokens in ``text`` back to originals.

    String content is inserted as written in the source, except that every line
    break ``str.splitlines`` recognizes (``\\r\\n`` included) becomes the two
    characters ``\\n``, so a revealed line stays one line.
    """
    ident_map = map_data.get("identifiers", {})
    string_map = map_data.get("strings", {})
    if not isinstance(ident_map, dict) or not isinstance(string_map, dict):
        raise InputError("map file is missing valid 'identifiers' or 'strings' objects")

    rev_idents: dict[str, str] = {str(tok): str(orig) for orig, tok in ident_map.items()}
    rev_strings: dict[str, str] = {str(tok): str(orig) for orig, tok in string_map.items()}

    def replace(match: re.Match[str]) -> str:
        tok = match.group(1)
        if tok[0] in ("f", "t", "v"):
            return rev_idents.get(tok, tok)
        orig_str = rev_strings.get(tok)
        if orig_str is None:
            return tok
        return LINE_BREAK_RE.sub(lambda _match: "\\n", orig_str)

    return TOKEN_RE.sub(replace, text)


# ---------------------------------------------------------------------------
# Garble orchestration
# ---------------------------------------------------------------------------


def _validate_output_paths(out_dir: Path, map_file: Path) -> None:
    if out_dir.exists():
        if not out_dir.is_dir():
            raise InputError(f"--out exists and is not a directory: {out_dir}")
        if any(out_dir.iterdir()):
            raise InputError(f"--out directory must be absent or empty: {out_dir}")

    out_resolved = out_dir.resolve()
    map_resolved = map_file.resolve()
    if map_resolved == out_resolved or map_resolved.is_relative_to(out_resolved):
        raise InputError(f"--map must not resolve inside --out: {map_file}")
    if map_file.exists() and map_file.is_dir():
        raise InputError(f"--map exists and is a directory: {map_file}")


def _parse_files(files: dict[str, SourceFile]) -> dict[str, ParsedFile]:
    parsed: dict[str, ParsedFile] = {}
    for rel_path in sorted(files):
        src = files[rel_path]
        tree = get_parser(src.language).parse(src.content)
        parsed[rel_path] = ParsedFile(
            source=src,
            tree=tree,
            had_parse_error=tree.root_node.has_error,
        )
    return parsed


def run_garble(
    candidate_dir: Path,
    base_dir: Path | None,
    out_dir: Path,
    map_file: Path,
    *,
    file_issues: bool = False,
) -> tuple[dict[str, object], int]:
    """Execute the full garble pipeline and return ``(summary_dict, exit_code)``."""
    ensure_git_checkout(candidate_dir, "--candidate")
    if base_dir is not None:
        ensure_git_checkout(base_dir, "--base")
    _validate_output_paths(out_dir, map_file)

    cand_scan = scan_tree(candidate_dir)
    base_scan = scan_tree(base_dir) if base_dir is not None else None

    cand_parsed = _parse_files(cand_scan.supported_files)
    base_parsed = _parse_files(base_scan.supported_files) if base_scan is not None else {}

    all_supported_paths = sorted(set(cand_parsed) | set(base_parsed))
    internal = InternalModules(all_supported_paths)

    # Scan order: candidate sorted by rel_path, then base-only sorted by rel_path,
    # then base versions of modified files (so deleted symbols/strings also garble).
    ordered_entries: list[tuple[str, str, ParsedFile]] = []
    for rel_path in sorted(cand_parsed):
        ordered_entries.append(("candidate", rel_path, cand_parsed[rel_path]))
    for rel_path in sorted(base_parsed):
        if rel_path not in cand_parsed:
            ordered_entries.append(("base", rel_path, base_parsed[rel_path]))
    for rel_path in sorted(base_parsed):
        if rel_path in cand_parsed and (
            base_parsed[rel_path].source.content != cand_parsed[rel_path].source.content
        ):
            ordered_entries.append(("base", rel_path, base_parsed[rel_path]))

    parse_errors_set: set[str] = set()
    garble_errors_set: set[str] = set()
    failed_entries: set[tuple[str, str]] = set()
    file_defs: list[tuple[str, list[Definition]]] = []
    all_seen_idents: set[str] = set()
    strong_categories: dict[str, str] = {}
    project_defined: set[str] = set()
    kept_path_components: set[str] = set()

    for role, rel_path, parsed in ordered_entries:
        if parsed.had_parse_error:
            parse_errors_set.add(rel_path)
        try:
            idents = collect_all_identifiers(parsed)
            defs = collect_definitions(parsed, internal)
        except Exception as exc:  # noqa: BLE001 - one bad file must not kill the run
            print(f"warning: could not scan {role} file: {type(exc).__name__}", file=sys.stderr)
            failed_entries.add((role, rel_path))
            garble_errors_set.add(rel_path)
            continue
        all_seen_idents.update(idents)
        file_defs.append((rel_path, defs))
        for item in defs:
            project_defined.add(item.name)
            if item.strong and item.name not in strong_categories:
                strong_categories[item.name] = item.category
        for comp in _path_components(rel_path):
            if is_never_renamed(comp):
                kept_path_components.add(comp)
            else:
                project_defined.add(comp)

    kept_names = (all_seen_idents - project_defined) | kept_path_components
    symbols = SymbolTable(kept_names)

    # Allocate identifier tokens in scan order using each name's resolved category.
    for rel_path, defs in file_defs:
        for comp in _path_components(rel_path):
            if comp in project_defined:
                symbols.assign_identifier(comp, strong_categories.get(comp, "v"))
        for item in defs:
            if item.name in project_defined:
                symbols.assign_identifier(item.name, strong_categories.get(item.name, "v"))

    # Garble all files in the same scan order so sN tokens are numbered deterministically.
    cand_garbled_by_orig: dict[str, bytes] = {}
    base_garbled_by_orig: dict[str, bytes] = {}

    for role, rel_path, parsed in ordered_entries:
        if (role, rel_path) in failed_entries:
            continue
        try:
            garbled_bytes, has_error = _garble_source(parsed, symbols, internal)
        except Exception as exc:  # noqa: BLE001 - one bad file must not kill the run
            print(f"warning: could not garble {role} file: {type(exc).__name__}", file=sys.stderr)
            garble_errors_set.add(rel_path)
            continue
        if has_error:
            garble_errors_set.add(rel_path)
        if role == "candidate":
            cand_garbled_by_orig[rel_path] = garbled_bytes
        else:
            base_garbled_by_orig[rel_path] = garbled_bytes

    for rel_path in sorted(base_parsed):
        if rel_path not in base_garbled_by_orig and rel_path in cand_garbled_by_orig:
            base_garbled_by_orig[rel_path] = cand_garbled_by_orig[rel_path]

    paths_map = {
        rel_path: garble_rel_path(rel_path, symbols.identifiers)
        for rel_path in all_supported_paths
    }

    # Write garbled candidate tree.
    tree_dir = out_dir / "tree"
    tree_dir.mkdir(parents=True, exist_ok=True)
    for rel_path in sorted(cand_garbled_by_orig):
        garbled_path = paths_map[rel_path]
        dest = tree_dir / garbled_path
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(cand_garbled_by_orig[rel_path])

    # Write unified diff when --base is given.
    if base_dir is not None:
        # A file that failed to garble has no trustworthy side; leave it out of the diff.
        base_by_garbled = {
            paths_map[rel_path]: content
            for rel_path, content in base_garbled_by_orig.items()
            if rel_path not in garble_errors_set
        }
        cand_by_garbled = {
            paths_map[rel_path]: content
            for rel_path, content in cand_garbled_by_orig.items()
            if rel_path not in garble_errors_set
        }
        diff_text = build_change_diff(base_by_garbled, cand_by_garbled)
        (out_dir / "change.diff").write_text(diff_text, encoding="utf-8")

    # Write mapping JSON outside --out.
    map_payload = {
        "version": 1,
        "identifiers": dict(sorted(symbols.identifiers.items())),
        "strings": dict(sorted(symbols.strings.items())),
        "paths": dict(sorted(paths_map.items())),
    }
    map_file.parent.mkdir(parents=True, exist_ok=True)
    map_file.write_text(
        json.dumps(map_payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    unsupported_changed = changed_unsupported(
        cand_scan.unsupported_files,
        base_scan.unsupported_files if base_scan is not None else None,
    )
    issues: list[dict[str, str]] = []
    issues_error: str | None = None
    if file_issues and unsupported_changed:
        issues, issues_error = file_unsupported_issues(unsupported_changed)
        if issues_error is not None:
            print(f"warning: issue filing failed: {issues_error}", file=sys.stderr)

    garble_errors = sorted(garble_errors_set)
    summary: dict[str, object] = {
        "supported": cand_scan.supported_counts,
        "unsupported": summarize_unsupported(cand_scan.unsupported_files.values()),
        "unsupported_changed": unsupported_changed,
        "omitted_non_code": cand_scan.omitted_non_code,
        "skipped_symlinks": cand_scan.skipped_symlinks,
        "parse_errors": sorted(parse_errors_set),
        "garble_errors": garble_errors,
        "issues": issues,
    }
    if issues_error is not None:
        summary["issues_error"] = issues_error

    exit_code = 1 if garble_errors else 0
    return summary, exit_code


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    """Build the top-level CLI parser with ``garble`` and ``reveal`` subcommands."""
    parser = argparse.ArgumentParser(
        description="Deterministic code garbler for blind structural code review.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_garble = sub.add_parser("garble", help="Garble a candidate checkout (and optional base).")
    p_garble.add_argument("--candidate", required=True, type=Path, help="Candidate git checkout.")
    p_garble.add_argument("--base", type=Path, help="Optional base git checkout for change.diff.")
    p_garble.add_argument("--out", required=True, type=Path, help="Output directory (absent/empty).")
    p_garble.add_argument("--map", required=True, type=Path, help="Mapping JSON path (outside --out).")
    p_garble.add_argument(
        "--file-issues",
        action="store_true",
        help="Ensure GitHub issues exist for unsupported languages in changed files (all files without --base).",
    )

    p_reveal = sub.add_parser("reveal", help="Map garbled tokens back to original names/strings.")
    p_reveal.add_argument("--map", required=True, type=Path, help="Mapping JSON file.")
    p_reveal.add_argument("--in", dest="input_path", type=Path, help="Input file (default stdin).")
    p_reveal.add_argument("--out", dest="output_path", type=Path, help="Output file (default stdout).")
    return parser


def main(argv: list[str] | None = None) -> int:
    """Run the CLI and return the process exit code."""
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        if args.command == "garble":
            summary, exit_code = run_garble(
                candidate_dir=args.candidate,
                base_dir=args.base,
                out_dir=args.out,
                map_file=args.map,
                file_issues=args.file_issues,
            )
            print(json.dumps(summary, indent=2, ensure_ascii=False))
            return exit_code

        if args.command == "reveal":
            map_path: Path = args.map
            if not map_path.is_file():
                raise InputError(f"--map file not found: {map_path}")
            try:
                map_data = json.loads(map_path.read_text(encoding="utf-8"))
            except json.JSONDecodeError as exc:
                raise InputError(f"invalid JSON in --map file {map_path}: {exc}") from exc
            if not isinstance(map_data, dict):
                raise InputError(f"--map file must contain a JSON object: {map_path}")

            if args.input_path is not None:
                if not args.input_path.is_file():
                    raise InputError(f"--in file not found: {args.input_path}")
                raw_text = args.input_path.read_text(encoding="utf-8")
            else:
                raw_text = sys.stdin.read()

            revealed = reveal_text(raw_text, map_data)
            if args.output_path is not None:
                args.output_path.parent.mkdir(parents=True, exist_ok=True)
                args.output_path.write_text(revealed, encoding="utf-8")
            else:
                sys.stdout.write(revealed)
            return 0
    except InputError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    return 2


if __name__ == "__main__":
    sys.exit(main())

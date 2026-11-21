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
"""Unit and integration tests for scripts/garble.py.

Run: uv run tests/test_garble.py
"""

from __future__ import annotations

import dataclasses
import json
import os
import re
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import ClassVar
from unittest import mock

SCRIPT_DIR = Path(__file__).resolve().parent.parent / "scripts"
GARBLE_SCRIPT = SCRIPT_DIR / "garble.py"
sys.path.insert(0, str(SCRIPT_DIR))

import garble

# ---------------------------------------------------------------------------
# Realistic multi-language fixtures (base vs candidate)
# ---------------------------------------------------------------------------

PY_AUTH_INIT = """\"\"\"Auth package marker.\"\"\"
"""

PY_AUTH_LOGIN_BASE = """#!/usr/bin/env python3
\"\"\"Handle credential verification.

Returns normalized tokens for active accounts.
\"\"\"

# Verify incoming user credentials
def verify_login(id: str, account_map: dict[str, str]) -> str:
    \"\"\"Look up an account by id.\"\"\"
    stored = account_map.get(id)
    if stored is None:
        return "missing"
    ordered = sorted([stored, id])
    return f"ok:{len(ordered)}:{stored}"
"""

PY_AUTH_LOGIN_CAND = """#!/usr/bin/env python3
\"\"\"Handle credential verification.

Returns normalized tokens for active accounts.
\"\"\"

# Verify incoming user credentials with prefix check
def verify_login(id: str, account_map: dict[str, str]) -> str:
    \"\"\"Look up an account by id.\"\"\"
    stored = account_map.get(id)
    if stored is None:
        return "missing"
    ordered = sorted([stored, id])
    prefix = "ok:"
    return f"{prefix}{len(ordered)}:{stored}"
"""

PY_APP_CAND = """from auth.login import verify_login


def run_check(account_map: dict[str, str]) -> str:
    \"\"\"Entry helper sharing a string literal with login.\"\"\"
    fallback = "missing"
    result = verify_login("alice", account_map)
    return result or fallback
"""

PY_DELETED_BASE = """# Legacy module removed in candidate
def legacy_cleanup(items: list[str]) -> int:
    return len(items)
"""

TS_INDEX_CAND = """export { formatBadge } from "./badge";
"""

TS_BADGE_BASE = """// Base badge helper
/* Multi-line
   block comment */
export interface BadgeProps {
    label: string;
    count: number;
}

export function formatBadge(props: BadgeProps): string {
    const pattern = /^badge-[0-9]+$/gi;
    console.log("missing", pattern.test(props.label));
    return `badge:${props.label}:${props.count}`;
}
"""

TS_BADGE_CAND = """// Candidate badge helper
/* Multi-line
   block comment */
export interface BadgeProps {
    label: string;
    count: number;
}

export function formatBadge(props: BadgeProps): string {
    const pattern = /^badge-[0-9]+$/gi;
    console.log("missing", pattern.test(props.label));
    return `badge:${props.label}:${props.count + 1}`;
}
"""

TSX_WIDGET_CAND = """import { useState } from "react";
import { readFileSync } from "node:fs";
import { parse } from "@scope/pkg";
import { formatBadge, BadgeProps } from "./badge";

export function StatusWidget(props: BadgeProps) {
    const [active, setActive] = useState(false);
    const title = formatBadge(props);
    void readFileSync;
    void parse;
    return (
        <section>
            <h1>Status Header</h1>
            <button onClick={() => setActive(!active)}>
                {title}
            </button>
        </section>
    );
}
"""

RS_MOD_CAND = """pub mod ledger;
"""

RS_LEDGER_BASE = """//! Ledger storage module.
/// Holds account balances.
/* Block
   comment */
pub struct LedgerEntry<'a> {
    pub owner: &'a str,
    pub balance: i64,
}

pub fn sum_balances<'a>(entries: &'a [LedgerEntry<'a>]) -> i64 {
    let mut totals = Vec::new();
    'outer: for entry in entries {
        if entry.balance < 0 {
            break 'outer;
        }
        totals.push(entry.balance);
    }
    let marker = 'x';
    let empty = "";
    let tag = r#"ledger-raw"#;
    let _ = (marker, empty, tag);
    totals.into_iter().reduce(|acc, val| acc + val).unwrap()
}
"""

RS_LEDGER_CAND = RS_LEDGER_BASE

GO_UNSUPPORTED = """package main

func main() {}
"""

README_NON_CODE = """# Sample Repo
Just documentation.
"""


ORIGIN_URL = "https://github.com/example/repo"

# ---------------------------------------------------------------------------
# Leak-regression fixtures: one file per supported language. Every secret name or
# string carries the marker "zqx" so a single scan finds anything left readable.
# ---------------------------------------------------------------------------

SECRET_MARKER = "zqx"

LEAK_PY = """#!/usr/bin/env python3
\"\"\"Module docstring zqxmoduledoc.\"\"\"
from dataclasses import dataclass
from typing import Annotated, Protocol

# comment zqxcomment


def zqx_decorator(*, zqx_deco_kw):
    def wrap(fn):
        return fn
    return wrap


@dataclass
class ZqxInvoice:
    \"\"\"Class docstring zqxclassdoc.\"\"\"

    zqx_amount: Annotated[int, "zqxannotationstring"] = 0


class ZqxProto(Protocol):
    def zqx_method(self, zqx_proto_param: int, /, zqx_kwonly: str = "zqxdefault") -> None: ...


@zqx_decorator(zqx_deco_kw="zqxdecoratorstring")
def zqx_charge(zqx_customer: str, *zqx_args: int, zqx_retry: int = 3, **zqx_kwargs: str) -> "ZqxInvoice":
    \"\"\"Charge docstring zqxchargedoc.\"\"\"
    zqx_other = object()
    zqx_other.zqx_dynamic_attr = 1
    zqx_other.zqx_ledger_attr += 1
    for zqx_obj in [zqx_other]:
        zqx_obj.zqx_new_attr = 2
    zqx_msg = f"hi {zqx_customer!r} {zqx_retry:%Y-%m-zqxfmt} {zqx_retry:>{zqx_retry}zqxwidth}"
    zqx_raw = rb"zqxraw\\bytes"
    zqx_multi = \"\"\"zqxmulti
    line\"\"\"
    zqx_joined = "zqxadjacent " "zqxconcat"
    return ZqxInvoice(zqx_amount=len(zqx_msg + zqx_raw.decode() + zqx_multi + zqx_joined))


def zqx_match(zqx_cmd):
    match zqx_cmd:
        case {"zqxkey": zqx_val, **zqx_rest}:
            return zqx_val, zqx_rest
        case ZqxInvoice(zqx_amount=zqx_amt):
            return zqx_amt
    if (zqx_walrus := len(zqx_cmd)) > 1:
        return [zqx_c for zqx_c in zqx_cmd if zqx_c]
    try:
        pass
    except ValueError as zqx_err:
        raise zqx_err
    return lambda zqx_lam, *zqx_lam_rest: zqx_lam
"""

LEAK_TS = """// comment zqxcomment
/* block zqxblock */
export type ZqxHandler = (zqxCustomerName: string, zqxInvoiceTotal: number) => void;
export type ZqxCtor = abstract new (zqxCtorTypeArg: string) => ZqxApi;
export interface ZqxApi {
  [zqxLookupKey: string]: number;
  (zqxCallArgSecret: string): void;
  new (zqxCtorArgSecret: string): ZqxApi;
  zqxMethodSig(zqxSigParam: number): void;
  readonly zqxProp: string;
}
export type ZqxTuple = [zqxFirst: string, zqxSecond?: number, ...zqxRest: boolean[]];
export function zqxIsThing(zqxValue: unknown): zqxValue is ZqxApi { return true; }
export function zqxAssert(zqxCond: unknown): asserts zqxCond {}
export function zqxOverload(zqxOverA: string): string;
export function zqxOverload(zqxOverB: number): number;
export function zqxOverload(zqxImpl: any): any { return zqxImpl; }
declare function zqxAmbient(zqxAmbientParam: string): void;
export type ZqxCond<ZqxT> = ZqxT extends Array<infer ZqxElem> ? ZqxElem : never;
export type ZqxMapped = { [ZqxKey in "zqxmappedkey"]: number };
export type ZqxTemplate = `zqx-${string}-zqxtail`;
declare module "zqx-ambient-module" { export const zqxAmbientConst: number; }
export type ZqxSelf = typeof import("zqxapp/zqxtypes");
export function zqxDecor(..._zqxDecorArgs: unknown[]): any { return () => undefined; }

@zqxDecor("zqxdecoratorstring")
export abstract class ZqxBase {
  @zqxDecor() zqxDecorated = "zqxfieldstring";
  static #zqxPrivate = 1;
  constructor(private readonly zqxSvc: string, public zqxOpt?: number) {}
  abstract zqxDoIt(zqxAbstractParam: string): void;
  @zqxDecor() zqxMethod(@zqxDecor() zqxDecoratedParam: string): string { return zqxDecoratedParam; }
  get zqxAccessor() { return 1; }
  set zqxAccessor(zqxSetVal: number) {}
}
export enum ZqxColor { ZqxRed = "zqxredvalue", ZqxBlue = 2 }
export namespace ZqxNs { export const zqxInner = 1; }
export const zqxExportsLike: any = {};
zqxExportsLike.zqxAssignedProp = 5;
zqxExportsLike.zqxLedgerAttr += 1;
module.exports.zqxCjsName = 1;
export const zqxRegex = /zqxregex-\\d+/gi;
export const zqxTpl = `zqx ${zqxRegex.source} zqxtpl`;
export const zqxObj = { "zqx-quoted-key": 1, zqxPlainKey: 2, [`zqxcomputed${1}`]: 3 };
"""

LEAK_TSX = """import { useState } from "react";
import type { ZqxApi } from "zqxapp/zqxtypes";
import { zqxDecor } from "zqxapp/zqxtypes.js";

// comment zqxcomment
interface ZqxProps {
  zqxTitle: string;
  "zqx-quoted": number;
  onZqxClick?: (zqxEvent: Event) => void;
}

export function ZqxWidget({ zqxTitle, onZqxClick }: ZqxProps) {
  const [zqxActive, zqxSetActive] = useState(false);
  const zqxLabel = `Click zqxtemplate ${zqxTitle} now`;
  const zqxRe = /zqx-pattern\\d+/gi;
  const zqxApi: ZqxApi | undefined = undefined;
  zqxDecor(zqxRe, zqxApi, onZqxClick);
  return (
    <div className="zqx-class" data-zqx-testid="zqxtid" data-customer-ssn="zqxssn" aria-label="zqxarialabel">
      {/* jsx comment zqxjsxcomment */}
      Hello Zqx World
      <ZqxInner zqxPropName="zqxpropvalue" {...{ zqxSpread: 1 }}>text &amp; zqxentity</ZqxInner>
      <a href="https://zqx.example.com">zqx link</a>
      <form>
        <input type="text" value={zqxLabel} />
        <button onClick={() => zqxSetActive(!zqxActive)}>go</button>
      </form>
      <svg:rect xlink:href="zqxsvg" />
    </div>
  );
}

function ZqxInner(zqxInnerProps: { zqxPropName?: string; children?: unknown }) {
  return <span>{String(zqxInnerProps.children)}</span>;
}
"""

LEAK_JSX = """const zqxTypes = require("zqxapp/zqxtypes");
const zqxLazy = () => import("zqxui/zqxwidget");
jest.mock("zqxapp/zqxtypes");
vi.mock("zqxui/zqxwidget.tsx");

// comment zqxcomment
function zqxDecor() { return (zqxTarget) => zqxTarget; }

@zqxDecor("zqxdecoratorstring")
class ZqxClass {
  #zqxPriv = 1;
  static zqxStatic = "zqxstaticstring";
  get zqxAccessor() { return this.#zqxPriv; }
  zqxMethod(zqxParam, { zqxDestructured } = {}) { return [zqxParam, zqxDestructured, zqxTypes, zqxLazy]; }
}

const zqxOther = {};
zqxOther.zqxLedgerAttr += 1;
zqxOther.zqxNewAttrSecret = 2;
module.exports.zqxCjsName = 1;
const zqxView = <p data-zqx-id="zqxid" aria-hidden="true">zqx text <b>zqx bold</b></p>;
const zqxRe = /zqx[a-z]+/u;
const zqxTpl = `zqx ${zqxOther} tail zqxtail`;
export { ZqxClass, zqxView, zqxRe, zqxTpl };
"""

LEAK_RS = """//! crate doc zqxcratedoc
/// doc comment zqxdoc
/* block zqxblock */
use std::collections::HashMap;

macro_rules! zqx_macro { ($zqx_arg:expr) => { $zqx_arg + 1 }; }

#[derive(Debug)]
#[serde(rename = "zqxrename")]
pub struct ZqxEngine<'zqx_life> { pub zqx_field: &'zqx_life str }

pub enum ZqxKind { ZqxUnit, ZqxTuple(i32), ZqxNamed { zqx_variant_field: u8 } }

pub trait ZqxShape { fn zqx_area(&self) -> f64; const ZQX_NAME: &'static str; type ZqxOut; }

#[cfg(feature = "zqxfeature")]
fn zqx_cfg() {}

mod zqx_nested { pub fn zqx_deep() {} }
use zqx_nested::zqx_deep as zqx_renamed;

impl<'a> ZqxEngine<'a> {
    pub fn zqx_run(&self, zqx_input: &str) -> Result<(), String> {
        let zqx_name = "zqxstring";
        println!("hello {zqx_name} {}", self.zqx_field);
        let zqx_char = 'q';
        let zqx_bytes = b"zqxbytes";
        let zqx_raw = r#"zqxraw"#;
        // comment zqxcomment
        let zqx_map: HashMap<String, i32> = HashMap::new();
        'zqx_outer: for zqx_i in 0..3 { if zqx_i > 1 { break 'zqx_outer; } }
        let zqx_closure = |zqx_a, (zqx_b, zqx_c): (i32, i32)| zqx_a + zqx_b + zqx_c;
        let ZqxKind::ZqxNamed { zqx_variant_field: zqx_fv } = ZqxKind::ZqxNamed { zqx_variant_field: 1 } else { return Ok(()) };
        let _ = (zqx_char, zqx_bytes, zqx_raw, zqx_map, zqx_closure, zqx_fv, zqx_macro!(1), zqx_renamed());
        match zqx_input.len() { 0 => Ok(()), 1 => Err("zqxerr".into()), _ => Ok(()) }
    }
}
"""

LEAK_FILES: dict[str, str] = {
    "zqxpkg/zqxbilling.py": LEAK_PY,
    "zqxapp/zqxtypes.ts": LEAK_TS,
    "zqxui/zqxwidget.tsx": LEAK_TSX,
    "zqxlib/zqxcjs.jsx": LEAK_JSX,
    "zqxcrate/zqxengine.rs": LEAK_RS,
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _git(cwd: Path, *args: str) -> None:
    proc = subprocess.run(
        ["git", "-C", str(cwd), *args],
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed: {proc.stderr}")


def init_git_repo(root: Path, files: dict[str, str], symlinks: dict[str, str] | None = None) -> Path:
    """Create a temporary git repository populated and committed with ``files``."""
    root.mkdir(parents=True, exist_ok=True)
    _git(root, "init", "-q")
    for rel_path, content in sorted(files.items()):
        dest = root / rel_path
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(content, encoding="utf-8")
    if symlinks:
        for rel_path, target in sorted(symlinks.items()):
            dest = root / rel_path
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.symlink_to(target)
    _git(root, "add", "-A")
    _git(
        root,
        "-c",
        "user.name=Test",
        "-c",
        "user.email=test@example.com",
        "-c",
        "commit.gpgsign=false",
        "commit",
        "-q",
        "-m",
        "initial",
    )
    return root


def write_fake_gh(bin_dir: Path, log_file: Path, list_payload: list[dict[str, object]], created_url: str) -> Path:
    """Create a fake ``gh`` script that logs invocations and returns canned JSON."""
    gh_path = bin_dir / "gh"
    script = f"""#!{sys.executable}
import json
import sys
from pathlib import Path

log_file = Path({str(log_file)!r})
argv = sys.argv[1:]
with log_file.open("a", encoding="utf-8") as fh:
    fh.write(json.dumps(argv) + "\\n")

if argv[:2] == ["issue", "list"]:
    print(json.dumps({list_payload!r}))
    sys.exit(0)
if argv[:2] == ["issue", "create"]:
    print({created_url!r})
    sys.exit(0)
sys.stderr.write("unexpected gh command\\n")
sys.exit(1)
"""
    gh_path.write_text(script, encoding="utf-8")
    gh_path.chmod(gh_path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return gh_path


def run_cli(
    argv: list[str],
    *,
    env: dict[str, str] | None = None,
    stdin_text: str | None = None,
    script: Path = GARBLE_SCRIPT,
) -> subprocess.CompletedProcess[str]:
    """Run ``garble.py`` (or a copy of it) directly using the active interpreter."""
    return subprocess.run(
        [sys.executable, str(script), *argv],
        input=stdin_text,
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )


def script_copy_with_origin(root: Path, origin_url: str) -> Path:
    """Copy ``garble.py`` into a temp git repo whose ``origin`` is ``origin_url``.

    Issue filing resolves the repo from the git checkout that hosts the script, so
    running the copy keeps these tests independent of the real repo's remote.
    """
    repo = init_git_repo(root / "script_repo", {"README.md": "script host\n"})
    _git(repo, "remote", "add", "origin", origin_url)
    copy = repo / "garble.py"
    copy.write_bytes(GARBLE_SCRIPT.read_bytes())
    return copy


def gh_calls(log_file: Path) -> list[list[str]]:
    """Return every logged fake-``gh`` invocation as an argv list."""
    if not log_file.exists():
        return []
    return [json.loads(line) for line in log_file.read_text(encoding="utf-8").splitlines()]


def repo_args(calls: list[list[str]]) -> list[str]:
    """Return the value passed to ``--repo`` in each logged call."""
    return [call[call.index("--repo") + 1] for call in calls if "--repo" in call]


def snapshot_tree(root: Path) -> dict[str, bytes]:
    """Return a sorted mapping of relative POSIX path -> file bytes under ``root``."""
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


@dataclasses.dataclass(frozen=True)
class GarbleRun:
    """Result of garbling a temporary checkout in-process."""

    out_dir: Path
    map_data: dict[str, dict[str, str]]
    summary: dict[str, object]
    exit_code: int

    def read(self, rel_path: str) -> str:
        """Return the garbled text of the original ``rel_path``."""
        gpath = self.map_data["paths"][rel_path]
        return (self.out_dir / "tree" / gpath).read_text(encoding="utf-8")

    def token(self, name: str) -> str:
        """Return the identifier token allocated for ``name``."""
        return self.map_data["identifiers"][name]


def garble_files(
    root: Path,
    files: dict[str, str],
    base_files: dict[str, str] | None = None,
) -> GarbleRun:
    """Commit ``files`` (and optionally ``base_files``) under ``root`` and garble them."""
    candidate = init_git_repo(root / "candidate", files)
    base = init_git_repo(root / "base", base_files) if base_files is not None else None
    out_dir = root / "out"
    map_file = root / "map.json"
    summary, code = garble.run_garble(candidate, base, out_dir, map_file)
    map_data = json.loads(map_file.read_text(encoding="utf-8"))
    return GarbleRun(out_dir=out_dir, map_data=map_data, summary=summary, exit_code=code)


def output_texts(out_dir: Path) -> dict[str, str]:
    """Return every file under ``out_dir`` as ``{relative path: text}``."""
    return {
        path.relative_to(out_dir).as_posix(): path.read_text(encoding="utf-8")
        for path in sorted(out_dir.rglob("*"))
        if path.is_file()
    }


# ---------------------------------------------------------------------------
# Test suite
# ---------------------------------------------------------------------------


class GarbleEndToEndTests(unittest.TestCase):
    """End-to-end verification across Python, TypeScript/TSX, Rust, and diffs."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)

        self.base_files: dict[str, str] = {
            "auth/__init__.py": PY_AUTH_INIT,
            "auth/login.py": PY_AUTH_LOGIN_BASE,
            "auth/legacy.py": PY_DELETED_BASE,
            "ui/badge.ts": TS_BADGE_BASE,
            "core/mod.rs": RS_MOD_CAND,
            "core/ledger.rs": RS_LEDGER_BASE,
            "README.md": README_NON_CODE,
        }
        self.cand_files: dict[str, str] = {
            "auth/__init__.py": PY_AUTH_INIT,
            "auth/login.py": PY_AUTH_LOGIN_CAND,
            "app.py": PY_APP_CAND,
            "ui/index.ts": TS_INDEX_CAND,
            "ui/badge.ts": TS_BADGE_CAND,
            "ui/widget.tsx": TSX_WIDGET_CAND,
            "core/mod.rs": RS_MOD_CAND,
            "core/ledger.rs": RS_LEDGER_CAND,
            "tools/helper.go": GO_UNSUPPORTED,
            "README.md": README_NON_CODE,
        }

        self.base_dir = init_git_repo(self.root / "base", self.base_files)
        self.cand_dir = init_git_repo(
            self.root / "cand",
            self.cand_files,
            symlinks={"auth/link.py": "login.py"},
        )
        self.out_dir = self.root / "out"
        self.map_file = self.root / "map.json"

        self.summary, self.exit_code = garble.run_garble(
            candidate_dir=self.cand_dir,
            base_dir=self.base_dir,
            out_dir=self.out_dir,
            map_file=self.map_file,
        )
        self.map_data = json.loads(self.map_file.read_text(encoding="utf-8"))
        self.ident_map: dict[str, str] = self.map_data["identifiers"]
        self.string_map: dict[str, str] = self.map_data["strings"]
        self.path_map: dict[str, str] = self.map_data["paths"]

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _read_garbled(self, orig_rel_path: str) -> str:
        gpath = self.path_map[orig_rel_path]
        return (self.out_dir / "tree" / gpath).read_text(encoding="utf-8")

    def test_exit_code_and_summary_counts(self) -> None:
        self.assertEqual(self.exit_code, 0)
        self.assertEqual(self.summary["garble_errors"], [])
        self.assertEqual(self.summary["parse_errors"], [])
        self.assertEqual(
            self.summary["supported"],
            {"python": 3, "rust": 2, "tsx": 1, "typescript": 2},
        )
        self.assertEqual(
            self.summary["unsupported"],
            {"Go": {"extensions": [".go"], "files": 1}},
        )
        self.assertEqual(self.summary["omitted_non_code"], 1)
        self.assertEqual(self.summary["skipped_symlinks"], 1)
        # Unsupported Go file must not appear in output tree or path map.
        self.assertNotIn("tools/helper.go", self.path_map)
        self.assertFalse(any(p.suffix == ".go" for p in (self.out_dir / "tree").rglob("*")))

    def test_consistent_renaming_across_files_and_trees(self) -> None:
        verify_tok = self.ident_map["verify_login"]
        badge_tok = self.ident_map["formatBadge"]
        self.assertTrue(verify_tok.startswith("f"))
        self.assertTrue(badge_tok.startswith("f"))
        self.assertTrue(self.ident_map["BadgeProps"].startswith("t"))
        self.assertTrue(self.ident_map["LedgerEntry"].startswith("t"))

        login_code = self._read_garbled("auth/login.py")
        app_code = self._read_garbled("app.py")
        self.assertIn(f"def {verify_tok}(", login_code)
        self.assertIn(verify_tok, app_code)
        self.assertNotIn("verify_login", login_code)
        self.assertNotIn("verify_login", app_code)

        badge_code = self._read_garbled("ui/badge.ts")
        widget_code = self._read_garbled("ui/widget.tsx")
        self.assertIn(f"function {badge_tok}(", badge_code)
        self.assertIn(badge_tok, widget_code)
        self.assertNotIn("formatBadge", badge_code)
        self.assertNotIn("formatBadge", widget_code)

    def test_builtins_and_external_apis_kept(self) -> None:
        login_code = self._read_garbled("auth/login.py")
        # Builtins & external dict method kept; param named like builtin `id` kept.
        self.assertIn("def " + self.ident_map["verify_login"] + "(id: str,", login_code)
        self.assertIn(".get(id)", login_code)
        self.assertIn("sorted([", login_code)
        self.assertIn("len(", login_code)
        self.assertNotIn("id", self.ident_map)

        badge_code = self._read_garbled("ui/badge.ts")
        self.assertIn("console.log(", badge_code)

        widget_code = self._read_garbled("ui/widget.tsx")
        self.assertIn('import { useState } from "react";', widget_code)
        self.assertIn('import { readFileSync } from "node:fs";', widget_code)
        self.assertIn('import { parse } from "@scope/pkg";', widget_code)
        self.assertIn("useState(false)", widget_code)
        self.assertNotIn("useState", self.ident_map)

        ledger_code = self._read_garbled("core/ledger.rs")
        self.assertIn("Vec::new()", ledger_code)
        self.assertIn(".unwrap()", ledger_code)
        self.assertIn("'x'", ledger_code)
        self.assertIn('""', ledger_code)
        # Raw string delimiter preserved.
        raw_tok = self.string_map["ledger-raw"]
        self.assertIn(f'r#"{raw_tok}"#', ledger_code)

    def test_strings_interpolations_docstrings_and_regex(self) -> None:
        # Identical string content ("missing") gets the exact same token across Python and TS.
        missing_tok = self.string_map["missing"]
        self.assertRegex(missing_tok, r"^s[1-9][0-9]*$")
        self.assertIn(f'"{missing_tok}"', self._read_garbled("auth/login.py"))
        self.assertIn(f'"{missing_tok}"', self._read_garbled("app.py"))
        self.assertIn(f'"{missing_tok}"', self._read_garbled("ui/badge.ts"))
        self.assertNotIn("missing", self._read_garbled("auth/login.py"))

        # Python f-string interpolations are garbled as code.
        login_code = self._read_garbled("auth/login.py")
        prefix_tok = self.ident_map["prefix"]
        ordered_tok = self.ident_map["ordered"]
        stored_tok = self.ident_map["stored"]
        self.assertIn(f"{{{prefix_tok}}}{{len({ordered_tok})}}", login_code)
        self.assertIn(f"{{{stored_tok}}}", login_code)

        # Docstring text is replaced with sN, delimiters kept.
        self.assertNotIn("Handle credential verification", login_code)
        self.assertIn('"""s', login_code)

        # TS template literal interpolations garbled as code, regex pattern garbled & flags kept.
        badge_code = self._read_garbled("ui/badge.ts")
        props_tok = self.ident_map["props"]
        label_tok = self.ident_map["label"]
        count_tok = self.ident_map["count"]
        self.assertIn(f"${{{props_tok}.{label_tok}}}", badge_code)
        self.assertIn(f"${{{props_tok}.{count_tok} + 1}}", badge_code)
        regex_tok = self.string_map["^badge-[0-9]+$"]
        self.assertIn(f"/{regex_tok}/gi", badge_code)

        # JSX text garbled and JSX expression garbled as code.
        widget_code = self._read_garbled("ui/widget.tsx")
        self.assertNotIn("Status Header", widget_code)
        self.assertIn(self.string_map["Status Header"], widget_code)
        self.assertIn(f"{{{self.ident_map['title']}}}", widget_code)

    def test_comments_removed_and_shebang_kept(self) -> None:
        login_code = self._read_garbled("auth/login.py")
        self.assertTrue(login_code.startswith("#!/usr/bin/env python3\n"))
        self.assertNotIn("Verify incoming user credentials", login_code)

        badge_code = self._read_garbled("ui/badge.ts")
        self.assertNotIn("Candidate badge helper", badge_code)
        self.assertNotIn("block comment", badge_code)

        ledger_code = self._read_garbled("core/ledger.rs")
        self.assertNotIn("Ledger storage module", ledger_code)
        self.assertNotIn("Holds account balances", ledger_code)
        self.assertNotIn("Block", ledger_code)

    def test_line_counts_preserved_and_every_file_reparses(self) -> None:
        for rel_path, orig_content in self.cand_files.items():
            suffix = Path(rel_path).suffix.lower()
            if suffix not in garble.SUPPORTED_EXTENSIONS:
                continue
            garbled_content = self._read_garbled(rel_path)
            self.assertEqual(
                orig_content.count("\n"),
                garbled_content.count("\n"),
                f"newline count mismatch for {rel_path}",
            )
            self.assertEqual(
                len(orig_content.splitlines()),
                len(garbled_content.splitlines()),
                f"splitlines count mismatch for {rel_path}",
            )
            lang = garble.SUPPORTED_EXTENSIONS[suffix]
            tree = garble.get_parser(lang).parse(garbled_content.encode("utf-8"))
            self.assertFalse(tree.root_node.has_error, f"re-parse failed for {rel_path}")

    def test_paths_match_module_identifiers_and_conventional_stems_kept(self) -> None:
        auth_tok = self.ident_map["auth"]
        login_tok = self.ident_map["login"]
        ui_tok = self.ident_map["ui"]
        core_tok = self.ident_map["core"]
        ledger_tok = self.ident_map["ledger"]

        self.assertEqual(self.path_map["auth/__init__.py"], f"{auth_tok}/__init__.py")
        self.assertEqual(self.path_map["auth/login.py"], f"{auth_tok}/{login_tok}.py")
        self.assertEqual(self.path_map["ui/index.ts"], f"{ui_tok}/index.ts")
        self.assertEqual(self.path_map["core/mod.rs"], f"{core_tok}/mod.rs")
        self.assertEqual(self.path_map["core/ledger.rs"], f"{core_tok}/{ledger_tok}.rs")

        # Python import statement uses the exact same tokens as the garbled path.
        app_code = self._read_garbled("app.py")
        self.assertIn(f"from {auth_tok}.{login_tok} import {self.ident_map['verify_login']}", app_code)

        # Rust mod declaration uses the exact same token as the garbled path.
        mod_code = self._read_garbled("core/mod.rs")
        self.assertIn(f"pub mod {ledger_tok};", mod_code)

        # TS internal relative specifier reuses the path token.
        index_code = self._read_garbled("ui/index.ts")
        self.assertIn(f'"./{self.ident_map["badge"]}"', index_code)

    def test_change_diff_modified_added_deleted(self) -> None:
        diff_text = (self.out_dir / "change.diff").read_text(encoding="utf-8")
        g_login = self.path_map["auth/login.py"]
        g_badge = self.path_map["ui/badge.ts"]
        g_app = self.path_map["app.py"]
        g_deleted = self.path_map["auth/legacy.py"]
        g_ledger = self.path_map["core/ledger.rs"]

        # Modified files present.
        self.assertIn(f"--- a/{g_login}\n+++ b/{g_login}\n", diff_text)
        self.assertIn(f"--- a/{g_badge}\n+++ b/{g_badge}\n", diff_text)
        # Added file uses /dev/null as source.
        self.assertIn(f"--- /dev/null\n+++ b/{g_app}\n", diff_text)
        # Deleted file uses /dev/null as destination and has its symbols garbled.
        self.assertIn(f"--- a/{g_deleted}\n+++ /dev/null\n", diff_text)
        self.assertNotIn("legacy_cleanup", diff_text)
        self.assertIn(self.ident_map["legacy_cleanup"], diff_text)
        # Unchanged file omitted from diff.
        self.assertNotIn(g_ledger, diff_text)

    def test_byte_identical_determinism_across_runs(self) -> None:
        out_dir_2 = self.root / "out2"
        map_file_2 = self.root / "map2.json"
        summary_2, exit_code_2 = garble.run_garble(
            candidate_dir=self.cand_dir,
            base_dir=self.base_dir,
            out_dir=out_dir_2,
            map_file=map_file_2,
        )
        self.assertEqual(exit_code_2, 0)
        self.assertEqual(self.summary, summary_2)
        self.assertEqual(self.map_file.read_bytes(), map_file_2.read_bytes())
        self.assertEqual(snapshot_tree(self.out_dir), snapshot_tree(out_dir_2))

    def test_reveal_round_trip(self) -> None:
        g_login = self.path_map["auth/login.py"]
        verify_tok = self.ident_map["verify_login"]
        missing_tok = self.string_map["missing"]
        doc_key = next(k for k in self.string_map if "\n" in k)
        doc_tok = self.string_map[doc_key]

        finding = (
            f"Bug in {g_login}:12 inside {verify_tok}: returns {missing_tok} "
            f"(doc {doc_tok}) and ignores unknown v9999 / s9999."
        )
        proc = run_cli(["reveal", "--map", str(self.map_file)], stdin_text=finding)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        expected_doc = doc_key.replace("\n", "\\n")
        expected_text = (
            f"Bug in auth/login.py:12 inside verify_login: returns missing "
            f"(doc {expected_doc}) and ignores unknown v9999 / s9999."
        )
        self.assertEqual(proc.stdout, expected_text)

        # Also verify reveal with explicit --in and --out files.
        in_file = self.root / "finding.txt"
        out_file = self.root / "revealed.txt"
        in_file.write_text(finding, encoding="utf-8")
        proc_io = run_cli(
            ["reveal", "--map", str(self.map_file), "--in", str(in_file), "--out", str(out_file)]
        )
        self.assertEqual(proc_io.returncode, 0, proc_io.stderr)
        self.assertEqual(out_file.read_text(encoding="utf-8"), expected_text)


class GarbleEdgeCaseTests(unittest.TestCase):
    """Focused tests for CLI guards, issue filing, token collisions, and edge syntax."""

    def test_rejects_map_inside_out_and_non_empty_out_and_non_git(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = init_git_repo(root / "repo", {"main.py": "def helper():\n    return 1\n"})
            non_git = root / "plain_dir"
            non_git.mkdir()

            # Non-git directory -> exit 2.
            proc = run_cli([
                "garble",
                "--candidate",
                str(non_git),
                "--out",
                str(root / "out1"),
                "--map",
                str(root / "map1.json"),
            ])
            self.assertEqual(proc.returncode, 2)

            # --map inside --out -> exit 2.
            out_dir = root / "out2"
            proc = run_cli([
                "garble",
                "--candidate",
                str(repo),
                "--out",
                str(out_dir),
                "--map",
                str(out_dir / "nested" / "map.json"),
            ])
            self.assertEqual(proc.returncode, 2)

            # Non-empty --out -> exit 2.
            non_empty = root / "out3"
            non_empty.mkdir()
            (non_empty / "existing.txt").write_text("occupied", encoding="utf-8")
            proc = run_cli([
                "garble",
                "--candidate",
                str(repo),
                "--out",
                str(non_empty),
                "--map",
                str(root / "map3.json"),
            ])
            self.assertEqual(proc.returncode, 2)

    def test_token_collision_with_kept_names_and_private_fields_and_lifetimes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = init_git_repo(
                root / "repo",
                {
                    "client.py": (
                        "def fetch(api):\n"
                        "    return api.v1.f1.s1(v2='line1 \\\nline2')\n"
                    ),
                    "box.js": (
                        "class SecretBox {\n"
                        "  #hidden = 1;\n"
                        "  read() { return this.#hidden; }\n"
                        "}\n"
                        "const metaUrl = import.meta.url;\n"
                        "const x = 1/*inline*/+2;\n"
                    ),
                    "lifetimes.rs": (
                        "pub fn pick<'a>(first: &'a str, fallback: &'static str) -> &'a str {\n"
                        "    let _ = fallback;\n"
                        "    first\n"
                        "}\n"
                    ),
                },
            )
            out_dir = root / "out"
            map_file = root / "map.json"
            summary, code = garble.run_garble(repo, None, out_dir, map_file)
            self.assertEqual(code, 0)
            self.assertEqual(summary["garble_errors"], [])

            map_data = json.loads(map_file.read_text(encoding="utf-8"))
            ident_values = set(map_data["identifiers"].values())
            string_values = set(map_data["strings"].values())
            # Kept external attribute names v1, f1, s1 must never be allocated as tokens.
            self.assertNotIn("v1", ident_values)
            self.assertNotIn("f1", ident_values)
            self.assertNotIn("s1", string_values)

            # JS private field becomes #<token> and reveals back cleanly.
            hidden_tok = map_data["identifiers"]["hidden"]
            box_garbled = (out_dir / "tree" / map_data["paths"]["box.js"]).read_text(encoding="utf-8")
            self.assertIn(f"#{hidden_tok}", box_garbled)
            self.assertIn("import.meta.url", box_garbled)
            self.assertEqual(garble.reveal_text(f"#{hidden_tok}", map_data), "#hidden")

            # Rust lifetime 'a becomes '<token>, while 'static stays 'static.
            a_tok = map_data["identifiers"]["a"]
            rs_garbled = (out_dir / "tree" / map_data["paths"]["lifetimes.rs"]).read_text(encoding="utf-8")
            self.assertIn(f"'{a_tok}", rs_garbled)
            self.assertIn("'static", rs_garbled)
            self.assertEqual(garble.reveal_text(f"'{a_tok}", map_data), "'a")

    def test_best_effort_garble_on_original_parse_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = init_git_repo(
                root / "repo",
                {"broken.py": "def broken_fn(:\n    secret = 'val'\n"},
            )
            out_dir = root / "out"
            map_file = root / "map.json"
            summary, code = garble.run_garble(repo, None, out_dir, map_file)
            self.assertEqual(code, 0)
            self.assertEqual(summary["parse_errors"], ["broken.py"])
            self.assertEqual(summary["garble_errors"], [])

    def test_parse_github_repo_https_and_ssh(self) -> None:
        self.assertEqual(
            garble.parse_github_repo("https://github.com/octocat/hello-world.git"),
            "octocat/hello-world",
        )
        self.assertEqual(
            garble.parse_github_repo("https://github.com/octocat/hello-world"),
            "octocat/hello-world",
        )
        self.assertEqual(
            garble.parse_github_repo("git@github.com:octocat/hello-world.git"),
            "octocat/hello-world",
        )
        self.assertEqual(
            garble.parse_github_repo("ssh://git@github.com/octocat/hello-world.git"),
            "octocat/hello-world",
        )

    def test_file_issues_with_base_covers_only_changed_unsupported_files(self) -> None:
        shared = {
            "main.py": "def compute(val):\n    return val + 1\n",
            "tools/build.sh": "#!/bin/sh\necho build\n",
        }
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            base = init_git_repo(root / "base", shared)
            candidate = init_git_repo(root / "candidate", {**shared, "cmd/new.go": "package cmd\n"})
            script = script_copy_with_origin(root, ORIGIN_URL)
            bin_dir = root / "bin"
            bin_dir.mkdir()
            log_file = root / "gh.log"
            write_fake_gh(bin_dir, log_file, [], "https://github.com/example/repo/issues/5")
            env = os.environ.copy()
            env["PATH"] = f"{bin_dir}{os.pathsep}{env.get('PATH', '')}"

            proc = run_cli(
                [
                    "garble",
                    "--base",
                    str(base),
                    "--candidate",
                    str(candidate),
                    "--out",
                    str(root / "out"),
                    "--map",
                    str(root / "map.json"),
                    "--file-issues",
                ],
                env=env,
                script=script,
            )

            self.assertEqual(proc.returncode, 0, proc.stderr)
            summary = json.loads(proc.stdout)
            self.assertEqual(sorted(summary["unsupported"]), ["Go", "Shell"])
            self.assertEqual(summary["unsupported_changed"], {"Go": {"extensions": [".go"], "files": 1}})
            self.assertEqual([issue["language"] for issue in summary["issues"]], ["Go"])
            calls = gh_calls(log_file)
            titles = [call[call.index("--title") + 1] for call in calls if call[:2] == ["issue", "create"]]
            self.assertEqual(titles, ["council garbler: support Go"])
            self.assertEqual(repo_args(calls), ["example/repo"] * len(calls))

    def test_file_issues_rejects_non_github_origin_without_calling_gh(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = init_git_repo(root / "repo", {"cmd/new.go": "package cmd\n"})
            script = script_copy_with_origin(root, "git@gitlab.com:me/proj.git")
            bin_dir = root / "bin"
            bin_dir.mkdir()
            log_file = root / "gh.log"
            write_fake_gh(bin_dir, log_file, [], "https://github.com/example/repo/issues/1")
            env = os.environ.copy()
            env["PATH"] = f"{bin_dir}{os.pathsep}{env.get('PATH', '')}"

            proc = run_cli(
                [
                    "garble",
                    "--candidate",
                    str(repo),
                    "--out",
                    str(root / "out"),
                    "--map",
                    str(root / "map.json"),
                    "--file-issues",
                ],
                env=env,
                script=script,
            )

            self.assertEqual(proc.returncode, 0, proc.stderr)
            summary = json.loads(proc.stdout)
            self.assertEqual(summary["issues"], [])
            self.assertIn("gitlab.com", summary["issues_error"])
            self.assertEqual(gh_calls(log_file), [])

    def test_parse_github_repo_rejects_other_hosts_and_shapes(self) -> None:
        for remote in (
            "https://gitlab.com/me/proj.git",
            "git@gitlab.com:me/proj.git",
            "ssh://git@bitbucket.org/me/proj.git",
            "https://github.com.evil.example/me/proj",
            "https://example.com/github.com/me/proj",
            "https://github.com/me",
            "https://github.com/me/proj/extra",
            "git@github.com:me/proj/extra.git",
            "git@github.com:proj.git",
            "http://github.com/me/proj",
            "file:///srv/git/me/proj",
            "/srv/git/proj.git",
            "",
        ):
            with self.subTest(remote=remote), self.assertRaises(ValueError):
                garble.parse_github_repo(remote)

    def test_file_issues_creates_and_deduplicates_open_and_closed(self) -> None:
        secret_rel_path = "private_client/secret_billing/invoice.go"
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = init_git_repo(
                root / "repo",
                {
                    "main.py": "def compute(val):\n    return val + 1\n",
                    secret_rel_path: "package billing\nfunc Charge() {}\n",
                },
            )
            script = script_copy_with_origin(root, ORIGIN_URL)

            # Case 1: no existing issue -> creates one, body has no file paths.
            bin1 = root / "bin1"
            bin1.mkdir()
            log1 = root / "gh1.log"
            write_fake_gh(bin1, log1, [], "https://github.com/example/repo/issues/42")
            env1 = os.environ.copy()
            env1["PATH"] = f"{bin1}{os.pathsep}{env1.get('PATH', '')}"

            proc1 = run_cli(
                [
                    "garble",
                    "--candidate",
                    str(repo),
                    "--out",
                    str(root / "out1"),
                    "--map",
                    str(root / "map1.json"),
                    "--file-issues",
                ],
                env=env1,
                script=script,
            )
            self.assertEqual(proc1.returncode, 0, proc1.stderr)
            summary1 = json.loads(proc1.stdout)
            self.assertEqual(
                summary1["issues"],
                [{"language": "Go", "action": "created", "url": "https://github.com/example/repo/issues/42"}],
            )
            self.assertNotIn("issues_error", summary1)

            calls1 = gh_calls(log1)
            self.assertEqual(len(calls1), 2)
            self.assertEqual(repo_args(calls1), ["example/repo"] * 2)
            self.assertEqual(calls1[0][:2], ["issue", "list"])
            self.assertEqual(calls1[1][:2], ["issue", "create"])
            title_idx = calls1[1].index("--title") + 1
            body_idx = calls1[1].index("--body") + 1
            self.assertEqual(calls1[1][title_idx], "council garbler: support Go")
            body = calls1[1][body_idx]
            self.assertIn(".go", body)
            self.assertIn("1", body)
            self.assertNotIn("private_client", body)
            self.assertNotIn("secret_billing", body)
            self.assertNotIn("invoice.go", body)

            # Case 2 & 3: existing exact-title issue (both OPEN and CLOSED) -> never creates.
            for state_name in ("OPEN", "CLOSED"):
                bin_dir = root / f"bin_{state_name.lower()}"
                bin_dir.mkdir()
                log_file = root / f"gh_{state_name.lower()}.log"
                canned = [
                    {
                        "number": 7,
                        "title": "council garbler: support Go",
                        "url": "https://github.com/example/repo/issues/7",
                        "state": state_name,
                    }
                ]
                write_fake_gh(bin_dir, log_file, canned, "https://github.com/example/repo/issues/999")
                env = os.environ.copy()
                env["PATH"] = f"{bin_dir}{os.pathsep}{env.get('PATH', '')}"

                proc = run_cli(
                    [
                        "garble",
                        "--candidate",
                        str(repo),
                        "--out",
                        str(root / f"out_{state_name.lower()}"),
                        "--map",
                        str(root / f"map_{state_name.lower()}.json"),
                        "--file-issues",
                    ],
                    env=env,
                    script=script,
                )
                self.assertEqual(proc.returncode, 0, proc.stderr)
                summary = json.loads(proc.stdout)
                self.assertEqual(
                    summary["issues"],
                    [{"language": "Go", "action": "exists", "url": "https://github.com/example/repo/issues/7"}],
                )
                calls = gh_calls(log_file)
                self.assertEqual(len(calls), 1, f"should not call issue create when {state_name} issue exists")
                self.assertEqual(repo_args(calls), ["example/repo"])

            # Case 4: gh failure sets issues_error, warns on stderr, keeps exit code 0.
            bin_fail = root / "bin_fail"
            bin_fail.mkdir()
            fail_gh = bin_fail / "gh"
            fail_gh.write_text("#!/bin/sh\necho 'auth expired' >&2\nexit 1\n", encoding="utf-8")
            fail_gh.chmod(0o755)
            env_fail = os.environ.copy()
            env_fail["PATH"] = f"{bin_fail}{os.pathsep}{env_fail.get('PATH', '')}"

            proc_fail = run_cli(
                [
                    "garble",
                    "--candidate",
                    str(repo),
                    "--out",
                    str(root / "out_fail"),
                    "--map",
                    str(root / "map_fail.json"),
                    "--file-issues",
                ],
                env=env_fail,
                script=script,
            )
            self.assertEqual(proc_fail.returncode, 0)
            self.assertIn("warning:", proc_fail.stderr)
            summary_fail = json.loads(proc_fail.stdout)
            self.assertIn("auth expired", summary_fail.get("issues_error", ""))


class GarbleLeakRegressionTests(unittest.TestCase):
    """A blind reviewer sees only ``--out``: nothing real may be readable there."""

    def test_no_secret_survives_in_any_supported_language(self) -> None:
        secrets = sorted({
            word
            for text in LEAK_FILES.values()
            for word in re.findall(r"[A-Za-z0-9_]*[Zz]qx[A-Za-z0-9_]*", text)
        })
        self.assertGreater(len(secrets), 100)
        with tempfile.TemporaryDirectory() as tmp:
            run = garble_files(Path(tmp), LEAK_FILES, base_files={"unrelated.py": "value = 1\n"})
            self.assertEqual(run.summary["parse_errors"], [])
            self.assertEqual(run.summary["garble_errors"], [])
            self.assertEqual(run.exit_code, 0)

            outputs = output_texts(run.out_dir)
            self.assertIn("change.diff", outputs)
            self.assertEqual(len([name for name in outputs if name.startswith("tree/")]), len(LEAK_FILES))
            for name, text in outputs.items():
                self.assertNotIn(SECRET_MARKER, f"{name}\n{text}".lower(), f"marker left in {name}")
                for secret in secrets:
                    self.assertNotIn(secret, name, f"{secret!r} left in path {name}")
                    self.assertNotIn(secret, text, f"{secret!r} left in {name}")

            for rel_path, original in LEAK_FILES.items():
                garbled = run.read(rel_path)
                language = garble.SUPPORTED_EXTENSIONS[Path(rel_path).suffix]
                self.assertEqual(original.count("\n"), garbled.count("\n"), rel_path)
                self.assertEqual(len(original.splitlines()), len(garbled.splitlines()), rel_path)
                tree = garble.get_parser(language).parse(garbled.encode("utf-8"))
                self.assertFalse(tree.root_node.has_error, f"re-parse failed for {rel_path}")

            # Standard attributes, intrinsic elements and externals stay readable.
            tsx = run.read("zqxui/zqxwidget.tsx")
            self.assertIn("aria-label=", tsx)
            self.assertIn("<a href=", tsx)
            self.assertIn("<form>", tsx)
            self.assertIn('from "react"', tsx)
            self.assertIn("useState(false)", tsx)

    def test_leak_run_is_byte_identical_across_runs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            first = garble_files(Path(tmp) / "a", LEAK_FILES)
            second = garble_files(Path(tmp) / "b", LEAK_FILES)
            self.assertEqual(snapshot_tree(first.out_dir), snapshot_tree(second.out_dir))


class GarbleTypeScriptTests(unittest.TestCase):
    """R1: names inside type-only signatures are definitions like any other."""

    def test_type_only_parameter_names_are_garbled(self) -> None:
        names = [
            "customerName", "invoiceTotal", "lookupKey", "callArgSecret", "ctorArgSecret",
            "firstSecret", "secondSecret", "restSecret", "ctorTypeArg", "sigParam",
            "overloadSecret", "ambientParam",
        ]
        source = (
            "export type Handler = (customerName: string, invoiceTotal: number) => void;\n"
            "export type Ctor = abstract new (ctorTypeArg: string) => Api;\n"
            "export interface Api {\n"
            "  [lookupKey: string]: number;\n"
            "  (callArgSecret: string): void;\n"
            "  new (ctorArgSecret: string): Api;\n"
            "  method(sigParam: number): void;\n"
            "}\n"
            "export type Tup = [firstSecret: string, secondSecret?: number, ...restSecret: boolean[]];\n"
            "export function over(overloadSecret: string): string;\n"
            "export function over(x: any): any { return x; }\n"
            "declare function ambient(ambientParam: string): void;\n"
        )
        with tempfile.TemporaryDirectory() as tmp:
            run = garble_files(Path(tmp), {"types.ts": source})
            garbled = run.read("types.ts")
            self.assertEqual(run.summary["garble_errors"], [])
            for name in names:
                self.assertNotIn(name, garbled)
                self.assertIn(name, run.map_data["identifiers"])
            self.assertIn(f"[{run.token('lookupKey')}: string]: number;", garbled)
            self.assertIn(f"[{run.token('firstSecret')}: string, {run.token('secondSecret')}?: number,", garbled)

    def test_this_parameter_and_builtin_names_stay(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run = garble_files(Path(tmp), {"m.ts": "export function bind(this: Window, string: number): void {}\n"})
            garbled = run.read("m.ts")
            self.assertIn("(this: Window, string: number)", garbled)


class GarbleImportSpecifierTests(unittest.TestCase):
    """R2: baseUrl/paths-style specifiers are project-internal and map to path tokens."""

    FILES: ClassVar[dict[str, str]] = {
        "src/components/Button.tsx": "export const Button = () => null;\n",
        "src/components/Card/index.tsx": "export const Card = () => null;\n",
        "src/app.ts": (
            'import { Button } from "components/Button";\n'
            'import { Card } from "components/Card";\n'
            'import { Card as Card2 } from "components/Card/index";\n'
            'import { Button as Button2 } from "components/Button.tsx";\n'
            'import { useState } from "react";\n'
            'import { parse } from "@scope/pkg";\n'
            'const lazy = () => import("components/Button");\n'
            'const cjs = require("components/Button");\n'
            'jest.mock("components/Button");\n'
            'vi.mock("src/components/Button");\n'
            'export * from "components/Button";\n'
            "console.log(Button, Card, Card2, Button2, useState, parse, lazy, cjs);\n"
        ),
    }

    def test_bare_internal_specifiers_use_path_tokens_everywhere(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run = garble_files(Path(tmp), self.FILES)
            app = run.read("src/app.ts")
            components, button, card = (run.token(name) for name in ("components", "Button", "Card"))
            src = run.token("src")

            self.assertEqual(run.map_data["paths"]["src/components/Button.tsx"], f"{src}/{components}/{button}.tsx")
            for expected in (
                f'from "{components}/{button}";\nimport',
                f'from "{components}/{card}";',
                f'from "{components}/{card}/index";',
                f'from "{components}/{button}.tsx";',
                f'import("{components}/{button}")',
                f'require("{components}/{button}")',
                f'jest.mock("{components}/{button}")',
                f'vi.mock("{src}/{components}/{button}")',
                f'export * from "{components}/{button}"',
            ):
                self.assertIn(expected, app)
            for leaked in ('"components/', "/Button", "/Card"):
                self.assertNotIn(leaked, app)
            # External packages keep their specifiers.
            self.assertIn('from "react"', app)
            self.assertIn('from "@scope/pkg"', app)

    def test_specifier_matching_rules(self) -> None:
        internal = garble.InternalModules([
            "src/components/Button.tsx",
            "packages/shared/utils/index.ts",
            "lib/types.d.ts",
        ])
        for specifier in (
            "components/Button",
            "src/components/Button",
            "components/Button.tsx",
            "components/Button.js",
            "components",
            "components/index",
            "shared/utils",
            "shared/utils/index",
            "utils",
            "packages/shared",
            "lib/types",
            "./anything",
            "@/anything",
        ):
            with self.subTest(specifier=specifier):
                self.assertTrue(internal.is_internal_js_ts_specifier(specifier))
        for specifier in (
            "react",
            "@scope/pkg",
            "node:fs",
            "ponents/Button",
            "mponents",
            "Button/components",
            "src/Button",
            "",
        ):
            with self.subTest(specifier=specifier):
                self.assertFalse(internal.is_internal_js_ts_specifier(specifier))


class GarbleAttributeTests(unittest.TestCase):
    """R3: any ``object.attr = ...`` target defines the attribute name."""

    def test_python_attribute_targets_on_any_object(self) -> None:
        source = (
            "def use(other, items):\n"
            "    other.ledger_secret_attr += 1\n"
            "    other.plain_secret_attr = 0\n"
            "    for obj in items:\n"
            "        obj.new_attr_secret = 2\n"
        )
        with tempfile.TemporaryDirectory() as tmp:
            run = garble_files(Path(tmp), {"m.py": source})
            garbled = run.read("m.py")
            for name in ("ledger_secret_attr", "plain_secret_attr", "new_attr_secret"):
                self.assertNotIn(name, garbled)

    def test_js_ts_attribute_targets_on_any_object(self) -> None:
        source = (
            "const exportsLike = {};\n"
            "exportsLike.assignedSecretProp = 5;\n"
            "exportsLike.ledgerSecretAttr += 1;\n"
            "module.exports.cjsSecretName = 1;\n"
            "module.exports = exportsLike;\n"
        )
        for name in ("m.js", "m.ts"):
            with self.subTest(name=name), tempfile.TemporaryDirectory() as tmp:
                run = garble_files(Path(tmp), {name: source})
                garbled = run.read(name)
                for secret in ("assignedSecretProp", "ledgerSecretAttr", "cjsSecretName"):
                    self.assertNotIn(secret, garbled)
                self.assertIn("module.exports = ", garbled)


class GarbleFormatAndJsxAttributeTests(unittest.TestCase):
    """R4: f-string format specs and ``data-*`` JSX attribute names."""

    def test_format_specifier_text_is_replaced_and_still_parses(self) -> None:
        source = 'x = 1\nw = 2\ny = f"{x:%Y-%m-secretfmt} {x!r:>{w}zzwidth} {x}"\n'
        with tempfile.TemporaryDirectory() as tmp:
            run = garble_files(Path(tmp), {"m.py": source})
            garbled = run.read("m.py")
            self.assertEqual(run.summary["garble_errors"], [])
            for leaked in ("secretfmt", "%Y", "zzwidth"):
                self.assertNotIn(leaked, garbled)
            self.assertIn(f"{{{run.token('x')}:{run.map_data['strings']['%Y-%m-secretfmt']}}}", garbled)
            self.assertIn("!r:", garbled)
            self.assertIn(f"{{{run.token('w')}}}", garbled)

    def test_data_attribute_suffix_is_tokenised_and_aria_kept(self) -> None:
        source = 'export const view = <div data-customer-ssn="1" data-x aria-customer-note="n" data-="e" />;\n'
        with tempfile.TemporaryDirectory() as tmp:
            run = garble_files(Path(tmp), {"v.jsx": source})
            garbled = run.read("v.jsx")
            suffix_token = run.map_data["strings"]["customer-ssn"]
            self.assertIn(f"data-{suffix_token}=", garbled)
            self.assertNotIn("customer-ssn", garbled)
            self.assertIn("aria-customer-note=", garbled)
            revealed = garble.reveal_text(garbled, run.map_data)
            self.assertIn("data-customer-ssn=", revealed)


class GarbleJsxIntrinsicTests(unittest.TestCase):
    """R10: lowercase non-member JSX tags are HTML elements, never project names."""

    def test_intrinsic_tags_survive_same_named_project_identifiers(self) -> None:
        source = (
            "const a = 1;\nconst form = 2;\nconst button = 3;\nconst input = 4;\nconst Panel = 5;\n"
            "export const view = <form><a href={a}>{form}</a><button>{button}</button>"
            "<input value={input} /><Panel /></form>;\n"
        )
        with tempfile.TemporaryDirectory() as tmp:
            run = garble_files(Path(tmp), {"v.jsx": source})
            garbled = run.read("v.jsx")
            self.assertIn("<form>", garbled)
            self.assertIn("</form>", garbled)
            self.assertIn("<a href={", garbled)
            self.assertIn("</a>", garbled)
            self.assertIn("<button>", garbled)
            self.assertIn("<input value={", garbled)
            self.assertIn(f"<{run.token('Panel')} />", garbled)
            self.assertIn(f"{{{run.token('form')}}}", garbled)


class GarbleRobustnessTests(unittest.TestCase):
    """R5: deep ASTs and one failing file must not take the run down."""

    def test_deeply_nested_expression_chain_is_garbled(self) -> None:
        terms = 1500
        sources = {
            "big.py": "total = " + " + ".join(["1"] * terms) + "\n",
            "big.ts": "export const total = " + " + ".join(["1"] * terms) + ";\n",
            "big.rs": "pub fn total() -> i64 { " + " + ".join(["1"] * terms) + " }\n",
        }
        self.assertGreater(terms, sys.getrecursionlimit())
        with tempfile.TemporaryDirectory() as tmp:
            run = garble_files(Path(tmp), sources)
            self.assertEqual(run.summary["garble_errors"], [])
            self.assertEqual(run.exit_code, 0)
            self.assertNotIn("total", run.read("big.py"))
            self.assertEqual(run.read("big.py").count("+ 1"), terms - 1)

    def test_one_failing_file_is_listed_and_the_rest_are_written(self) -> None:
        real_garble_source = garble._garble_source

        def flaky(parsed: garble.ParsedFile, symbols: garble.SymbolTable, internal: garble.InternalModules):
            if parsed.source.rel_path == "boom.py":
                raise RuntimeError("synthetic failure")
            return real_garble_source(parsed, symbols, internal)

        files = {"boom.py": "boom_secret = 1\n", "fine.py": "fine_secret = 2\n"}
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(garble, "_garble_source", flaky):
            run = garble_files(Path(tmp), files, base_files={"boom.py": "boom_secret = 0\n"})
            self.assertEqual(run.summary["garble_errors"], ["boom.py"])
            self.assertEqual(run.exit_code, 1)
            outputs = output_texts(run.out_dir)
            self.assertEqual(len([name for name in outputs if name.startswith("tree/")]), 1)
            self.assertNotIn("boom_secret", "".join(outputs.values()))
            self.assertNotIn("fine_secret", run.read("fine.py"))
            self.assertNotIn(run.map_data["paths"]["boom.py"], outputs["change.diff"])

    def test_scan_failure_in_one_file_is_isolated(self) -> None:
        files = {"boom.py": "boom_secret = 1\n", "fine.py": "fine_secret = 2\n"}

        def explode(parsed: garble.ParsedFile, internal: garble.InternalModules) -> list[garble.Definition]:
            if parsed.source.rel_path == "boom.py":
                raise RuntimeError("synthetic failure")
            return []

        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(garble, "collect_definitions", explode):
            run = garble_files(Path(tmp), files)
            self.assertEqual(run.summary["garble_errors"], ["boom.py"])
            self.assertEqual(run.exit_code, 1)
            written = [name for name in output_texts(run.out_dir) if name.startswith("tree/")]
            self.assertEqual(written, [f"tree/{run.map_data['paths']['fine.py']}"])


class GarbleRevealTests(unittest.TestCase):
    """R9: revealed strings are inserted as written, with newlines shown as ``\\n``."""

    def test_reveal_keeps_backslashes_and_flattens_newlines(self) -> None:
        original = 'C:\\path\\to "quoted"\nsecond\tline \\n literal'
        map_data = {"identifiers": {}, "strings": {original: "s7"}}
        revealed = garble.reveal_text("finding: s7 here", map_data)
        self.assertEqual(revealed, 'finding: C:\\path\\to "quoted"\\nsecond\tline \\n literal here')
        self.assertNotIn("\n", revealed)

    def test_reveal_leaves_unknown_tokens(self) -> None:
        self.assertEqual(garble.reveal_text("s9 v9", {"identifiers": {}, "strings": {}}), "s9 v9")


class GarbleModelTests(unittest.TestCase):
    """R11: derived state stays derived."""

    def test_tree_scan_does_not_store_unsupported_summary(self) -> None:
        fields = {field.name for field in dataclasses.fields(garble.TreeScan)}
        self.assertIn("unsupported_files", fields)
        self.assertNotIn("unsupported", fields)


class GarbleRoundTwoTests(unittest.TestCase):
    """Round-2 review findings: R9 and R14-R17."""

    def test_non_js_stems_are_not_js_specifiers(self) -> None:
        internal = garble.InternalModules(["src/myapp/os.py", "crates/fs/src/lib.rs", "web/utils/index.ts"])
        self.assertFalse(internal.is_internal_js_ts_specifier("os"))
        self.assertFalse(internal.is_internal_js_ts_specifier("fs"))
        self.assertTrue(internal.is_internal_js_ts_specifier("utils"))

    def test_query_hash_and_trailing_slash_do_not_defeat_suffix_match(self) -> None:
        internal = garble.InternalModules(["src/components/SecretButton.tsx"])
        for specifier in (
            "components/SecretButton?raw",
            "components/SecretButton#id",
            "components/SecretButton/",
            "components/SecretButton.tsx?raw",
        ):
            with self.subTest(specifier=specifier):
                self.assertTrue(internal.is_internal_js_ts_specifier(specifier))
        with tempfile.TemporaryDirectory() as tmp:
            run = garble_files(Path(tmp), {
                "src/components/SecretButton.tsx": "export const Btn = 1;\n",
                "src/app.ts": 'import { Btn } from "components/SecretButton?raw";\nexport const x = Btn;\n',
            })
            self.assertNotIn("SecretButton", "".join(output_texts(run.out_dir).values()))

    def test_module_exports_property_is_never_renamed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run = garble_files(Path(tmp), {
                "a.js": "const mock = {};\nmock.exports = { one: 1 };\nmodule.exports = mock;\n",
                "b.js": "const holder = { exports: 2 };\nmodule.exports = holder;\n",
            })
            self.assertIn("module.exports = ", run.read("a.js"))
            self.assertIn("module.exports = ", run.read("b.js"))

    def test_data_attribute_prefix_is_case_insensitive(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run = garble_files(Path(tmp), {
                "c.tsx": 'export const C = () => <div DATA-zqxsecret="1" data-zqxother="2" />;\n',
            })
            text = run.read("c.tsx")
            self.assertNotIn("zqxsecret", text)
            self.assertNotIn("zqxother", text)
            self.assertIn("DATA-", text)

    def test_reveal_flattens_every_line_break(self) -> None:
        map_data = {"identifiers": {}, "strings": {"alpha\r\nbeta\rgamma delta\n": "s1"}}
        revealed = garble.reveal_text("f.py:1 s1 end", map_data)
        self.assertEqual(revealed.splitlines(), [revealed])
        self.assertEqual(revealed, "f.py:1 alpha\\nbeta\\ngamma\\ndelta\\n end")


if __name__ == "__main__":
    unittest.main()

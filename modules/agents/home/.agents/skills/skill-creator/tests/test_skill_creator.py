"""Unit and CLI tests for skill-creator helper scripts."""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SKILL_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = SKILL_ROOT / "scripts"
EVAL_VIEWER = SKILL_ROOT / "eval-viewer"


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


quick_validate = load_module("skill_creator_quick_validate", SCRIPTS / "quick_validate.py")
generate_review = load_module("skill_creator_generate_review", EVAL_VIEWER / "generate_review.py")


class QuickValidateTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def _make_skill(self, rel: str, content: str) -> Path:
        skill = self.root / rel
        skill.mkdir(parents=True, exist_ok=True)
        (skill / "SKILL.md").write_text(content, encoding="utf-8")
        return skill

    def test_valid_skill_passes_including_todo_inside_nested_fences(self):
        skill = self._make_skill(
            "tests/demo-skill",  # ancestor directory literally named 'tests' must not break relative_to
            "---\n"
            "name: demo-skill\n"
            "description: When the user asks to run a demo check.\n"
            "license: MIT\n"
            "metadata:\n"
            "  source: local\n"
            "---\n\n"
            "# Demo\n\n"
            "````markdown\n"
            "```text\n"
            "[TODO: allowed inside fenced code block]\n"
            "```\n"
            "````\n",
        )
        # Nested SKILL.md inside ignored subdirs (evals/tests) is allowed.
        (skill / "evals/case-1").mkdir(parents=True)
        (skill / "evals/case-1/SKILL.md").write_text("fixture", encoding="utf-8")
        valid, msg = quick_validate.validate_skill(skill)
        self.assertTrue(valid, msg)

    def test_rejects_disable_model_invocation_with_explicit_guidance(self):
        skill = self._make_skill(
            "bad-key",
            "---\nname: bad-key\ndescription: When triggered.\ndisable-model-invocation: true\n---\n\nBody\n",
        )
        valid, msg = quick_validate.validate_skill(skill)
        self.assertFalse(valid)
        self.assertIn("disable-model-invocation", msg)
        self.assertIn("Codex", msg)

    def test_rejects_nested_skill_md_outside_ignored_dirs(self):
        skill = self._make_skill(
            "nested-skill",
            "---\nname: nested-skill\ndescription: When triggered.\n---\n\nBody\n",
        )
        (skill / "references/extra").mkdir(parents=True)
        (skill / "references/extra/SKILL.md").write_text("oops", encoding="utf-8")
        valid, msg = quick_validate.validate_skill(skill)
        self.assertFalse(valid)
        self.assertIn("references/extra/SKILL.md", msg)

    def test_rejects_unfinished_todos_and_empty_fields(self):
        cases = [
            ("---\nname: a\ndescription: \"[TODO: fill in]\"\n---\nBody\n", "TODO"),
            ("---\nname: a\ndescription: When triggered.\n---\n\n[TODO: write body]\n", "TODO"),
            ("---\nname: \"\"\ndescription: When triggered.\n---\nBody\n", "empty"),
            ("---\nname: bad_name\ndescription: When triggered.\n---\nBody\n", "hyphen-case"),
        ]
        for idx, (text, needle) in enumerate(cases):
            with self.subTest(idx=idx):
                skill = self._make_skill(f"case-{idx}", text)
                valid, msg = quick_validate.validate_skill(skill)
                self.assertFalse(valid)
                self.assertIn(needle, msg)

    def test_cli_validates_skill_creator_itself_under_isolated_python(self):
        proc = subprocess.run(
            [sys.executable, "-I", "-B", str(SCRIPTS / "quick_validate.py"), str(SKILL_ROOT)],
            capture_output=True, text=True, check=False,
        )
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)


class InitAndOpenAiYamlTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def test_init_skill_direct_isolated_omits_openai_yaml_by_default(self):
        proc = subprocess.run(
            [
                sys.executable, "-I", "-B", str(SCRIPTS / "init_skill.py"),
                "My Sample Skill", "--path", str(self.root), "--resources", "scripts,references", "--examples",
            ],
            capture_output=True, text=True, check=False,
        )
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        created = self.root / "my-sample-skill"
        self.assertTrue((created / "SKILL.md").is_file())
        self.assertFalse((created / "agents/openai.yaml").exists())
        self.assertTrue((created / "scripts/example.py").is_file())
        self.assertTrue((created / "references/reference.md").is_file())
        # Placeholder TODO must fail validation until edited.
        valid, msg = quick_validate.validate_skill(created)
        self.assertFalse(valid)
        self.assertIn("TODO", msg)

    def test_init_skill_with_openai_yaml_via_module_invocation_and_direct_generator(self):
        proc = subprocess.run(
            [
                sys.executable, "-I", "-B", str(SCRIPTS / "init_skill.py"),
                "codex-ui-skill", "--path", str(self.root), "--openai-yaml",
                "--interface", "display_name=Codex UI Skill",
            ],
            capture_output=True, text=True, check=False,
        )
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        yaml_path = self.root / "codex-ui-skill/agents/openai.yaml"
        self.assertTrue(yaml_path.is_file())
        text = yaml_path.read_text(encoding="utf-8")
        self.assertIn('display_name: "Codex UI Skill"', text)

        # Also test `python3 -B -m scripts.init_skill` from SKILL_ROOT.
        proc_mod = subprocess.run(
            [
                sys.executable, "-B", "-m", "scripts.init_skill",
                "module-skill", "--path", str(self.root), "--openai-yaml",
            ],
            cwd=str(SKILL_ROOT), capture_output=True, text=True, check=False,
        )
        self.assertEqual(proc_mod.returncode, 0, proc_mod.stdout + proc_mod.stderr)
        self.assertTrue((self.root / "module-skill/agents/openai.yaml").is_file())

        # Standalone generate_openai_yaml.py CLI
        proc_gen = subprocess.run(
            [
                sys.executable, "-I", "-B", str(SCRIPTS / "generate_openai_yaml.py"),
                str(self.root / "module-skill"),
                "--interface", "short_description=Custom description with sufficient length",
            ],
            capture_output=True, text=True, check=False,
        )
        self.assertEqual(proc_gen.returncode, 0, proc_gen.stdout + proc_gen.stderr)
        self.assertIn("Custom description with sufficient length", (self.root / "module-skill/agents/openai.yaml").read_text(encoding="utf-8"))


class BenchmarkAndViewerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.ws = Path(self.tmp.name)

    def _populate_workspace(self) -> Path:
        iter_dir = self.ws / "iteration-1"
        for cfg, rate, secs, tokens in (("with_skill", 1.0, 12.5, 1000), ("without_skill", 0.5, 10.0, 800)):
            run_dir = iter_dir / "eval-0" / cfg
            (run_dir / "outputs").mkdir(parents=True)
            (run_dir / "outputs/result.txt").write_text("payload </script><!-- xss --> ok\n", encoding="utf-8")
            (run_dir / "grading.json").write_text(json.dumps({
                "expectations": [{"text": "check 1", "passed": rate == 1.0, "evidence": "seen"}],
                "summary": {"passed": int(rate * 2), "failed": 2 - int(rate * 2), "total": 2, "pass_rate": rate},
                "execution_metrics": {"total_tool_calls": 3, "errors_encountered": 0},
            }), encoding="utf-8")
            (run_dir / "timing.json").write_text(json.dumps({
                "total_duration_seconds": secs, "total_tokens": tokens,
            }), encoding="utf-8")
        (iter_dir / "eval-0/eval_metadata.json").write_text(
            json.dumps({"eval_id": 0, "eval_name": "smoke", "prompt": "Do the smoke task"}),
            encoding="utf-8",
        )
        return iter_dir

    def test_aggregate_benchmark_and_static_review_viewer_are_safe_and_headless(self):
        iter_dir = self._populate_workspace()
        proc_agg = subprocess.run(
            [sys.executable, "-I", "-B", str(SCRIPTS / "aggregate_benchmark.py"), str(iter_dir), "--skill-name", "demo"],
            capture_output=True, text=True, check=False,
        )
        self.assertEqual(proc_agg.returncode, 0, proc_agg.stdout + proc_agg.stderr)
        bench = json.loads((iter_dir / "benchmark.json").read_text(encoding="utf-8"))
        self.assertEqual(bench["run_summary"]["delta"]["pass_rate"], "+0.50")
        self.assertTrue((iter_dir / "benchmark.md").is_file())

        # Default mode writes static HTML to <workspace>/review.html and exits 0 without opening a browser.
        proc_rev = subprocess.run(
            [
                sys.executable, "-I", "-B", str(EVAL_VIEWER / "generate_review.py"),
                str(iter_dir), "--skill-name", "demo", "--benchmark", str(iter_dir / "benchmark.json"),
            ],
            capture_output=True, text=True, check=False,
        )
        self.assertEqual(proc_rev.returncode, 0, proc_rev.stdout + proc_rev.stderr)
        review_html = (iter_dir / "review.html").read_text(encoding="utf-8")
        self.assertIn("const EMBEDDED_DATA =", review_html)
        self.assertIn("\\u003c/script\\u003e\\u003c!-- xss --\\u003e", review_html)
        self.assertNotIn("payload </script>", review_html)

        # Ensure browser auto-launch, subprocess, and port-killing helpers are absent via AST.
        import ast
        tree = ast.parse((EVAL_VIEWER / "generate_review.py").read_text(encoding="utf-8"))
        imported = {
            alias.name
            for node in ast.walk(tree)
            if isinstance(node, ast.Import)
            for alias in node.names
        } | {
            node.module
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.module
        }
        self.assertNotIn("webbrowser", imported)
        self.assertNotIn("subprocess", imported)
        func_names = {node.name for node in ast.walk(tree) if isinstance(node, ast.FunctionDef)}
        self.assertNotIn("_kill_port", func_names)


if __name__ == "__main__":
    unittest.main()

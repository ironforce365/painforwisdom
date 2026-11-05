"""The console's decision tree is enforceable (spec 2026-11-05-console-end-to-end sections 2 and 5).

`pipeline/ui/e2e/decision_tree.json` and the spec's tree hold the same ids, and each id has a `[DT-<id>]` tag at each
level it requires. Stdlib + pytest only, no server: this file is the `decision tree` CI job.

Tags count where a test actually runs:
- unit: on a pytest test (decorators to last line) that is not marked integration;
- integration: on a pytest test marked integration, by the file's `pytestmark` or by its own decorator or class's;
- browser: in the title of a `test(...)` under `pipeline/ui/e2e/tests` or `pipeline/ui/e2e/uat`.
A skipped test, a comment, a describe title or `real.spec.js` (not in the normal Playwright run) count for nothing.
"""
from __future__ import annotations

import ast
import json
import re
import shutil
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SPEC = "docs/superpowers/specs/2026-11-05-console-end-to-end.md"
TREE = "pipeline/ui/e2e/decision_tree.json"
CI = ".github/workflows/ci.yml"
THIS_FILE = "tests/test_decision_tree.py"
BROWSER_ROOTS = ("pipeline/ui/e2e/tests", "pipeline/ui/e2e/uat")
NOT_IN_THE_BROWSER_RUN = {"real.spec.js"}  # testIgnore in playwright.config.js
LEVELS = ("unit", "integration", "browser")

TAG = re.compile(r"\[DT-([^\]\s]*)\]")
SPEC_ID = re.compile(r"\b([SRAEFDPN]\d+)\b")
JS_TEST_TITLE = re.compile(
    r"""^[ \t]*test\(\s*(?:'(?:[^'\\\n]|\\.)*'|"(?:[^"\\\n]|\\.)*"|`(?:[^`\\]|\\.)*`)""", re.M)


def spec_ids(root: Path) -> set[str]:
    fence = re.search(r"^## 2\. Decision tree.*?^```\n(.*?)^```", (root / SPEC).read_text(), re.M | re.S)
    return set(SPEC_ID.findall(fence.group(1))) if fence else set()


def _has_mark(decorators: list[ast.expr], mark: str) -> bool:
    return any(isinstance(n, ast.Attribute) and n.attr == mark for d in decorators for n in ast.walk(d))


def _file_is_integration(tree: ast.Module) -> bool:
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "pytestmark" for t in node.targets):
            return _has_mark([node.value], "integration")
    return False


def _tests_in(tree: ast.Module):
    """(function node, its decorators and its class's)"""
    functions = (ast.FunctionDef, ast.AsyncFunctionDef)
    for node in tree.body:
        if isinstance(node, functions) and node.name.startswith("test"):
            yield node, node.decorator_list
        elif isinstance(node, ast.ClassDef) and node.name.startswith("Test"):
            for sub in node.body:
                if isinstance(sub, functions) and sub.name.startswith("test"):
                    yield sub, node.decorator_list + sub.decorator_list


def _pytest_files(root: Path):
    for name in ("test_*.py", "unit_*.py"):
        for path in sorted((root / "tests").rglob(name)):
            if path.relative_to(root).as_posix() != THIS_FILE:
                yield path


def _browser_files(root: Path):
    for sub in BROWSER_ROOTS:
        for path in sorted((root / sub).rglob("*.js")) if (root / sub).is_dir() else ():
            if path.name not in NOT_IN_THE_BROWSER_RUN:
                yield path


def collect(root: Path) -> tuple[dict[str, set[str]], list[str]]:
    """Ids tagged at each level, and each tag sitting where it proves nothing."""
    found: dict[str, set[str]] = {level: set() for level in LEVELS}
    misplaced: list[str] = []
    for path in _pytest_files(root):
        text = path.read_text()
        if not TAG.search(text):
            continue
        tree = ast.parse(text)
        whole_file = _file_is_integration(tree)
        spans = [(min([d.lineno for d in fn.decorator_list] + [fn.lineno]), fn.end_lineno, marks)
                 for fn, marks in _tests_in(tree)]
        for lineno, line in enumerate(text.splitlines(), 1):
            for tag in TAG.findall(line):
                span = next((s for s in spans if s[0] <= lineno <= s[1]), None)
                if span is None:
                    misplaced.append(f"{path.relative_to(root)}:{lineno} [DT-{tag}] is not on a pytest test")
                elif _has_mark(span[2], "skip"):
                    misplaced.append(f"{path.relative_to(root)}:{lineno} [DT-{tag}] is on a skipped test")
                else:
                    found["integration" if whole_file or _has_mark(span[2], "integration") else "unit"].add(tag)
    for path in _browser_files(root):
        text = path.read_text()
        titles = [m.span() for m in JS_TEST_TITLE.finditer(text)]
        for tag in TAG.finditer(text):
            if any(a <= tag.start() < b for a, b in titles):
                found["browser"].add(tag.group(1))
            else:
                line = text.count("\n", 0, tag.start()) + 1
                misplaced.append(f"{path.relative_to(root)}:{line} [DT-{tag.group(1)}] is not in a test() title")
    return found, misplaced


def problems(root: Path) -> list[str]:
    out: list[str] = []
    tree = json.loads((root / TREE).read_text())
    ids, specified = set(tree), spec_ids(root)
    if not specified:
        out.append(f"no ids found in the tree of {SPEC}")
    out += [f"{i}: in the spec's tree, not in {TREE}" for i in sorted(specified - ids)]
    out += [f"{i}: in {TREE}, not in the spec's tree" for i in sorted(ids - specified)]
    found, misplaced = collect(root)
    out += misplaced
    for level in LEVELS:
        out += [f"[DT-{i}]: unknown id, tagged at {level}" for i in sorted(found[level] - ids)]
    for i, leaf in sorted(tree.items()):
        out += [f"{i}: `{key}` is empty" for key in ("title", "user_action") if not leaf.get(key)]
        levels = leaf.get("levels", {})
        for level in LEVELS:
            need = levels.get(level)
            tagged = i in found[level]
            if level == "browser" and need is not True:
                out.append(f"{i}: browser is never exempt")
            elif need is True:
                if not tagged:
                    out.append(f"{i}: no [DT-{i}] at {level}")
            elif not (isinstance(need, str) and need.strip()):
                out.append(f"{i}: {level} must be true or the reason for an exemption")
            elif tagged:
                out.append(f"{i}: exempt at {level} but tagged there; drop the exemption")
    return out


def test_every_leaf_is_tagged_at_every_required_level():
    assert problems(REPO) == []


# The check bites: each case breaks one thing in a tree that otherwise passes.

GOOD_UNIT = "def test_u():  # [DT-A1]\n    pass\n"
GOOD_INTEGRATION = "import pytest\npytestmark = pytest.mark.integration\ndef test_i():  # [DT-A1]\n    pass\n"
GOOD_BROWSER = "test('[DT-A1] approve', async () => {});\n"
ALL_LEVELS = {"unit": True, "integration": True, "browser": True}


def _tree(tmp_path: Path, unit=GOOD_UNIT, integration=GOOD_INTEGRATION, browser=GOOD_BROWSER, levels=ALL_LEVELS,
          spec_leaves="├─ A1 approve\n") -> Path:
    (tmp_path / SPEC).parent.mkdir(parents=True)
    (tmp_path / SPEC).write_text(f"# s\n\n## 2. Decision tree\n\n```\n{spec_leaves}```\n\n## 3. Next\n\n```\nZ9\n```\n")
    (tmp_path / TREE).parent.mkdir(parents=True)
    (tmp_path / TREE).write_text(json.dumps({"A1": {"title": "t", "user_action": "a", "levels": levels}}))
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests/test_a.py").write_text(unit)
    (tmp_path / "tests/test_b.py").write_text(integration)
    (tmp_path / BROWSER_ROOTS[0]).mkdir(parents=True)
    (tmp_path / BROWSER_ROOTS[0] / "a.spec.js").write_text(browser)
    return tmp_path


def test_a_complete_tree_passes(tmp_path):
    assert problems(_tree(tmp_path)) == []


def test_spec_ids_come_from_section_2_only():
    assert {"S1", "E6", "P11", "N5"} <= spec_ids(REPO)
    assert "C11" not in spec_ids(REPO)


def test_a_missing_unit_tag_fails(tmp_path):
    assert problems(_tree(tmp_path, unit="def test_u():\n    pass\n")) == ["A1: no [DT-A1] at unit"]


def test_an_integration_tag_does_not_stand_in_for_a_unit_one(tmp_path):
    assert problems(_tree(tmp_path, unit=GOOD_INTEGRATION)) == ["A1: no [DT-A1] at unit"]


def test_a_missing_integration_tag_fails(tmp_path):
    root = _tree(tmp_path, integration="def test_i():  # [DT-A1]\n    pass\n")
    assert problems(root) == ["A1: no [DT-A1] at integration"]


def test_the_decorator_marks_one_test_in_a_mixed_file(tmp_path):
    mixed = ("import pytest\ndef test_u():  # [DT-A1]\n    pass\n"
             "@pytest.mark.integration\ndef test_i():  # [DT-A1]\n    pass\n")
    assert problems(_tree(tmp_path, unit=mixed, integration="")) == []


def test_a_class_decorator_marks_its_tests(tmp_path):
    cls = "import pytest\n@pytest.mark.integration\nclass TestI:\n    def test_i(self):  # [DT-A1]\n        pass\n"
    assert problems(_tree(tmp_path, integration=cls)) == []


def test_a_missing_browser_tag_fails(tmp_path):
    assert problems(_tree(tmp_path, browser="test('approve', async () => {});\n")) == ["A1: no [DT-A1] at browser"]


def test_a_nested_uat_spec_counts(tmp_path):
    root = _tree(tmp_path, browser="")
    (root / BROWSER_ROOTS[0] / "uat").mkdir()
    (root / BROWSER_ROOTS[0] / "uat/flow.spec.js").write_text("test(`[DT-A1] flow at ${w}`, async () => {});\n")
    assert problems(root) == []


def test_the_uat_folder_counts(tmp_path):
    root = _tree(tmp_path, browser="")
    (root / BROWSER_ROOTS[1]).mkdir()
    (root / BROWSER_ROOTS[1] / "flow.spec.js").write_text("test('[DT-A1] flow', async () => {});\n")
    assert problems(root) == []


def test_an_unknown_id_fails(tmp_path):
    root = _tree(tmp_path, unit=GOOD_UNIT + "def test_x():  # [DT-Z7]\n    pass\n")
    assert problems(root) == ["[DT-Z7]: unknown id, tagged at unit"]


def test_an_unknown_browser_id_fails(tmp_path):
    root = _tree(tmp_path, browser=GOOD_BROWSER + "test('[DT-A99] nope', async () => {});\n")
    assert problems(root) == ["[DT-A99]: unknown id, tagged at browser"]


def test_a_tag_on_no_test_does_not_count(tmp_path):
    root = _tree(tmp_path, unit="# [DT-A1]\ndef helper():\n    pass\n")
    assert problems(root) == ["tests/test_a.py:1 [DT-A1] is not on a pytest test", "A1: no [DT-A1] at unit"]


def test_a_skipped_pytest_test_does_not_count(tmp_path):
    root = _tree(tmp_path, unit="import pytest\n@pytest.mark.skip\ndef test_u():  # [DT-A1]\n    pass\n")
    assert problems(root) == ["tests/test_a.py:3 [DT-A1] is on a skipped test", "A1: no [DT-A1] at unit"]


def test_a_browser_tag_counts_only_in_a_runnable_test_title(tmp_path):
    for browser in ("// test('[DT-A1] off', async () => {});\n", "test.skip('[DT-A1] off', async () => {});\n",
                    "test.describe('[DT-A1] group', () => {});\n", "test('x', () => { /* [DT-A1] */ });\n"):
        shutil.rmtree(tmp_path)
        tmp_path.mkdir()
        got = problems(_tree(tmp_path, browser=browser))
        assert "A1: no [DT-A1] at browser" in got and any("not in a test() title" in p for p in got), browser


def test_real_spec_is_not_in_the_browser_run(tmp_path):
    root = _tree(tmp_path, browser="")
    (root / BROWSER_ROOTS[0] / "real.spec.js").write_text(GOOD_BROWSER)
    assert problems(root) == ["A1: no [DT-A1] at browser"]


def test_an_id_only_in_the_spec_fails(tmp_path):
    root = _tree(tmp_path, spec_leaves="├─ A1 approve\n├─ A2 reject\n")
    assert problems(root) == [f"A2: in the spec's tree, not in {TREE}"]


def test_an_id_only_in_the_json_fails(tmp_path):
    root = _tree(tmp_path, spec_leaves="├─ A2 reject\n")
    assert f"A1: in {TREE}, not in the spec's tree" in problems(root)


def test_a_spec_without_the_tree_fails(tmp_path):
    root = _tree(tmp_path)
    (root / SPEC).write_text("# nothing here\n")
    assert f"no ids found in the tree of {SPEC}" in problems(root)


def test_an_exemption_needs_a_reason(tmp_path):
    root = _tree(tmp_path, unit="", levels={**ALL_LEVELS, "unit": "  "})
    assert problems(root) == ["A1: unit must be true or the reason for an exemption"]


def test_a_reasoned_exemption_passes(tmp_path):
    assert problems(_tree(tmp_path, unit="", levels={**ALL_LEVELS, "unit": "purely visual"})) == []


def test_an_exempt_level_with_a_tag_fails(tmp_path):
    root = _tree(tmp_path, levels={**ALL_LEVELS, "unit": "purely visual"})
    assert problems(root) == ["A1: exempt at unit but tagged there; drop the exemption"]


def test_browser_is_never_exempt(tmp_path):
    root = _tree(tmp_path, levels={**ALL_LEVELS, "browser": "no browser"})
    assert problems(root) == ["A1: browser is never exempt"]


def test_tags_in_this_file_prove_nothing(tmp_path):
    root = _tree(tmp_path, unit="")
    (root / THIS_FILE).write_text(GOOD_UNIT)
    assert problems(root) == ["A1: no [DT-A1] at unit"]


# The CI job stays cheap and leaves the pipeline jobs alone.


def _ci_job(name: str) -> str:
    match = re.search(rf"^  {name}:\n(.*?)(?=^  [\w-]+:\n|\Z)", (REPO / CI).read_text(), re.M | re.S)
    assert match, name
    return match.group(1)


def test_the_ci_job_needs_only_pytest():
    job = _ci_job("decision-tree")
    assert re.search(r"^\s+- run: pip install pytest$", job, re.M)
    assert "requirements" not in job
    assert "python -m pytest tests/test_decision_tree.py" in job


def test_the_pipeline_jobs_leave_this_file_to_its_own_job():
    assert _ci_job("pipeline").count("--ignore=tests/test_decision_tree.py") == 2

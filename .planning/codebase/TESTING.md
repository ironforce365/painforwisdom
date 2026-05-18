# Testing Patterns

**Analysis Date:** 2026-05-18

The project mixes two test styles:

1. **Python unit tests in `tests/*.py`** — stdlib `unittest`, no pytest,
   no coverage tool, no CI. Manual local run.
2. **Sandbox smoke harness** (`tests/smoke_pipeline.sh`) — drives the
   full LangGraph pipeline against checked-in transcript fixtures
   against a parallel Notion workspace and parallel vault worktree.

Together they validate: pure helpers (unit), node-level logic (`httpx`
mocking + `unittest.mock.patch`), and end-to-end pipeline behavior
(smoke).

---

## Test Framework

**Runner:** Python stdlib `unittest`. Every test file ends with:

```python
if __name__ == "__main__":
    unittest.main()      # or: unittest.main(verbosity=2)
```

Run commands per test file (header comments are explicit):

```bash
# Per OPERATIONS conventions:
conda activate painforwisdom-poc
cd ~/workspace/painforwisdom/painforwisdom

# Single test file (module form)
python -m unittest tests.test_contracts
python -m unittest tests.test_research_node
python -m unittest tests.test_themes_db
python -m unittest tests.test_wordpress_client
python -m unittest tests.test_local_books
python -m unittest tests.test_banned_sources
python -m unittest tests.test_blog_context_vault
python -m unittest tests.test_image_extractor
python -m unittest tests.test_summarize_daily_fetch_resilience
python -m unittest tests.test_migration_idempotency

# Or run a file directly (each carries its own sys.path bootstrap):
python tests/test_contracts.py

# Discovery from project root:
python -m unittest discover -s tests
```

**No pytest, no config file.** There is no `pytest.ini`,
`pyproject.toml`, `setup.cfg`, `conftest.py`, `.pre-commit-config.yaml`,
or `.github/workflows/` directory in the repo. The conda env
`painforwisdom-poc` carries the runtime deps; tests rely on stdlib +
the same runtime deps (`httpx`, `dotenv` optional).

**`sys.path` bootstrap.** Every test inserts the project root onto
`sys.path` itself rather than relying on a package install:

```python
# tests/test_contracts.py:13
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pipeline.contracts import (  # noqa: E402
    NODE_INPUTS,
    InputContractError,
    assert_inputs,
)
```

**Assertion library:** Stdlib `unittest.TestCase` assertions only —
`assertEqual`, `assertTrue`, `assertIn`, `assertRaises`,
`assertGreater`, `assertIsNone`, `subTest`, etc.

---

## Test File Layout

**Location:** Flat under `tests/` (not co-located). Fixtures live in
`tests/fixtures/`.

```
tests/
├── fixtures/
│   ├── README.md                                  # Fixture matrix
│   ├── transcript_2026-04-14.txt                  # Strong + new theme HITL
│   ├── transcript_2026-04-15-flagged.txt          # Flagged auto-attach
│   ├── transcript_2026-04-16-weak.txt             # Weak → still PROCEED
│   └── transcript_2026-04-17-strong-existing-themes.txt  # Fastest happy path
├── sandbox_reset.sh                               # Vault revert + Notion archive
├── smoke_pipeline.sh                              # E2E driver
├── test_banned_sources.py
├── test_blog_context_vault.py
├── test_contracts.py
├── test_image_extractor.py
├── test_local_books.py
├── test_migration_idempotency.py
├── test_research_node.py
├── test_summarize_daily_fetch_resilience.py
├── test_themes_db.py
└── test_wordpress_client.py
```

**Naming:**
- Test files: `test_<module-under-test>.py`. Each maps 1:1 to a
  `pipeline/*.py` or `pipeline/<sub>/*.py` module.
- Test classes: `<Subject>Test` or `<BehaviorScope>Test(s)` —
  examples: `ContractsTest`, `ClassifyUrlTest`, `VerifyRowsTest`,
  `FirstFiftyWordsTest`, `LocalBooksTest`, `DenyListTests`,
  `MigrationIdempotencyTest`.
- Test methods: `test_<scenario_under_test>` — descriptive snake_case,
  often a full sentence (`test_curator_yes_with_banned_url_gets_overridden`,
  `test_empty_string_counts_as_missing`,
  `test_local_book_match_overrides_web_url`).

**File header convention.** Every test file starts with a docstring
that includes the run command:

```python
# tests/test_contracts.py:1-6
"""Per-node input contract tests (stdlib unittest, no pytest dep).

Run:  python -m unittest tests.test_contracts
or:   python tests/test_contracts.py
"""
```

---

## Test Structure

**One class per scenario family, multiple methods per class.**
Example pattern from `tests/test_research_node.py`:

```python
class ClassifyUrlTest(unittest.TestCase):
    def test_reachable_url_passes(self):
        ...
    def test_404_marked_unreachable(self):
        ...
    def test_paywall_phrase_in_body_rejected(self):
        ...

class VerifyRowsTest(unittest.TestCase):
    def test_curator_yes_with_banned_url_gets_overridden(self):
        ...
```

**Setup/teardown.** Two patterns coexist:

1. **`setUp` + `addCleanup`** for `TemporaryDirectory`:

   ```python
   # tests/test_blog_context_vault.py:36-40
   class VaultBackendTest(unittest.TestCase):
       def setUp(self) -> None:
           self.tmpdir = TemporaryDirectory()
           self.addCleanup(self.tmpdir.cleanup)
           ...
   ```

2. **Custom context-manager helpers** for env-driven configuration:

   ```python
   # tests/test_themes_db.py:20-31
   class _TempDB:
       """Context manager that points themes_db at a fresh temp file."""

       def __enter__(self):
           self._dir = tempfile.TemporaryDirectory()
           self._path = Path(self._dir.name) / "themes.db"
           os.environ["PAINFORWISDOM_THEMES_DB"] = str(self._path)
           return self._path

       def __exit__(self, *exc):
           os.environ.pop("PAINFORWISDOM_THEMES_DB", None)
           self._dir.cleanup()
   ```

**Parametrized assertions via `subTest`** for contract iteration:

```python
# tests/test_contracts.py:40-53
def test_missing_required_field_raises_for_every_node(self) -> None:
    for node, keys in NODE_INPUTS.items():
        for required in keys:
            with self.subTest(node=node, missing=required):
                state = {k: v for k, v in VALID_STATE.items() if k != required}
                with self.assertRaises(InputContractError) as cm:
                    assert_inputs(node, state)
                self.assertEqual(cm.exception.node, node)
                self.assertIn(required, cm.exception.missing)
```

---

## Mocking Patterns

**No live network. No live LLM. No live Notion.**

### HTTP mocking — `httpx.MockTransport`

The research-node and daily-summary tests use `httpx.MockTransport` to
intercept requests in-memory:

```python
# tests/test_research_node.py:22-46
def _mock_transport(handler):
    return httpx.MockTransport(handler)

def _make_client(handler):
    return httpx.Client(transport=_mock_transport(handler), follow_redirects=True)

GOOD_BODY = "<html><body>" + ("Mechanism of effortful behaviour..." * 50) + "</body></html>"
PAYWALL_BODY = "<html><body>Subscribers only — sign in to read.</body></html>"

class ClassifyUrlTest(unittest.TestCase):
    def test_reachable_url_passes(self):
        def handler(req):
            return httpx.Response(200, text=GOOD_BODY)

        with _make_client(handler) as client:
            state, reason = _classify_url(
                "https://pmc.ncbi.nlm.nih.gov/articles/PMC7381101/", client
            )
        self.assertEqual(state, "yes")
        self.assertIn("verified", reason)
```

### `unittest.mock.patch` for module-internal seams

When a function calls peer-module helpers, those helpers are patched at
the calling module's import path:

```python
# tests/test_research_node.py:152-161
def fake_classify(url, client):
    return "no", "http-status: 404"

with patch("pipeline.nodes.research._classify_url", side_effect=fake_classify):
    kept, dropped = _verify_rows(rows)
```

Counter dicts (`called = {"n": 0}`) are used instead of
`mock.call_count` when the test author wants the count visible
inline:

```python
# tests/test_research_node.py:78-88
def test_banned_domain_rejected_before_fetch(self):
    called = {"n": 0}

    def handler(req):
        called["n"] += 1
        return httpx.Response(200, text=GOOD_BODY)

    with _make_client(handler) as client:
        state, reason = _classify_url("https://www.amazon.com/dp/123", client)
    self.assertEqual(state, "no")
    self.assertIn("banned-domain", reason)
    self.assertEqual(called["n"], 0)  # Never fetched
```

### `patch.object` for module-level constants

`tests/test_summarize_daily_fetch_resilience.py:60`:

```python
with patch.object(fetcher, "DENYLIST_FILE", denylist):
    ...
```

### Filesystem "mocking" via `TemporaryDirectory`

Filesystem dependencies (vault, books, themes DB, dormant WordPress
bundles) are exercised against real temp dirs, not mocked:

```python
# tests/test_wordpress_client.py:64-83
class WriteDormantBundleTest(unittest.TestCase):
    def test_bundle_files_written(self) -> None:
        with TemporaryDirectory() as tmp:
            out_dir = Path(tmp) / "wp"
            paths = write_dormant_bundle(
                out_dir,
                title="my post",
                body_md="hello\n\nworld",
                ...
            )
            self.assertTrue(Path(paths["post_md"]).is_file())
            meta = json.loads(Path(paths["meta_json"]).read_text())
            self.assertEqual(meta["title"], "my post")
```

### What is NOT mocked

- **The LLM itself.** Unit tests cover only *non-LLM* helpers
  (parsers, contracts, URL classifiers, markdown→HTML, dormant bundle
  writers). LLM-shape regressions are caught by the **smoke harness**,
  which makes real LLM calls against the sandbox profile.
- **Notion API for migration idempotency.** Instead, the test is
  skipped when offline (`@unittest.skipUnless(os.getenv("NOTION_API_KEY"),
  "NOTION_API_KEY not set")` in
  `tests/test_migration_idempotency.py:27`).
- **OpenCV / ffmpeg.** Heavy-path image-extractor tests are gated:
  ```python
  # tests/test_image_extractor.py:40-53
  def _ffmpeg_available() -> bool:
      return shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None

  def _opencv_available() -> bool:
      try:
          import cv2
          import numpy
          return True
      except ImportError:
          return False

  @unittest.skipUnless(_ffmpeg_available() and _opencv_available(), "ffmpeg / opencv missing")
  class EndToEndImageExtractTest(unittest.TestCase):
      def test_synthesised_video_produces_jpeg(self) -> None:
          ...
          # Synthesizes a 3-second SMPTE-bars video with ffmpeg, runs
          # the real pick_and_resize_best path, asserts JPEG SOI marker.
  ```

---

## Fixtures and Factories

**Test data builders are local helpers.** No `factory_boy` or
equivalent. Each test file defines tiny factories at module top:

```python
# tests/test_summarize_daily_fetch_resilience.py:29-47
def _row(url: str, title: str = "Untitled") -> dict:
    return {
        "page_id": f"pid-{url}",
        "url": "https://notion.so/x",
        "title": title,
        "type": "Paper",
        ...
        "source_url": url,
    }
```

```python
# tests/test_themes_db.py:34-40
def _sample_themes() -> list[Theme]:
    return [
        Theme("umbrella-a", None, "dead", "umbrella-a", "DEAD umbrella A", 999, "(dead)"),
        Theme("sub-a1", "umbrella-a", "active", "umbrella-a", "first sub of A", 10, "rule a1"),
        ...
    ]
```

```python
# tests/test_contracts.py:22-36
VALID_STATE = {
    "video_path": "/tmp/v.mp4",
    "run_dir": "/tmp/run",
    "transcript_text": "hello world this is a transcript",
    ...
}
```

```python
# tests/test_blog_context_vault.py:14-32
def _write_entry(entries_dir: Path, slug: str, date: str, themes: list[str], body: str) -> Path:
    ...
    path.write_text(dedent(f"""\
        # {date} — {slug.replace('-', ' ').title()}

        **Date:** {date}
        **Themes:** {theme_links}
        ...
    """))
    return path
```

**Real transcript fixtures.** `tests/fixtures/` holds four
checked-in transcripts — these are golden inputs, but **not golden
outputs**. They drive the smoke harness to exercise four distinct
pipeline branches (see [`tests/fixtures/README.md`](../../tests/fixtures/README.md)):

| Fixture | Quality | Expected kb-curator path |
|---------|---------|--------------------------|
| `tests/fixtures/transcript_2026-04-14.txt` | Strong | `NEEDS_APPROVAL_THEME` → `PROCEED` |
| `tests/fixtures/transcript_2026-04-15-flagged.txt` | Flagged | `PROCEED` (auto-attaches `pattern-manifestation`) |
| `tests/fixtures/transcript_2026-04-16-weak.txt` | Weak | `PROCEED` (entry created, flagged for thinness) |
| `tests/fixtures/transcript_2026-04-17-strong-existing-themes.txt` | Strong | `PROCEED` directly (no HITL) |

**No snapshot/golden-file output assertions.** The smoke harness
validates verdicts (PASS / PARTIAL / FAIL) and presence-checks via
`pipeline/nodes/validator.py`, not byte-for-byte output matching.
Generated vault entries, blog text, and research CSVs vary by LLM
sampling — they're audited structurally
(`EXPECTED_ENTRY_SECTIONS = ("Story Anchor", "Core Insight")` in
`pipeline/nodes/validator.py:42`) rather than diffed.

---

## End-to-End Pipeline Exercise

**Canonical transcripts, not video.** The smoke harness skips Whisper
by feeding `--from-transcript`. The PXL_20260413_194231193.mp4 video
referenced as a perf baseline is exercised manually via
`python -m pipeline.run --video bulk-daily/PXL_*.mp4` against the
production profile, not via automated tests.

**Smoke harness — `tests/smoke_pipeline.sh`:**

```bash
#!/usr/bin/env bash
# tests/smoke_pipeline.sh
set -euo pipefail
PROJECT_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$PROJECT_ROOT"

if [[ ! -f .env.sandbox ]]; then
    echo "[smoke] .env.sandbox missing — copy .env.sandbox.template, fill in" >&2
    exit 1
fi

PYTHON_BIN="${PYTHON_BIN:-/home/gonzalo/miniconda3/envs/painforwisdom-poc/bin/python}"
FIXTURE="${SMOKE_FIXTURE:-tests/fixtures/transcript_2026-04-14.txt}"

exec "$PYTHON_BIN" -m pipeline.run \
    --profile sandbox \
    --from-transcript "$FIXTURE" \
    --auto-approve \
    --telegram-on-error
```

What this exercises end-to-end (per `pipeline/graph.py:build_graph`):

```
START → extract → kb_curator(HITL bypassed by --auto-approve)
                ├─▶ writer ─▶ notion_blog ─┐
                ├─▶ research ─▶ notion_research ──┐
                ├─▶ extract_image ──────┐         │
                └─▶ youtube_upload ─────┼─────────┤
                                         ▼         │
                                  wordpress_draft ─┤
                                                   ▼
                                            validator → END
```

Exit codes: `0 = PASS or PARTIAL`, `2 = FAIL`.

**Sandbox profile contract** (`--profile sandbox`,
`pipeline/run.py:58-69`):
- Loads `.env.sandbox` instead of `.env`.
- Points at duplicated Notion DBs (`NOTION_*_DATA_SOURCE_ID` differ).
- Points `VAULT_PATH` at `obsidian-vault-sandbox/` (a separate git
  worktree of the vault submodule).
- Uses a separate `CHECKPOINT_DB_PATH`
  (`pipeline/checkpoints-sandbox.db`).
- Prefixes every Telegram message with `[SANDBOX] ` via
  `TELEGRAM_MESSAGE_PREFIX`.

**Reset between runs** — `tests/sandbox_reset.sh`. Idempotent. Reverts
the vault worktree and archives all pages in the sandbox Notion DBs.
Documented in `OPERATIONS.md:64-93`. Standard iteration loop:

```bash
bash tests/sandbox_reset.sh && bash tests/smoke_pipeline.sh
```

**Pre-flight, no smoke needed:**

```bash
# Verify NOTION_API_KEY + schema match
python -m pipeline.smoke_notion

# Forecast tokens / cost / quota for a transcript or video — no LLM call, no Notion write
python -m pipeline.cost_forecast --transcript path/to/transcript.txt
python -m pipeline.cost_forecast --video path/to/video.mp4
```

---

## Test Types

**Unit tests (the bulk):**
- Pure functions in `pipeline/banned_sources.py`,
  `pipeline/contracts.py`, `pipeline/themes_db.py`,
  `pipeline/local_books.py`, `pipeline/image_extractor.py`
  helpers, `pipeline/wordpress_client.py` markdown→HTML +
  bundle writers.
- Node helpers when isolatable (`pipeline.nodes.research._classify_url`,
  `_verify_rows`).
- ~1,200 LOC across 10 test files.

**Integration tests** (without live network):
- `tests/test_blog_context_vault.py` — exercises the vault-backed
  cross-post context backend against a TemporaryDirectory mirroring the
  real vault layout.
- `tests/test_summarize_daily_fetch_resilience.py` — regression-pins
  the 2026-05-13 silent-failure incident: denylist load, per-row
  fail-soft, all-rows-fail loud abort. Mocks `fetcher.DENYLIST_FILE`
  and `httpx`.

**Skipped-by-default integration:**
- `tests/test_migration_idempotency.py` —
  `@unittest.skipUnless(os.getenv("NOTION_API_KEY"))`. Hits real Notion
  in a dry-run mode to assert no-op idempotency.
- `tests/test_image_extractor.py:EndToEndImageExtractTest` — gated on
  ffmpeg + OpenCV availability.

**End-to-end / smoke:**
- `tests/smoke_pipeline.sh` — see above. Exercises the full LangGraph
  DAG against the sandbox profile and a fixture transcript. Calls the
  real LLM, the real (sandbox) Notion, the real (sandbox) vault.

**No load tests, no fuzz tests, no property-based tests.**

---

## Common Patterns

**`assertRaises` with exception inspection:**

```python
# tests/test_contracts.py:49-53
with self.assertRaises(InputContractError) as cm:
    assert_inputs(node, state)
self.assertEqual(cm.exception.node, node)
self.assertIn(required, cm.exception.missing)
```

**Mocked side-effect with counter:**

```python
# tests/test_research_node.py:172-183
called = {"n": 0}

def fake_classify(url, client):
    called["n"] += 1
    return "yes", "verified 9999 chars"

with patch("pipeline.nodes.research._classify_url", side_effect=fake_classify):
    kept, dropped = _verify_rows(rows)
self.assertEqual(called["n"], 0)
```

**Bytes inspection for binary outputs:**

```python
# tests/test_image_extractor.py:79-81
header = result_path.read_bytes()[:3]
self.assertEqual(header, b"\xff\xd8\xff")  # JPEG SOI marker
```

**Subprocess fixtures for binary-dependent tests:**

```python
# tests/test_image_extractor.py:60-72
subprocess.run(
    ["ffmpeg", "-y",
     "-f", "lavfi",
     "-i", "smptebars=size=320x240:rate=24:duration=3",
     "-pix_fmt", "yuv420p",
     str(video_path)],
    check=True,
    capture_output=True,
)
```

---

## Coverage

**No coverage tool configured.** No `.coveragerc`, no
`coverage.xml`, no badge. Coverage discipline relies on:
- The smoke harness driving four distinct pipeline branches (see
  `tests/fixtures/README.md`).
- Contract tests asserting every required field in `NODE_INPUTS`
  raises on omission (`tests/test_contracts.py` iterates
  `NODE_INPUTS.items()` automatically — new nodes are auto-covered).
- Regression tests added per-incident (`test_summarize_daily_fetch_resilience.py`
  pins commit `5c424ec — Daily-brief fail-soft …` behavior).

---

## CI / Hooks

**None.** There is no `.github/workflows/`, `.gitlab-ci.yml`, CircleCI,
or local `pre-commit` config. The repo runs entirely off the user's
workstation. Quality gates:

- Manual `python -m unittest discover -s tests` before any commit that
  touches pipeline code.
- `bash tests/sandbox_reset.sh && bash tests/smoke_pipeline.sh` for
  changes that touch the graph topology, nodes, or prompts.
- Systemd watchdog (`pipeline/scripts/check_daily_brief_freshness.sh`)
  alerts on prod regressions to the `daily_summary` Telegram channel —
  this is the *runtime* safety net that compensates for the missing CI.

---

## Where to Add New Tests

| Change type | Add tests to |
|-------------|--------------|
| New pure helper in `pipeline/<x>.py` | New `tests/test_<x>.py` mirroring the import path |
| New LangGraph node | Add a row to `NODE_INPUTS` in `pipeline/contracts.py` — `tests/test_contracts.py` auto-asserts every required field |
| New node helper that does I/O | Mock via `unittest.mock.patch("pipeline.nodes.<stage>.<helper>")`; gate true I/O paths with `@unittest.skipUnless(...)` |
| New pipeline branch | Add a transcript fixture to `tests/fixtures/`, document in `tests/fixtures/README.md`, run via `SMOKE_FIXTURE=... bash tests/smoke_pipeline.sh` |
| Regression on prod-only failure | Pin behavior with a test like `tests/test_summarize_daily_fetch_resilience.py` — header docstring should name the incident date |

---

*Testing analysis: 2026-05-18*

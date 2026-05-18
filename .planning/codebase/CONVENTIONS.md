# Coding Conventions

**Analysis Date:** 2026-05-18

This document captures the actual conventions in use across the
`painforwisdom` repo. Two distinct codebases coexist:

1. **Pipeline (`pipeline/`)** — Python 3, LangGraph orchestrator. ~8,400
   LOC. Production code path.
2. **Obsidian vault (`obsidian-vault/gonzalo-book/`)** — Hand- and
   pipeline-written markdown. Treated as data, not code, but with strict
   structural conventions enforced by `pipeline/nodes/kb_curator.py`.

There is **no `pyproject.toml`, no `ruff`/`black`/`flake8` config, no
`pytest.ini`, no `.pre-commit-config.yaml`, and no `.github/workflows/`**
in the repo. Conventions are enforced by code review and by the
structural assertions in node OUTPUT specs, not by tooling.

---

## Naming Patterns

**Files (Python):**
- `snake_case.py` throughout. Example: `pipeline/nodes/kb_curator.py`,
  `pipeline/summarize_daily/brief_writer.py`.
- One concern per module. `pipeline/runtime.py` comment is explicit:
  *"Keeping this small on purpose. Nodes should import what they need
  rather than sprawling utility modules across the package."*

**LangGraph nodes:**
- Module file: `pipeline/nodes/<stage>.py` (e.g. `extract.py`,
  `kb_curator.py`, `notion_blog.py`).
- Entry-point callable: `node_<stage>(state: State) -> Dict[str, Any]`.
  Examples: `node_extract`, `node_kb_curator`, `node_writer`,
  `node_notion_blog`, `node_validator`. Registered in
  `pipeline/graph.py:build_graph`.
- Stage label used in logging / metrics: bare `<stage>` (no `node_`
  prefix). See `append_metric(run_telemetry_path(...), "extract", ...)`
  in `pipeline/nodes/extract.py`.

**Agent prompts (Markdown):**
- Located in `.claude/agents/<agent-name>.md`, kebab-case.
- Filenames map 1:1 to node callsites via
  `pipeline/runtime.py:load_agent_prompt("kb-curator.md")`.
- Active agents: `coaching-thought-extractor.md`, `kb-curator.md`,
  `painforwisdom-writer.md`, `research-curator.md`,
  `notion-blog-post-logger.md`, `notion-research-logger.md`,
  `pipeline-summary.md`, `youtube-upload-agent.md`,
  `blog-post-catchy-title.md` (legacy — dropped from the active graph).

**Vault content slugs:**
- Theme / framework slugs: `kebab-case-lowercase`. Examples:
  `deliberate-discomfort`, `cookie-jar-types`,
  `pattern-manifestation`. Asserted in
  `.claude/agents/kb-curator.md` rules and in the OUTPUT spec block
  `pipeline/nodes/kb_curator.py:_KB_OUTPUT_SPEC`.
- Vault entry filenames: `YYYY-MM-DD-2-to-4-word-kebab.md`. Example:
  `obsidian-vault/gonzalo-book/entries/2026-02-17-storm-as-perfect-test.md`.
- `slugify()` in `pipeline/runtime.py:110` enforces this for
  generated slugs: lowercase, alphanumeric+hyphen, capped at 4 words.

**Functions / variables:**
- `snake_case` for functions and locals. Module-private helpers
  prefixed with `_` (e.g. `_is_transient`, `_refresh_auth`,
  `_build_messages`).
- Module-level constants: `UPPER_SNAKE_CASE` (e.g. `PROJECT_ROOT`,
  `AGENTS_DIR`, `VAULT_PATH`, `CACHE_TOKEN_FLOOR`,
  `CLAUDE_CODE_IDENTITY`). See `pipeline/runtime.py`, `pipeline/llm.py`.
- `TypedDict` field names: `snake_case` (matches LangGraph state
  reducer semantics). See `pipeline/state.py`.

---

## Code Style

**No formatter / linter is configured.** Style is hand-maintained:
- 4-space indent.
- Soft wrap ~95 cols; docstrings and prose wrap ~80.
- Trailing comma on multi-line tuples / lists / kwargs.
- No `__all__` declarations. Imports are filtered by leading `_`
  convention.

**`from __future__ import annotations` is mandatory.** 20 of 21
`pipeline/*.py` modules carry it. Pattern:

```python
# pipeline/llm.py:20
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any, Dict, List, Optional
```

**Type hints are everywhere.** Every public function in the pipeline is
fully annotated. Internal helpers usually too. Example from
`pipeline/llm.py:122`:

```python
def call_llm(
    model: str,
    system_prompt: str,
    user_message: str,
    max_tokens: int = 2000,
    tools: Optional[List[Dict[str, Any]]] = None,
    web_search: bool = False,
    long_context: bool = False,
    max_retries: int = 4,
    base_backoff_s: float = 4.0,
) -> Dict[str, Any]:
```

**No Pydantic.** Structured data uses:
- `TypedDict` with `total=False` for LangGraph state
  (`pipeline/state.py:12`).
- `dataclasses` for small value types (e.g. `LocalBook` in
  `pipeline/local_books.py`, `Theme` in `pipeline/themes_db.py`,
  `Cluster` in `pipeline/summarize_daily/clusterer.py`).
- Plain `Dict[str, Any]` for LLM payloads and Notion responses.

**`noqa` directives** are reserved for `E402` (deferred imports after
profile-aware `load_dotenv`) and `BLE001` (deliberate broad
`except Exception`). See `pipeline/run.py:61` and
`pipeline/backfill_wordpress.py`.

---

## Import Organization

**Order (enforced by hand, not isort):**

1. Standard library (`os`, `sys`, `time`, `subprocess`, `pathlib`,
   `typing`).
2. Third-party (`httpx`, `litellm`, `yaml`, `langgraph.*`,
   `notion_client`, `dotenv`).
3. First-party (`pipeline.*`).

Each group separated by a blank line. `from __future__` always sits
between the docstring and group 1.

**Profile-aware deferred imports.** `pipeline/run.py` and
`pipeline/retry.py` peek at `sys.argv` for `--profile` BEFORE importing
anything from `pipeline.*` because the pipeline modules read env vars
(`VAULT_PATH`, `NOTION_*_DATA_SOURCE_ID`, `CHECKPOINT_DB_PATH`) at
import time. This is the only place `# noqa: E402` appears in normal
files:

```python
# pipeline/run.py:58-78
_PROFILE = _peek_profile(sys.argv[1:])
_ENV_FILE = ".env.sandbox" if _PROFILE == "sandbox" else ".env"

from dotenv import load_dotenv  # noqa: E402

load_dotenv(PROJECT_ROOT / _ENV_FILE)

# Heavy imports must come after env load.
from langgraph.types import Command  # noqa: E402

from pipeline.contracts import InputContractError  # noqa: E402
from pipeline.graph import build_graph  # noqa: E402
```

**No path aliases.** Pipeline is a flat package; everything imports as
`pipeline.<module>` or `pipeline.nodes.<stage>`.

---

## Error Handling

The project follows a **loud-fail philosophy with classified retries**.
The classification lives in `pipeline/graph.py:_is_transient`:

```python
# pipeline/graph.py:60-96
def _is_transient(exc: Exception) -> bool:
    """Classifier for RetryPolicy.retry_on. True = retry, False = give up.

    Keep this conservative: only retry things that are demonstrably infra
    failures. A `RuntimeError` from a node usually means a structural
    problem (bad model output, missing field) — retrying just burns tokens.
    """
    if isinstance(exc, InputContractError):
        return False
    if isinstance(exc, (httpx.HTTPError, socket.timeout, ConnectionError)):
        return True
    ...
    return False
```

**Three error tiers:**

1. **Per-node input contracts** — Each node's first line is
   `assert_inputs("<node>", state)` from `pipeline/contracts.py`. A
   missing field raises `InputContractError` and bubbles. Contracts are
   declared centrally in `NODE_INPUTS`
   (`pipeline/contracts.py:31`):

   ```python
   NODE_INPUTS: Dict[str, List[str]] = {
       "transcribe":      ["video_path", "run_dir"],
       "extract":         ["transcript_text", "transcript_path", "run_dir"],
       "kb_curator":      ["extraction_report", "video_date", "run_dir"],
       ...
   }
   ```

2. **LangGraph `RetryPolicy`** — 3 attempts, exponential backoff (2s
   initial, 2× factor, 60s max), with jitter, scoped to transient
   errors only. Defined once in `pipeline/graph.py:99`. **Not** attached
   to `extract_image`, `youtube_upload`, `wordpress_draft`, `validator` —
   those handle their own failures and degrade to PARTIAL.

3. **Orchestrator escalation** — After retries are exhausted,
   `pipeline/run.py:_drive_graph` catches the exception, classifies
   non-recoverable cases via `_classify_non_recoverable`
   (`pipeline/run.py:94`), and asks the user via Telegram:

   ```python
   # pipeline/run.py:94-111
   def _classify_non_recoverable(exc: Exception) -> Optional[str]:
       if isinstance(exc, InputContractError):
           return "input contract violation — retry would hit the same check"
       msg = str(exc).lower()
       if "extra usage is required for long context requests" in msg:
           return "Anthropic long-context tier rejected the request — ..."
       if "invalid authentication credentials" in msg or "unauthorized" in msg:
           return "auth credentials rejected after in-call refresh — retry would re-fail"
       return None
   ```

**Single-call retries inside `call_llm`** — `pipeline/llm.py:122` has
its own loop for auth refresh (re-read `~/.claude/.credentials.json`)
and rate-limit backoff. These are intentionally distinct from the
graph-level RetryPolicy: an auth refresh is cheap, but a node retry
would re-do work.

**`kb_curator` does ONE corrective retry** when the LLM drops the
`---kb-plan---` markers or emits invalid YAML
(`pipeline/nodes/kb_curator.py:270`). Token spend is summed across the
two calls.

**Loud failures preferred over silent defaults.** The recent commit
`fd1468b — Daily brief Telegram: ... fail-loud on focal pre-summary
loss` explicitly removed a silent-failure path. Pattern:
`state.get("x")` with a default is a code smell; the `assert_inputs`
contract layer exists *because* `state.get("x", default)` "surfaces three
stages later as something cryptic" (`pipeline/contracts.py:12`).

**Telegram-send failures are non-fatal but surfaced.** Calls log the
non-zero rc and continue. Example in `pipeline/run.py:351`:

```python
rc = telegram_send(intake_msg)
if rc != 0:
    print(f"[run] telegram intake notify rc={rc} (non-fatal)")
```

---

## Logging

**Framework:** `print()` — there is **no `logging` module use** in the
pipeline. `grep -r "import logging" pipeline/` returns nothing.

**Format:** `[<stage>] <message>` with **square-bracketed lowercase
stage name** as a prefix. Stage prefixes in use:

| Prefix | Source |
|--------|--------|
| `[transcribe]` | `pipeline/nodes/transcribe.py` |
| `[extract]` | `pipeline/nodes/extract.py` |
| `[kb-curator]` | `pipeline/nodes/kb_curator.py` |
| `[writer]` | `pipeline/nodes/writer.py` |
| `[research]` | `pipeline/nodes/research.py` |
| `[validator]` | `pipeline/nodes/validator.py` |
| `[run]` | `pipeline/run.py` |
| `[batch]` | `pipeline/run.py` |
| `[retry]` | `pipeline/retry.py` |
| `[smoke]` | `tests/smoke_pipeline.sh` |

Lifecycle pattern — every node prints start + done with timing:

```python
# pipeline/nodes/extract.py:89, 139
print("[extract] start")
...
print(f"[extract] done {duration:.1f}s quality={quality} themes={themes}")
```

**Structured telemetry on disk.** Every node also appends a JSONL row
via `pipeline.runtime.append_metric` to
`processed/<run_id>/<suffix>/runs.jsonl`:

```python
# pipeline/runtime.py:85-90
def append_metric(jsonl_path: Path, stage: str, **fields: Any) -> Dict[str, Any]:
    row = {"ts": now_iso(), "stage": stage, **fields}
    jsonl_path.parent.mkdir(parents=True, exist_ok=True)
    with jsonl_path.open("a") as f:
        f.write(json.dumps(row) + "\n")
    return row
```

Fields always include `stage`, `duration_s`, and (for LLM-calling
nodes) `model`, `input_tokens`, `output_tokens`, `cache_read_tokens`,
`cache_creation_tokens`, `cost_usd`, `billing_mode`.

**There is no global `pipeline.log` file** — the `.log` glob in
`.gitignore:40` (`*.log`) covers ad-hoc captures (e.g. systemd-unit
shell-script logs from `pipeline/scripts/check_daily_brief_freshness.sh`),
but the canonical observability surface is:
- stdout for live runs,
- `runs.jsonl` per run-dir for telemetry,
- `journalctl --user -u painforwisdom-daily-brief.service` for
  scheduled jobs.

---

## Function & Module Design

**Function size.** Most stage entry points are ~80 lines including
docstring. Helpers stay under 30 lines. Anything bigger gets split into
named private helpers (`_apply_proceed`, `_call_llm_for_plan`,
`_vault_snapshot` in `pipeline/nodes/kb_curator.py`).

**Return shape.** Nodes return `Dict[str, Any]` matching the keys
LangGraph will reduce into `State`. They never mutate the input
`state`. Example:

```python
# pipeline/nodes/extract.py:140-149
return {
    "extraction_report": text,
    "extraction_report_path": str(report_path),
    "content_quality": quality,
    "core_insight": core_insight,
    ...
}
```

**Docstrings.** Module-level docstrings are required and substantive —
they describe stage purpose, inputs, outputs, and edge cases. See
`pipeline/llm.py:1-19`, `pipeline/graph.py:1-26`. Function docstrings
focus on **invariants** and **why** (not what — the type hints already
say that).

**Module exports.** No `__all__`. Public surface = names not prefixed
with `_`. `pipeline/__init__.py` is empty.

**No barrel files.** `pipeline/nodes/__init__.py` is empty; each node
is imported directly: `from pipeline.nodes.extract import node_extract`.

---

## Prompt Storage Conventions

**Location:** `.claude/agents/<agent-name>.md`. Loaded by
`pipeline/runtime.py:66:load_agent_prompt`:

```python
def load_agent_prompt(agent_filename: str, *, strip_output_section: bool = True) -> str:
    path = AGENTS_DIR / agent_filename
    raw = path.read_text()
    m = re.match(r"^---\n.*?\n---\n(.*)", raw, re.DOTALL)
    body = m.group(1) if m else raw
    if strip_output_section:
        body = re.split(r"\n##\s+OUTPUT\s*\n", body, maxsplit=1)[0]
    body += CACHE_PADDING_APPENDIX
    return body
```

**Required frontmatter** (YAML, `---` fenced):

```yaml
---
name: coaching-thought-extractor
description: >
  Use this agent whenever Gonzalo provides a video transcript ...
model: claude-opus-4-6
tools: Bash, Write, Read
---
```

Frontmatter is stripped before the prompt is sent to the model.

**`## OUTPUT` section stripped.** The legacy agent prompts told the
model to call MCP tools (`Bash`, `Write`, `Edit`). In LangGraph we keep
file I/O in Python, so `strip_output_section=True` cuts everything
after the `## OUTPUT` heading. Each node then appends its **own**
machine-checkable `## OUTPUT SPEC (STRICT)` (see
`pipeline/nodes/extract.py:_EXTRACT_OUTPUT_SPEC`,
`pipeline/nodes/kb_curator.py:_KB_OUTPUT_SPEC`,
`pipeline/nodes/writer.py:_WRITER_OUTPUT_SPEC`).

**`CACHE_PADDING_APPENDIX` is appended unconditionally**
(`pipeline/runtime.py:31`). It does two jobs at once:
1. Bumps the cached system block above the empirical Sonnet 4.6
   prompt-cache floor (`CACHE_TOKEN_FLOOR = 2200`).
2. Overrides any "use Bash / Write / WebSearch" instructions in the
   role body so the agent returns inline content for the orchestrator
   to write.

---

## Environment Variable Conventions

**Profile selection.** `pipeline/run.py:48` and `pipeline/retry.py:45`
peek argv for `--profile [prod|sandbox]`, then `load_dotenv` either
`.env` or `.env.sandbox`. **Heavy imports must be deferred until after
the env file is loaded** because pipeline modules read env at import
time.

**Caller env overrides `.env` for Telegram routing** (commit
`6cdbdbf — Telegram: caller env overrides .env`). The pattern in
`telegram_io.sh:18-30`:

```bash
# Caller-set TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID / TELEGRAM_PARSE_MODE
# take precedence over .env so a caller can route a single send
# to a non-default channel without editing .env.
_caller_bot_token="${TELEGRAM_BOT_TOKEN:-}"
_caller_chat_id="${TELEGRAM_CHAT_ID:-}"
_caller_parse_mode="${TELEGRAM_PARSE_MODE:-}"
if [ -f "$SCRIPT_DIR/.env" ]; then
    set -a
    source "$SCRIPT_DIR/.env"
    set +a
fi
[ -n "$_caller_bot_token" ] && TELEGRAM_BOT_TOKEN="$_caller_bot_token"
[ -n "$_caller_chat_id" ] && TELEGRAM_CHAT_ID="$_caller_chat_id"
[ -n "$_caller_parse_mode" ] && TELEGRAM_PARSE_MODE="$_caller_parse_mode"
```

Python-side mirror in `pipeline/telegram.py:34`: `send(text, chat_id=..., parse_mode=...)` builds `env` from `os.environ.copy()` and overlays the per-call overrides.

**Variable catalog (referenced by pipeline code):**

| Var | Purpose | Default behavior |
|-----|---------|------------------|
| `ANTHROPIC_AUTH_TOKEN` | Subscription Bearer token. Auto-refreshed from `~/.claude/.credentials.json` on every call (`pipeline/llm.py:_refresh_auth`). | Required for subscription path. |
| `ANTHROPIC_API_KEY` | Pay-per-token fallback (`x-api-key`). | Used only when no subscription token. |
| `PIPELINE_MODEL` | Override default model. | Default `claude-sonnet-4-6` (see `pipeline/nodes/extract.py:97`). |
| `VAULT_PATH` | Obsidian vault root. | Default `<project>/obsidian-vault`. Sandbox sets `<project>/obsidian-vault-sandbox`. |
| `CHECKPOINT_DB_PATH` | LangGraph SqliteSaver path. | Default `pipeline/checkpoints.db`. Sandbox uses `pipeline/checkpoints-sandbox.db`. |
| `NOTION_API_KEY` | Notion integration token. | Required for `notion_blog`, `notion_research`. |
| `NOTION_BLOG_DATA_SOURCE_ID` | UUID of blog DB data source. | **Data source UUID, not database name** (see OPERATIONS.md §6). |
| `NOTION_RESEARCH_DATA_SOURCE_ID` | UUID of research DB data source. | Same. |
| `TELEGRAM_BOT_TOKEN` / `TELEGRAM_CHAT_ID` | Main pipeline chat. | Required. |
| `TELEGRAM_DAILY_SUMMARY_CHAT_ID` | Daily brief channel. | Falls back to `TELEGRAM_CHAT_ID`. |
| `TELEGRAM_MESSAGE_PREFIX` | Prepended to every send. | Set to `[SANDBOX] ` by `pipeline/run.py:69` under `--profile sandbox`. |
| `TELEGRAM_PARSE_MODE` | Telegram parse mode for the next send. | Unset = plain text. Used by daily brief for HTML `<a href>` links. |
| `WORDPRESS_ENABLED`, `WORDPRESS_SITE`, `WORDPRESS_USERNAME`, `WORDPRESS_APP_PASSWORD`, `WORDPRESS_AUTH_MODE`, `WORDPRESS_OAUTH_TOKEN` | WordPress draft node. | Skips gracefully if disabled or missing creds (writes dormant bundle). |
| `YOUTUBE_ENABLED`, `YOUTUBE_CLIENT_ID`, `YOUTUBE_CLIENT_SECRET`, `YOUTUBE_REFRESH_TOKEN` | YouTube upload node. | Skips on missing creds. |
| `ZLIBRARY_EMAIL`, `ZLIBRARY_PASSWORD`, `ZLIBRARY_DOWNLOAD_DIR`, `ZLIBRARY_REPO_PATH`, `ZLIBRARY_TIMEOUT_S` | Research book download bridge. | Used by `pipeline/zlibrary_bridge.py`. |
| `PAINFORWISDOM_THEMES_DB` | Path override for the themes SQLite DB. | Default `pipeline/state/themes.db`. Used by tests via `_TempDB` (`tests/test_themes_db.py:20`). |
| `PAINFORWISDOM_THEMES_YAML` | Path override for the themes seed YAML. | Used by `pipeline/scripts/seed_themes_db.py`. |
| `NLM_PROFILE`, `LISTENER_NAME` | NotebookLM CLI integration. | Used by `pipeline/summarize_daily/notebooklm_publisher.py`. |

`.env` and `.env.sandbox` live at project root (gitignored). The
template is `.env.sandbox.template` (committed).

---

## Commit Message Style

Recent `git log` shows a consistent **scope-prefix + imperative**
pattern, often paired with a PR number:

```
LLM: gate 1M-context beta header per-call; bound error-recovery prompts (#34)
OPERATIONS.md: daily-brief retry budget = 3 starts in 2h (#33)
Watchdog: heal-then-notify for dead daily-brief timer (#32)
Telegram: caller env overrides .env; watchdog alerts to daily-summary channel (#31)
Daily brief Telegram: HTML href Listen link, drop broken audio CDN URL, fail-loud on focal pre-summary loss (#30)
Daily summarizer: 3 briefs/day, direct audio link, dedicated TG channel (#26)
chore: track daily-brief watchdog script + tighten .gitignore (#25)
Daily research-tasks augmenter timer + idempotent audit (#24)
Bump obsidian-vault: May 12 + 13 entries, new themes (#23)
Fix kb-curator YAML parse failure on long reason text (#14)
```

Rules in use:
- **First word identifies a scope** (`LLM:`, `Telegram:`, `Watchdog:`,
  `Daily brief Telegram:`, `OPERATIONS.md:`) followed by a colon, OR a
  bare imperative verb (`Bump`, `Fix`, `Archive`, `Add`, `Remove`,
  `Accept`).
- Sentence case after the scope. No trailing period.
- Multi-concern commits are explicitly joined with `;` so reviewers
  see both pieces in the log.
- `chore:` is the only Conventional-Commit prefix that appears, and
  rarely.
- Subject ends with ` (#N)` — the PR number is appended on merge.
- No `Co-Authored-By` line in current main; commits are direct from
  the user account.

No body lines required for trivial changes; bigger changes carry a few
lines explaining the *why*.

---

## Vault Content Conventions

The vault `obsidian-vault/gonzalo-book/` is structured as a small,
strict graph:

```
gonzalo-book/
├── _index.md          # Timeline table: date → entry link → themes
├── book-outline.md    # Live book outline maintained by kb-curator
├── research-index.md  # Cross-entry research index
├── entries/           # One file per coaching thought, immutable
├── themes/            # One file per recurring theme
└── frameworks/        # One file per named framework
```

**Entry file convention** (`entries/YYYY-MM-DD-<slug>.md`):

```markdown
# 2026-02-17 — The Storm Is the Perfect Test

**Date:** 2026-02-17
**Content Quality:** Strong
**Themes:** [[strategic-discomfort]], [[conditions-as-training]]
**Frameworks:** [[strategic-vs-manufactured-suffering]], [[amcc-effect]]

## Core Insight
...

## Story Anchor
...

## Framework Connection
...

## Practical Application
...

## Who It's For
...

## Integrity Check
...

## Blog Post Seed
...

## Raw Transcript Notes
- ...

---
*Entry created by kb-curator. Do not edit.*

## Research
(appended by research node post-creation)
```

Section names asserted as load-bearing in
`pipeline/nodes/validator.py:42`:
`EXPECTED_ENTRY_SECTIONS = ("Story Anchor", "Core Insight")`.

**Entries are immutable.** `pipeline/nodes/kb_curator.py:316`:

```python
if entry_path.exists():
    # Entries are immutable; refuse to overwrite.
    raise RuntimeError(f"kb-curator: entry already exists, refusing overwrite: {entry_path}")
```

OPERATIONS.md §6 documents the recovery: reset sandbox with
`bash tests/sandbox_reset.sh`, or manually delete from the vault submodule
for prod.

**Theme file convention** (`themes/<slug>.md`):

```markdown
# <Theme Name (Title Case)>

**First appeared:** YYYY-MM-DD
**Last updated:** YYYY-MM-DD
**Entry count:** <N>

## Core tension
...

## Key insight so far
...

## Entries
| Date | Entry | Core Insight |
|------|-------|--------------|
| YYYY-MM-DD | [[YYYY-MM-DD-slug]] | one-sentence insight |
...

## Patterns emerging
...

## Possible chapter angle
...
```

See `obsidian-vault/gonzalo-book/themes/deliberate-discomfort.md` for
the canonical example (26-entry theme).

**Framework file convention** (`frameworks/<slug>.md`):

```markdown
# <Framework Name (Title Case)>

**First appeared:** YYYY-MM-DD
**Last updated:** YYYY-MM-DD

## Definition
...

## Entries referencing this
- [[YYYY-MM-DD-slug]] — connection narrative ...

## Evolution
- YYYY-MM-DD: ...
```

Example: `obsidian-vault/gonzalo-book/frameworks/cookie-jar-types.md`.

**Wikilink convention.** All cross-references use Obsidian double-
bracket links: `[[slug]]` for entries/themes/frameworks. The
`pipeline-summary` and `painforwisdom-writer` blog text also uses
`[[link:<slug>]]` (`pipeline/nodes/writer.py:_WRITER_OUTPUT_SPEC`) as a
placeholder the post-processor expands into a real blog cross-link.

**Pre-approved exception:** `pattern-manifestation` is a permanent
theme. Any `Flagged`-quality entry auto-attaches it without HITL
approval (`pipeline/nodes/kb_curator.py:_KB_OUTPUT_SPEC` rule line ~159,
exercised by fixture `transcript_2026-04-15-flagged.txt`).

---

## OPERATIONS.md Runbook Rules

`OPERATIONS.md` is the day-to-day shell reference. Key conventions it
codifies:

- **Conda env activation first.** Every command assumes
  `conda activate painforwisdom-poc && cd ~/workspace/painforwisdom/painforwisdom`.
- **`crontab -l` is empty by design** (`OPERATIONS.md:189`). All
  scheduled work uses **systemd user units**:
  `painforwisdom-daily-brief.{service,timer}` at
  `~/.config/systemd/user/`.
- **Daily-brief retry budget = 3 starts in 2h.**
  `Restart=on-failure / RestartSec=300 / StartLimitBurst=3 /
  StartLimitIntervalSec=2h` (OPERATIONS §8). Documented in commit
  `57d5cf5`.
- **`--auto-approve` is TEST ONLY.** `pipeline/run.py:461` and
  `OPERATIONS.md:31` both flag this — production runs must NOT pass
  `--auto-approve`.
- **`--telegram-on-error`** pairs with `--auto-approve` for the smoke
  harness: non-blocking error notification before re-raise.
- **`pipeline.cost_forecast`** is the cost preview command. Memory
  rule: project tokens + cost + quota share before any batch replay;
  never silently burn subscription quota.
- **Run-ID collision protection** — batch mode appends `_NNN` to every
  run id (`pipeline/run.py:399`) so two videos in the same wall-clock
  second never share a thread.
- **Resume-after-HITL.** Re-launching with the same `--run-id` resumes
  from the LangGraph checkpoint; the pending `interrupt()` re-prompts
  on Telegram (OPERATIONS §6, `pipeline/run.py:_get_pending_interrupt`).

---

## Quick Reference — Where Things Live

| Concern | Path |
|---------|------|
| Pipeline entry point | `pipeline/run.py` |
| DAG topology + RetryPolicy | `pipeline/graph.py` |
| Per-stage logic | `pipeline/nodes/<stage>.py` |
| Shared state schema | `pipeline/state.py` |
| Per-node input contracts | `pipeline/contracts.py` |
| LLM wrapper + auth refresh | `pipeline/llm.py` |
| Prompt loader + cache padding | `pipeline/runtime.py:load_agent_prompt` |
| Agent prompts (stripped to body) | `.claude/agents/*.md` |
| Telegram I/O | `pipeline/telegram.py` + `telegram_io.sh` |
| Notion REST helpers | `pipeline/notion_client.py` |
| WordPress client + dormant bundle | `pipeline/wordpress_client.py` |
| YouTube client | `pipeline/youtube_client.py` |
| Smart-frame image extraction | `pipeline/image_extractor.py` |
| Cost forecasting (pre-flight) | `pipeline/cost_forecast.py` |
| Notion smoke test | `pipeline/smoke_notion.py` |
| Daily-summary subsystem | `pipeline/summarize_daily/` |
| Themes SQLite + seed | `pipeline/themes_db.py`, `pipeline/scripts/seed_themes_db.py` |
| Banned-source list | `pipeline/banned_sources.py` |
| Tests (stdlib unittest) | `tests/*.py` |
| Smoke harness | `tests/smoke_pipeline.sh` |
| Sandbox reset | `tests/sandbox_reset.sh` |
| Transcript fixtures | `tests/fixtures/transcript_*.txt` |
| Vault content | `obsidian-vault/gonzalo-book/` |
| Per-run artifacts | `processed/<run_id>/<suffix>/` (gitignored) |
| Day-to-day commands | `OPERATIONS.md` |
| High-level overview | `README.md` |

---

*Convention analysis: 2026-05-18*

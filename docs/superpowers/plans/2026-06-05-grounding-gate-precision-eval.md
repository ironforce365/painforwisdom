# Grounding Gate & Precision Eval — Implementation Plan (Stream 0)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a per-claim grounding gate for the coach — assertions about the user must cite an entailing source or be demoted to a question; interpretations are throttled by a temperature knob — plus the offline harness, regression corpus, and synthetic fixtures that calibrate it, all on the Max subscription (no API key).

**Architecture:** New `grounding/` subpackage inside the existing `services/coach/eval/` package. All LLM calls go through one monkeypatchable seam (`eval/llm.py`, backed by `claude -p`), so every logic test runs offline. The gate is a pure decision function fed by an LLM judge; orchestration wires judge → route → rewrite → log. Finally the gate plumbs into the coach send-path behind `COACH_GROUNDING_GATE` (default OFF) — code only, no deploy.

**Tech Stack:** Python 3.14 (linuxbrew host), pytest 9.0.3, `claude` CLI v2.1.165 headless, existing `services/coach/eval/` package, stdlib `subprocess`/`json`/`dataclasses`/`pathlib`.

**Spec:** `docs/superpowers/specs/2026-06-05-grounding-gate-precision-eval-design.md`

**Run tests:** `PYTHONPATH=services/coach python3 -m pytest services/coach/tests/<file> -v`
**Commit cadence:** after each task's tests pass. **Push:** `git push` after each commit (branch `worktree-audio-feedback-loop-poc`).

---

## File Structure

**New (under `services/coach/eval/`):**
- `llm.py` — subscription LLM caller (`call_llm`) shelling `claude -p --output-format json`. The single seam.
- `grounding/__init__.py`
- `grounding/types.py` — `ClaimType`, `Claim`, `Source`, `Verdict`, `Decision`, `GateResult` dataclasses/enums.
- `grounding/config.py` — `GroundingConfig` (temperature, absorption) load/defaults.
- `grounding/segmenter.py` — parse a tagged coach draft → `list[Claim]`.
- `grounding/judge.py` — `judge_claims(claims, sources)` → `list[Verdict]` via the seam (batched, one call).
- `grounding/decide.py` — **pure** `decide(verdict, confidence, temperature)` → `Decision`.
- `grounding/rewriter.py` — `demote_to_question(claim, sources)` via the seam.
- `grounding/corpus.py` — append/load regression records (jsonl + markdown mirror).
- `grounding/gate.py` — `run_gate(...)` orchestration: judge → decide → rewrite → log → reassembled message.
- `grounding/harness.py` — offline CLI: run gate over fixtures/corpus → precision report.
- `grounding/fixtures/*.json` — synthetic self-labeling debrief fixtures; `f000_punishing_yourself.json` first.

**Modified:**
- `eval/judge.py` — route `_call_judge_llm` through `eval.llm.call_llm` (migrate to subscription).
- coach send-path (likely `agent/service.py`) — call the gate behind `COACH_GROUNDING_GATE`.

**Tests (under `services/coach/tests/`):** one `test_grounding_*.py` per module + `test_eval_llm.py`.

---

## Claim tag format (the coach output contract, §2.4)

The coach emits each claim on its own line, tagged. The segmenter parses this; it does not infer. Format:

```
[[claim id=c1 type=fact cite=S2]] You missed Thursday and Friday.
[[claim id=c2 type=interpretation conf=7]] This reads to me like self-punishment.
[[claim id=c3 type=conceptual cite=F1]] Hormesis is adaptation to mild stress.
```

- `id` — stable claim id (string).
- `type` — `fact | interpretation | conceptual`.
- `cite` — comma-separated source ids (required for fact/conceptual; absent ⇒ uncited).
- `conf` — integer 1–10 (required for interpretation).
- Free text after `]]` is the claim text.
- Untagged lines pass through verbatim (not claims — greetings, questions the coach already poses).

Sources are provided to the gate as `list[Source]` with `id`, `tier` (1 or 2), `text`, and `kind` (`thread|debrief|vault_entry|vault_framework|memory`).

---

## Task 1: Subscription LLM seam (`eval/llm.py`)

**Files:**
- Create: `services/coach/eval/llm.py`
- Test: `services/coach/tests/test_eval_llm.py`

- [ ] **Step 1: Write the failing test**

```python
"""call_llm shells out to the claude CLI and returns the .result text."""
from __future__ import annotations
import json
import eval.llm as llm


def test_call_llm_parses_result(monkeypatch):
    captured = {}

    class FakeCompleted:
        returncode = 0
        stdout = json.dumps({"type": "result", "is_error": False, "result": "HELLO"})
        stderr = ""

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        return FakeCompleted()

    monkeypatch.setattr(llm.subprocess, "run", fake_run)
    out = llm.call_llm(system="sys", user="usr", model="claude-sonnet-4-6")
    assert out == "HELLO"
    # uses claude -p headless json with the chosen model
    assert "claude" in captured["cmd"][0]
    assert "-p" in captured["cmd"]
    assert "--output-format" in captured["cmd"] and "json" in captured["cmd"]
    assert "claude-sonnet-4-6" in captured["cmd"]


def test_call_llm_raises_on_api_error(monkeypatch):
    class FakeCompleted:
        returncode = 0
        stdout = json.dumps({"type": "result", "is_error": True, "result": "", "api_error_status": "overloaded"})
        stderr = ""

    monkeypatch.setattr(llm.subprocess, "run", lambda cmd, **kw: FakeCompleted())
    import pytest
    with pytest.raises(llm.LLMError):
        llm.call_llm(system="s", user="u")
```

- [ ] **Step 2: Run test, verify it fails** — `PYTHONPATH=services/coach python3 -m pytest services/coach/tests/test_eval_llm.py -v` → FAIL (no module `eval.llm`).

- [ ] **Step 3: Implement `eval/llm.py`**

```python
"""Subscription-backed LLM caller. Rides Gonzalo's Max plan via the claude CLI
headless mode (no Anthropic API key). See memory: subscription_cli_judge."""
from __future__ import annotations
import json
import subprocess

DEFAULT_MODEL = "claude-sonnet-4-6"
_TIMEOUT_S = 120


class LLMError(RuntimeError):
    pass


def call_llm(*, system: str, user: str, model: str = DEFAULT_MODEL) -> str:
    """Call claude -p headless. Returns the model's text output.

    System + user are concatenated into the single prompt the CLI accepts;
    the CLI has no separate system-prompt flag in -p mode, so we frame it.
    """
    prompt = f"{system}\n\n---\n\n{user}" if system else user
    cmd = ["claude", "-p", prompt, "--output-format", "json", "--model", model]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=_TIMEOUT_S)
    except (FileNotFoundError, subprocess.TimeoutExpired) as e:
        raise LLMError(f"claude CLI call failed: {e}") from e
    if proc.returncode != 0:
        raise LLMError(f"claude CLI exit {proc.returncode}: {proc.stderr[:300]}")
    try:
        payload = json.loads(proc.stdout)
    except json.JSONDecodeError as e:
        raise LLMError(f"non-JSON CLI output: {proc.stdout[:300]}") from e
    if payload.get("is_error"):
        raise LLMError(f"CLI api error: {payload.get('api_error_status')}")
    return payload.get("result", "")
```

- [ ] **Step 4: Run test, verify PASS.**
- [ ] **Step 5: Commit + push** — `feat(eval): subscription LLM seam via claude CLI headless`.

---

## Task 2: Migrate existing judge to the subscription seam

**Files:**
- Modify: `services/coach/eval/judge.py`
- Test: `services/coach/tests/test_eval_judge.py` (existing — must stay green)

- [ ] **Step 1: Confirm existing test passes first** — `... test_eval_judge.py -v` → PASS (baseline).

- [ ] **Step 2: Modify `_call_judge_llm` to use the seam.** Replace the `anthropic.Anthropic()` body with:

```python
def _call_judge_llm(system: str, user: str) -> str:
    from eval.llm import call_llm
    return call_llm(system=system, user=user, model="claude-sonnet-4-6")
```

(Keep the function name + signature — the existing test monkeypatches `eval.judge._call_judge_llm`, so it stays green.)

- [ ] **Step 3: Run existing test, verify still PASS.**
- [ ] **Step 4: Commit + push** — `refactor(eval): route rubric judge through subscription seam`.

---

## Task 3: Core types (`grounding/types.py`)

**Files:**
- Create: `services/coach/eval/grounding/__init__.py` (empty), `services/coach/eval/grounding/types.py`
- Test: `services/coach/tests/test_grounding_types.py`

- [ ] **Step 1: Write the failing test**

```python
from eval.grounding.types import ClaimType, Claim, Source, Verdict, Decision, Action


def test_types_construct():
    c = Claim(id="c1", type=ClaimType.FACT, text="You missed two runs.", cites=["S1"], confidence=None)
    s = Source(id="S1", tier=1, kind="debrief", text="missed Thu/Fri")
    v = Verdict(claim_id="c1", derived_type=ClaimType.FACT, grounded=True, contradicts=False, rationale="ok")
    d = Decision(claim_id="c1", action=Action.ASSERT, question=None)
    assert c.type is ClaimType.FACT and s.tier == 1 and v.grounded and d.action is Action.ASSERT
```

- [ ] **Step 2: Run → FAIL.**
- [ ] **Step 3: Implement `grounding/types.py`**

```python
from __future__ import annotations
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


class ClaimType(str, Enum):
    FACT = "fact"
    INTERPRETATION = "interpretation"
    CONCEPTUAL = "conceptual"


class Action(str, Enum):
    ASSERT = "assert"            # send as-is
    STATE_AS_READ = "state_as_read"  # interpretation above temperature
    DEMOTE = "demote"            # rewrite to a question


@dataclass
class Source:
    id: str
    tier: int           # 1 or 2
    kind: str           # thread|debrief|vault_entry|vault_framework|memory
    text: str


@dataclass
class Claim:
    id: str
    type: ClaimType
    text: str
    cites: list[str] = field(default_factory=list)
    confidence: Optional[int] = None


@dataclass
class Verdict:
    claim_id: str
    derived_type: ClaimType     # judge re-derived (anti-dodge)
    grounded: bool              # for fact/conceptual: cited & entailed by a Tier-1 source
    contradicts: bool           # for interpretation: contradicts a source
    rationale: str


@dataclass
class Decision:
    claim_id: str
    action: Action
    question: Optional[str] = None  # filled when action == DEMOTE


@dataclass
class GateResult:
    message: str                # reassembled, gated message
    decisions: list[Decision]
    logged_ids: list[str]       # claim ids written to the regression corpus
```

- [ ] **Step 4: Run → PASS.**  **Step 5: Commit + push** — `feat(grounding): core types`.

---

## Task 4: Config (`grounding/config.py`)

**Files:** Create `services/coach/eval/grounding/config.py`; Test `services/coach/tests/test_grounding_config.py`

- [ ] **Step 1: Failing test**

```python
import os
from eval.grounding.config import GroundingConfig, load_config


def test_defaults_when_unset(monkeypatch):
    monkeypatch.delenv("COACH_GROUNDING_TEMPERATURE", raising=False)
    monkeypatch.delenv("COACH_GROUNDING_ABSORPTION", raising=False)
    cfg = load_config()
    assert cfg.temperature == 5 and cfg.absorption == 5  # neutral pre-calibration


def test_env_override(monkeypatch):
    monkeypatch.setenv("COACH_GROUNDING_TEMPERATURE", "8")
    monkeypatch.setenv("COACH_GROUNDING_ABSORPTION", "2")
    cfg = load_config()
    assert cfg.temperature == 8 and cfg.absorption == 2


def test_clamps_out_of_range(monkeypatch):
    monkeypatch.setenv("COACH_GROUNDING_TEMPERATURE", "99")
    assert load_config().temperature == 10
```

- [ ] **Step 2: Run → FAIL.**
- [ ] **Step 3: Implement**

```python
from __future__ import annotations
import os
from dataclasses import dataclass

_DEFAULT = 5  # neutral until calibration sets the real default (spec §2.5/§14)


def _read(name: str) -> int:
    raw = os.environ.get(name)
    if raw is None:
        return _DEFAULT
    try:
        return max(1, min(10, int(raw)))
    except ValueError:
        return _DEFAULT


@dataclass
class GroundingConfig:
    temperature: int = _DEFAULT
    absorption: int = _DEFAULT


def load_config() -> GroundingConfig:
    return GroundingConfig(
        temperature=_read("COACH_GROUNDING_TEMPERATURE"),
        absorption=_read("COACH_GROUNDING_ABSORPTION"),
    )
```

- [ ] **Step 4: PASS.**  **Step 5: Commit + push** — `feat(grounding): temperature/absorption config`.

---

## Task 5: Segmenter (`grounding/segmenter.py`)

**Files:** Create `services/coach/eval/grounding/segmenter.py`; Test `services/coach/tests/test_grounding_segmenter.py`

- [ ] **Step 1: Failing test**

```python
from eval.grounding.segmenter import segment
from eval.grounding.types import ClaimType


def test_parses_tagged_claims():
    draft = (
        "[[claim id=c1 type=fact cite=S1,S2]] You missed Thursday and Friday.\n"
        "[[claim id=c2 type=interpretation conf=7]] This reads like self-punishment.\n"
        "Plain line with no tag.\n"
        "[[claim id=c3 type=conceptual cite=F1]] Hormesis is adaptation to mild stress."
    )
    claims, passthrough = segment(draft)
    assert [c.id for c in claims] == ["c1", "c2", "c3"]
    assert claims[0].type is ClaimType.FACT and claims[0].cites == ["S1", "S2"]
    assert claims[1].type is ClaimType.INTERPRETATION and claims[1].confidence == 7 and claims[1].cites == []
    assert claims[2].cites == ["F1"]
    assert "Plain line with no tag." in passthrough


def test_uncited_fact_has_empty_cites():
    claims, _ = segment("[[claim id=c1 type=fact]] You were punishing yourself.")
    assert claims[0].cites == []  # fail-safe: treated as ungrounded downstream
```

- [ ] **Step 2: Run → FAIL.**
- [ ] **Step 3: Implement** — regex parse `\[\[claim ([^\]]*)\]\]\s*(.*)`, parse the key=val attrs, build `Claim`; collect untagged non-empty lines into `passthrough`. Return `(list[Claim], str)`.

```python
from __future__ import annotations
import re
from .types import Claim, ClaimType

_TAG = re.compile(r"^\s*\[\[claim\s+([^\]]*)\]\]\s*(.*)$")


def _attrs(blob: str) -> dict:
    out = {}
    for tok in blob.split():
        if "=" in tok:
            k, v = tok.split("=", 1)
            out[k] = v
    return out


def segment(draft: str) -> tuple[list[Claim], str]:
    claims: list[Claim] = []
    passthrough: list[str] = []
    for line in draft.splitlines():
        m = _TAG.match(line)
        if not m:
            if line.strip():
                passthrough.append(line)
            continue
        a = _attrs(m.group(1))
        cites = [c for c in a.get("cite", "").split(",") if c]
        conf = int(a["conf"]) if a.get("conf", "").isdigit() else None
        claims.append(Claim(
            id=a.get("id", f"auto{len(claims)}"),
            type=ClaimType(a.get("type", "interpretation")),
            text=m.group(2).strip(),
            cites=cites,
            confidence=conf,
        ))
    return claims, "\n".join(passthrough)
```

- [ ] **Step 4: PASS.**  **Step 5: Commit + push** — `feat(grounding): claim segmenter`.

---

## Task 6: Pure decision logic (`grounding/decide.py`) — the heart

**Files:** Create `services/coach/eval/grounding/decide.py`; Test `services/coach/tests/test_grounding_decide.py`

This is pure (no LLM). Test the full routing matrix incl. the "punishing yourself" case.

- [ ] **Step 1: Failing test**

```python
from eval.grounding.decide import decide
from eval.grounding.types import Verdict, ClaimType, Action


def V(dt, grounded=False, contradicts=False):
    return Verdict(claim_id="c", derived_type=dt, grounded=grounded, contradicts=contradicts, rationale="")


def test_grounded_fact_asserts():
    assert decide(V(ClaimType.FACT, grounded=True), confidence=None, temperature=5).action is Action.ASSERT


def test_ungrounded_fact_demoted_regardless_of_temperature():
    # "punishing yourself": fact, not grounded -> always demote, even at temp 1
    assert decide(V(ClaimType.FACT, grounded=False), confidence=None, temperature=1).action is Action.DEMOTE


def test_confident_interpretation_states_as_read():
    d = decide(V(ClaimType.INTERPRETATION), confidence=7, temperature=6)
    assert d.action is Action.STATE_AS_READ


def test_shaky_interpretation_demoted():
    d = decide(V(ClaimType.INTERPRETATION), confidence=4, temperature=6)
    assert d.action is Action.DEMOTE


def test_contradicting_interpretation_demoted_even_if_confident():
    d = decide(V(ClaimType.INTERPRETATION, contradicts=True), confidence=10, temperature=1)
    assert d.action is Action.DEMOTE


def test_grounded_conceptual_asserts():
    assert decide(V(ClaimType.CONCEPTUAL, grounded=True), confidence=None, temperature=5).action is Action.ASSERT
```

- [ ] **Step 2: Run → FAIL.**
- [ ] **Step 3: Implement**

```python
from __future__ import annotations
from typing import Optional
from .types import Verdict, ClaimType, Decision, Action


def decide(verdict: Verdict, *, confidence: Optional[int], temperature: int) -> Decision:
    dt = verdict.derived_type  # judge-re-derived (uncited conceptual already re-derived as interpretation upstream)
    if dt in (ClaimType.FACT, ClaimType.CONCEPTUAL):
        # HARD FLOOR — temperature ignored
        action = Action.ASSERT if verdict.grounded else Action.DEMOTE
        return Decision(claim_id=verdict.claim_id, action=action)
    # INTERPRETATION — temperature band
    if verdict.contradicts:
        return Decision(claim_id=verdict.claim_id, action=Action.DEMOTE)
    conf = confidence if confidence is not None else 1  # missing confidence = least confident
    action = Action.STATE_AS_READ if conf >= temperature else Action.DEMOTE
    return Decision(claim_id=verdict.claim_id, action=action)
```

- [ ] **Step 4: PASS (all 6).**  **Step 5: Commit + push** — `feat(grounding): pure routing decision (hard floor + temp band)`.

---

## Task 7: Claim judge (`grounding/judge.py`) — LLM seam, batched

**Files:** Create `services/coach/eval/grounding/judge.py`; Test `services/coach/tests/test_grounding_judge.py`

- [ ] **Step 1: Failing test** (monkeypatch the seam — no real call)

```python
import json
import eval.grounding.judge as gj
from eval.grounding.types import Claim, ClaimType, Source


def test_judge_claims_parses_batch(monkeypatch):
    claims = [
        Claim(id="c1", type=ClaimType.FACT, text="You missed two runs.", cites=["S1"]),
        Claim(id="c2", type=ClaimType.FACT, text="You were punishing yourself.", cites=[]),
    ]
    sources = [Source(id="S1", tier=1, kind="debrief", text="missed Thursday and Friday")]
    fake = json.dumps({"verdicts": [
        {"claim_id": "c1", "derived_type": "fact", "grounded": True, "contradicts": False, "rationale": "S1 entails"},
        {"claim_id": "c2", "derived_type": "fact", "grounded": False, "contradicts": False, "rationale": "no source"},
    ]})
    monkeypatch.setattr(gj, "call_llm", lambda **kw: fake)
    verdicts = gj.judge_claims(claims, sources)
    assert {v.claim_id: v.grounded for v in verdicts} == {"c1": True, "c2": False}
    assert verdicts[1].derived_type is ClaimType.FACT


def test_uncited_conceptual_rederived_as_interpretation(monkeypatch):
    claims = [Claim(id="c3", type=ClaimType.CONCEPTUAL, text="Studies show X.", cites=[])]
    fake = json.dumps({"verdicts": [
        {"claim_id": "c3", "derived_type": "interpretation", "grounded": False, "contradicts": False, "rationale": "uncited theory"},
    ]})
    monkeypatch.setattr(gj, "call_llm", lambda **kw: fake)
    v = gj.judge_claims(claims, [])[0]
    assert v.derived_type is ClaimType.INTERPRETATION
```

- [ ] **Step 2: Run → FAIL.**
- [ ] **Step 3: Implement** — build a system+user prompt embedding claims + sources, call `call_llm`, extract the JSON object (find first `{`/last `}`), map to `Verdict`s. Import `from eval.llm import call_llm` at module top (so the test can monkeypatch `gj.call_llm`).

```python
from __future__ import annotations
import json
from eval.llm import call_llm
from .types import Claim, Source, Verdict, ClaimType

_SYSTEM = """You are a grounding judge. For each claim, independently re-derive its
type (fact = stated as fact about the user; interpretation = the coach's read;
conceptual = general theory). Do NOT trust the coach's label. Then:
- fact/conceptual: is it ENTAILED by at least one cited Tier-1 source? (grounded true/false)
- an uncited fact/conceptual claim is grounded=false. An uncited conceptual claim should be
  re-derived as 'interpretation'.
- interpretation: does it CONTRADICT any source? (contradicts true/false)
Return ONLY JSON: {"verdicts":[{"claim_id","derived_type","grounded","contradicts","rationale"}]}"""


def _fmt_sources(sources: list[Source]) -> str:
    return "\n".join(f"[{s.id}] (tier{s.tier},{s.kind}) {s.text[:400]}" for s in sources) or "(none)"


def _fmt_claims(claims: list[Claim]) -> str:
    out = []
    for c in claims:
        out.append(f'{c.id} | label={c.type.value} | cites={",".join(c.cites) or "-"} | text="{c.text}"')
    return "\n".join(out)


def judge_claims(claims: list[Claim], sources: list[Source], *, model: str = "claude-sonnet-4-6") -> list[Verdict]:
    if not claims:
        return []
    user = f"SOURCES:\n{_fmt_sources(sources)}\n\nCLAIMS:\n{_fmt_claims(claims)}"
    raw = call_llm(system=_SYSTEM, user=user, model=model)
    start, end = raw.find("{"), raw.rfind("}")
    data = json.loads(raw[start:end + 1])
    verdicts = []
    for v in data["verdicts"]:
        verdicts.append(Verdict(
            claim_id=v["claim_id"],
            derived_type=ClaimType(v["derived_type"]),
            grounded=bool(v.get("grounded", False)),
            contradicts=bool(v.get("contradicts", False)),
            rationale=v.get("rationale", ""),
        ))
    return verdicts
```

- [ ] **Step 4: PASS.**  **Step 5: Commit + push** — `feat(grounding): batched claim judge (subscription seam)`.

---

## Task 8: Demotion rewriter (`grounding/rewriter.py`)

**Files:** Create `services/coach/eval/grounding/rewriter.py`; Test `services/coach/tests/test_grounding_rewriter.py`

- [ ] **Step 1: Failing test** (mock seam)

```python
import eval.grounding.rewriter as rw
from eval.grounding.types import Claim, ClaimType


def test_demote_returns_question(monkeypatch):
    monkeypatch.setattr(rw, "call_llm", lambda **kw: "Were you punishing yourself, or just noticing the heaviness?")
    c = Claim(id="c1", type=ClaimType.FACT, text="You were punishing yourself.")
    q = rw.demote_to_question(c, sources=[])
    assert q.endswith("?")
    assert "punishing" in q.lower()
```

- [ ] **Step 2: Run → FAIL.**
- [ ] **Step 3: Implement** — prompt: "Rewrite this asserted claim as a single open question that preserves the coach's read without asserting it as the user's reality. Return only the question." Call seam, strip, ensure endswith `?` (append if missing).

```python
from __future__ import annotations
from eval.llm import call_llm
from .types import Claim, Source

_SYSTEM = ("Rewrite the coach's asserted claim as ONE short, open question that surfaces the "
           "coach's read WITHOUT asserting it as the user's reality. Keep the hypothesis, strip "
           "the certainty. Return only the question, nothing else.")


def demote_to_question(claim: Claim, sources: list[Source], *, model: str = "claude-sonnet-4-6") -> str:
    user = f'CLAIM: "{claim.text}"'
    q = call_llm(system=_SYSTEM, user=user, model=model).strip().strip('"')
    return q if q.endswith("?") else q + "?"
```

- [ ] **Step 4: PASS.**  **Step 5: Commit + push** — `feat(grounding): demotion rewriter`.

---

## Task 9: Regression corpus (`grounding/corpus.py`)

**Files:** Create `services/coach/eval/grounding/corpus.py`; Test `services/coach/tests/test_grounding_corpus.py`

- [ ] **Step 1: Failing test** (use `tmp_path`)

```python
from eval.grounding.corpus import RegressionCorpus


def test_append_and_load(tmp_path):
    corpus = RegressionCorpus(tmp_path / "corpus")
    corpus.append({"claim_id": "c1", "signal": "catch", "claim_text": "you were punishing yourself",
                   "demoted_question": "were you?", "rationale": "ungrounded"})
    corpus.append({"claim_id": "c2", "signal": "correction", "claim_text": "x", "user_correction": "no"})
    records = corpus.load()
    assert len(records) == 2 and records[0]["signal"] == "catch"
    # human-readable mirror exists
    assert (tmp_path / "corpus" / "corpus.md").exists()
    assert (tmp_path / "corpus" / "corpus.jsonl").exists()


def test_signal_filter(tmp_path):
    corpus = RegressionCorpus(tmp_path / "c")
    corpus.append({"claim_id": "a", "signal": "catch", "claim_text": "t"})
    corpus.append({"claim_id": "b", "signal": "validation", "claim_text": "t"})
    assert [r["claim_id"] for r in corpus.load(signal="catch")] == ["a"]
```

- [ ] **Step 2: Run → FAIL.**
- [ ] **Step 3: Implement** — `RegressionCorpus(dir)`: ensures dir; `append(record)` adds `ts` placeholder field (caller may set; here use empty string since `Date.now` unavailable — store `record` as-is, do not synthesize time), writes a jsonl line + appends a markdown bullet; `load(signal=None)` reads jsonl, filters. (Timestamps are supplied by the caller/runtime, not invented here.)

```python
from __future__ import annotations
import json
from pathlib import Path


class RegressionCorpus:
    def __init__(self, directory: Path | str):
        self.dir = Path(directory)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.jsonl = self.dir / "corpus.jsonl"
        self.md = self.dir / "corpus.md"

    def append(self, record: dict) -> None:
        with self.jsonl.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
        line = (f"- **{record.get('signal','?')}** `{record.get('claim_id','?')}` — "
                f"{record.get('claim_text','')[:160]}"
                + (f"  → _{record['demoted_question']}_" if record.get("demoted_question") else "")
                + (f"  ✗ correction: {record['user_correction']}" if record.get("user_correction") else "")
                + "\n")
        with self.md.open("a", encoding="utf-8") as f:
            f.write(line)

    def load(self, signal: str | None = None) -> list[dict]:
        if not self.jsonl.exists():
            return []
        out = []
        for ln in self.jsonl.read_text(encoding="utf-8").splitlines():
            if ln.strip():
                rec = json.loads(ln)
                if signal is None or rec.get("signal") == signal:
                    out.append(rec)
        return out
```

- [ ] **Step 4: PASS.**  **Step 5: Commit + push** — `feat(grounding): regression corpus (jsonl + md)`.

---

## Task 10: Gate orchestration (`grounding/gate.py`)

**Files:** Create `services/coach/eval/grounding/gate.py`; Test `services/coach/tests/test_grounding_gate.py`

Wires segment → judge → decide → rewrite → log → reassemble. Inject judge/rewriter so tests stay offline.

- [ ] **Step 1: Failing test**

```python
from eval.grounding.gate import run_gate
from eval.grounding.types import Verdict, ClaimType, Source
from eval.grounding.corpus import RegressionCorpus


def test_gate_demotes_ungrounded_fact(tmp_path):
    draft = (
        "[[claim id=c1 type=fact cite=S1]] You missed Thursday and Friday.\n"
        "[[claim id=c2 type=fact]] You were punishing yourself.\n"
        "How did the week feel overall?"
    )
    sources = [Source(id="S1", tier=1, kind="debrief", text="missed Thu/Fri, legs heavy")]
    verdicts = [
        Verdict("c1", ClaimType.FACT, grounded=True, contradicts=False, rationale=""),
        Verdict("c2", ClaimType.FACT, grounded=False, contradicts=False, rationale="no source for self-punishment"),
    ]
    corpus = RegressionCorpus(tmp_path / "c")
    result = run_gate(
        draft, sources, temperature=5,
        judge_fn=lambda claims, srcs: verdicts,
        rewrite_fn=lambda claim, srcs: "Were you punishing yourself, or just noticing the heaviness?",
        corpus=corpus,
    )
    assert "You missed Thursday and Friday." in result.message      # grounded fact survives
    assert "punishing yourself." not in result.message               # assertion removed
    assert "Were you punishing yourself" in result.message           # replaced by question
    assert "How did the week feel overall?" in result.message        # passthrough kept
    assert "c2" in result.logged_ids
    assert corpus.load(signal="catch")[0]["claim_id"] == "c2"


def test_gate_states_confident_interpretation(tmp_path):
    draft = "[[claim id=c1 type=interpretation conf=8]] This reads like self-punishment."
    verdicts = [Verdict("c1", ClaimType.INTERPRETATION, grounded=False, contradicts=False, rationale="")]
    result = run_gate(draft, [], temperature=6,
                      judge_fn=lambda c, s: verdicts,
                      rewrite_fn=lambda c, s: "Q?",
                      corpus=RegressionCorpus(tmp_path / "c"))
    assert "self-punishment" in result.message
    assert "my read" in result.message.lower()  # framed as the coach's read
```

- [ ] **Step 2: Run → FAIL.**
- [ ] **Step 3: Implement** — default `judge_fn=judge_claims`, `rewrite_fn=demote_to_question`. For each claim: get verdict (by id), `decide(...)`, build output fragment per action (ASSERT → text; STATE_AS_READ → `"My read: " + text`; DEMOTE → rewrite + log catch). Reassemble claims (in order) + passthrough appended. Return `GateResult`.

```python
from __future__ import annotations
from typing import Callable, Optional
from .segmenter import segment
from .decide import decide
from .types import Action, Source, GateResult, Decision
from .judge import judge_claims
from .rewriter import demote_to_question
from .corpus import RegressionCorpus


def run_gate(draft: str, sources: list[Source], *, temperature: int,
             judge_fn: Callable = judge_claims,
             rewrite_fn: Callable = demote_to_question,
             corpus: Optional[RegressionCorpus] = None,
             thread_id: str = "", user_id: str = "") -> GateResult:
    claims, passthrough = segment(draft)
    verdicts = {v.claim_id: v for v in judge_fn(claims, sources)}
    out_lines: list[str] = []
    decisions: list[Decision] = []
    logged: list[str] = []
    for c in claims:
        v = verdicts.get(c.id)
        if v is None:
            # no verdict -> fail safe: demote
            q = rewrite_fn(c, sources)
            out_lines.append(q)
            decisions.append(Decision(c.id, Action.DEMOTE, q))
            continue
        d = decide(v, confidence=c.confidence, temperature=temperature)
        d.question = None
        if d.action is Action.ASSERT:
            out_lines.append(c.text)
        elif d.action is Action.STATE_AS_READ:
            out_lines.append(f"My read: {c.text}")
        else:  # DEMOTE
            q = rewrite_fn(c, sources)
            d.question = q
            out_lines.append(q)
            if corpus is not None:
                corpus.append({
                    "claim_id": c.id, "signal": "catch", "claim_text": c.text,
                    "type": v.derived_type.value, "cited_sources": c.cites,
                    "demoted_question": q, "judge_rationale": v.rationale,
                    "thread_id": thread_id, "user_id": user_id,
                })
                logged.append(c.id)
        decisions.append(d)
    message = "\n".join(out_lines + ([passthrough] if passthrough else []))
    return GateResult(message=message, decisions=decisions, logged_ids=logged)
```

- [ ] **Step 4: PASS.**  **Step 5: Commit + push** — `feat(grounding): gate orchestration`.

---

## Task 11: Synthetic fixtures + loader

**Files:** Create `services/coach/eval/grounding/fixtures/f000_punishing_yourself.json`, `f001_grounded_facts.json`, `f002_confident_read.json`; Create `services/coach/eval/grounding/fixtures.py`; Test `services/coach/tests/test_grounding_fixtures.py`

Fixture schema (self-labeling): `{id, sources:[{id,tier,kind,text}], draft, expect:{assert:[claim_ids], demote:[claim_ids], state_as_read:[claim_ids]}}`.

- [ ] **Step 1: Author `f000_punishing_yourself.json`**

```json
{
  "id": "f000_punishing_yourself",
  "note": "Reconstructs the PoC hallucination. Source plants missed runs + heaviness, OMITS any emotional cause.",
  "sources": [
    {"id": "S1", "tier": 1, "kind": "debrief", "text": "Missed Thursday and Friday runs. Legs felt heavy on Saturday."}
  ],
  "draft": "[[claim id=c1 type=fact cite=S1]] You missed Thursday and Friday.\n[[claim id=c2 type=fact]] You were out there punishing yourself for the miles you didn't log.",
  "expect": {"assert": ["c1"], "demote": ["c2"], "state_as_read": []}
}
```

- [ ] **Step 2: Author `f001_grounded_facts.json`** (all grounded facts → all assert) and `f002_confident_read.json` (one interpretation conf=8, temp 6 → state_as_read; one interpretation conf=3 → demote). Use the same schema with concrete content.

- [ ] **Step 3: Failing test for the loader**

```python
from eval.grounding.fixtures import load_fixtures


def test_fixtures_load_and_have_expectations():
    fx = load_fixtures()
    ids = {f["id"] for f in fx}
    assert "f000_punishing_yourself" in ids
    f000 = next(f for f in fx if f["id"] == "f000_punishing_yourself")
    assert "c2" in f000["expect"]["demote"]
    assert all({"sources", "draft", "expect"} <= set(f) for f in fx)
```

- [ ] **Step 4: Implement `fixtures.py`** — glob `fixtures/*.json`, `json.load` each, return list.

```python
from __future__ import annotations
import json
from pathlib import Path

_DIR = Path(__file__).parent / "fixtures"


def load_fixtures() -> list[dict]:
    return [json.loads(p.read_text(encoding="utf-8")) for p in sorted(_DIR.glob("*.json"))]
```

- [ ] **Step 5: PASS. Commit + push** — `feat(grounding): synthetic self-labeling fixtures + loader`.

---

## Task 12: Offline harness (`grounding/harness.py`)

**Files:** Create `services/coach/eval/grounding/harness.py`; Test `services/coach/tests/test_grounding_harness.py`

Runs the gate over fixtures, compares decisions to `expect`, computes agreement. Judge is injected so the unit test is offline; the `__main__` path uses the real judge (subscription).

- [ ] **Step 1: Failing test** (inject a perfect judge derived from each fixture's expectation)

```python
from eval.grounding.harness import score_fixture
from eval.grounding.types import Verdict, ClaimType


def test_score_fixture_perfect_judge():
    fx = {
        "id": "t", "sources": [{"id": "S1", "tier": 1, "kind": "debrief", "text": "missed Thu/Fri"}],
        "draft": "[[claim id=c1 type=fact cite=S1]] You missed two runs.\n[[claim id=c2 type=fact]] You hated yourself.",
        "expect": {"assert": ["c1"], "demote": ["c2"], "state_as_read": []},
    }
    # judge: c1 grounded, c2 not
    def judge_fn(claims, sources):
        return [
            Verdict("c1", ClaimType.FACT, grounded=True, contradicts=False, rationale=""),
            Verdict("c2", ClaimType.FACT, grounded=False, contradicts=False, rationale=""),
        ]
    report = score_fixture(fx, temperature=6, judge_fn=judge_fn, rewrite_fn=lambda c, s: "Q?")
    assert report["correct"] == 2 and report["total"] == 2 and report["agreement"] == 1.0
```

- [ ] **Step 2: Run → FAIL.**
- [ ] **Step 3: Implement** — `score_fixture(fx, temperature, judge_fn, rewrite_fn)`: build `Source`s, `run_gate`, map each claim id → action, compare to `expect` (assert/demote/state_as_read membership), count correct, return `{id, correct, total, agreement, mismatches}`. Add `run_all(temperature, judge_fn=judge_claims)` aggregating across fixtures, and a `__main__` that runs with the real judge + prints a report. Use `argparse` for `--temperature`.

(Full `score_fixture` code:)

```python
from __future__ import annotations
import argparse
from .types import Source, Action
from .gate import run_gate
from .judge import judge_claims
from .rewriter import demote_to_question
from .fixtures import load_fixtures


def _expected_action(fx, claim_id):
    for action_name in ("assert", "demote", "state_as_read"):
        if claim_id in fx["expect"].get(action_name, []):
            return {"assert": Action.ASSERT, "demote": Action.DEMOTE,
                    "state_as_read": Action.STATE_AS_READ}[action_name]
    return None


def score_fixture(fx, *, temperature, judge_fn=judge_claims, rewrite_fn=demote_to_question):
    sources = [Source(**s) for s in fx["sources"]]
    result = run_gate(fx["draft"], sources, temperature=temperature,
                      judge_fn=judge_fn, rewrite_fn=rewrite_fn)
    by_id = {d.claim_id: d.action for d in result.decisions}
    correct = total = 0
    mismatches = []
    for cid, got in by_id.items():
        exp = _expected_action(fx, cid)
        if exp is None:
            continue
        total += 1
        if got is exp:
            correct += 1
        else:
            mismatches.append({"claim_id": cid, "expected": exp.value, "got": got.value})
    return {"id": fx["id"], "correct": correct, "total": total,
            "agreement": (correct / total) if total else 0.0, "mismatches": mismatches}


def run_all(*, temperature, judge_fn=judge_claims):
    return [score_fixture(fx, temperature=temperature, judge_fn=judge_fn) for fx in load_fixtures()]


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--temperature", type=int, default=6)
    args = ap.parse_args()
    reports = run_all(temperature=args.temperature)
    tot = sum(r["total"] for r in reports)
    ok = sum(r["correct"] for r in reports)
    for r in reports:
        print(f"{r['id']}: {r['correct']}/{r['total']} agreement={r['agreement']:.2f} mismatches={r['mismatches']}")
    print(f"OVERALL: {ok}/{tot} agreement={ (ok/tot) if tot else 0:.2f}")
```

- [ ] **Step 4: PASS (unit).**  **Step 5: Commit + push** — `feat(grounding): offline precision harness`.
- [ ] **Step 6 (real-call calibration, overnight, manual):** run `PYTHONPATH=services/coach python3 -m eval.grounding.harness --temperature 6` (real subscription judge). Capture output into `services/coach/eval/grounding/CALIBRATION.md`. **Acceptance: f000 `c2` is in `demote`.** If the real judge mis-scores f000, tune `_SYSTEM` in `grounding/judge.py` and re-run. Commit `CALIBRATION.md`.

---

## Task 13: Coach send-path integration (behind `COACH_GROUNDING_GATE`, default OFF)

**Files:**
- Modify: coach send-path — locate the function in `services/coach/agent/service.py` that produces the final reply string before returning/streaming (search for `reply` / `StreamingResponse`). If streaming complicates it, gate the fully-accumulated reply before the final send.
- Create: `services/coach/eval/grounding/integration.py` — thin adapter `maybe_gate(reply, sources, user_id, thread_id) -> str`.
- Test: `services/coach/tests/test_grounding_integration.py`

- [ ] **Step 1: Failing test** for the adapter

```python
import eval.grounding.integration as integ


def test_flag_off_returns_reply_unchanged(monkeypatch):
    monkeypatch.delenv("COACH_GROUNDING_GATE", raising=False)
    out = integ.maybe_gate("[[claim id=c1 type=fact]] You were punishing yourself.", sources=[], user_id="u", thread_id="t")
    assert out == "[[claim id=c1 type=fact]] You were punishing yourself."  # untouched when OFF


def test_flag_on_gates(monkeypatch):
    monkeypatch.setenv("COACH_GROUNDING_GATE", "1")
    monkeypatch.setattr(integ, "judge_claims", lambda claims, srcs: [
        __import__("eval.grounding.types", fromlist=["Verdict","ClaimType"]).Verdict(
            "c1", __import__("eval.grounding.types", fromlist=["ClaimType"]).ClaimType.FACT,
            grounded=False, contradicts=False, rationale="")
    ])
    monkeypatch.setattr(integ, "demote_to_question", lambda c, s: "Were you punishing yourself?")
    out = integ.maybe_gate("[[claim id=c1 type=fact]] You were punishing yourself.", sources=[], user_id="u", thread_id="t")
    assert out == "Were you punishing yourself?"
```

- [ ] **Step 2: Run → FAIL.**
- [ ] **Step 3: Implement `integration.py`**

```python
from __future__ import annotations
import os
from eval.grounding.types import Source
from eval.grounding.gate import run_gate
from eval.grounding.judge import judge_claims        # re-exported for monkeypatching
from eval.grounding.rewriter import demote_to_question
from eval.grounding.config import load_config
from eval.grounding.corpus import RegressionCorpus

_CORPUS_DIR = os.environ.get("COACH_GROUNDING_CORPUS_DIR", "/data/grounding_corpus")


def maybe_gate(reply: str, *, sources: list[Source], user_id: str, thread_id: str) -> str:
    if os.environ.get("COACH_GROUNDING_GATE") not in ("1", "true", "on"):
        return reply
    cfg = load_config()
    result = run_gate(reply, sources, temperature=cfg.temperature,
                      judge_fn=judge_claims, rewrite_fn=demote_to_question,
                      corpus=RegressionCorpus(_CORPUS_DIR), user_id=user_id, thread_id=thread_id)
    return result.message
```

- [ ] **Step 4: PASS.**
- [ ] **Step 5: Wire into the coach** — find where the reply is finalized in `agent/service.py`; import `from eval.grounding.integration import maybe_gate`; wrap the final reply: `reply = maybe_gate(reply, sources=<retrieved-as-Source>, user_id=user_id, thread_id=session_id)`. Map the coach's retrieved chunks → `Source(id, tier=1, kind="vault_entry", text=...)`. Guard the import so a failure can't crash the live turn (try/except → fall back to ungated reply, log a warning). **Do not** change streaming behavior when the flag is OFF.
- [ ] **Step 6: Run the full coach test suite** — `PYTHONPATH=services/coach python3 -m pytest services/coach/tests/ -q`. Expected: all green (flag defaults OFF ⇒ existing behavior unchanged).
- [ ] **Step 7: Commit + push** — `feat(coach): grounding gate in send-path behind COACH_GROUNDING_GATE (default off)`.

---

## Task 14: Wake-up artifacts

**Files:** Create `services/coach/eval/grounding/README.md`, `WAKEUP.md` (run summary), `CALIBRATION_TODO.md` (human task)

- [ ] **Step 1:** `README.md` — how the package fits together, how to run the harness, the flag, the corpus location.
- [ ] **Step 2:** `CALIBRATION_TODO.md` — the ~20–40 borderline-interpretation claims for Gonzalo to label on waking (template table with columns: claim, source, your-verdict). Pre-fill 6–8 borderline examples drawn from the fixtures.
- [ ] **Step 3:** `WAKEUP.md` — what was built, test counts, calibration result on f000, what's NOT done (live deploy, human calibration set), recommended next step.
- [ ] **Step 4: Commit + push** — `docs(grounding): readme + wakeup + human calibration task`.

---

## Self-Review checklist (run before execution)
- [ ] Spec coverage: judge ✓(T7) gate ✓(T6,T10) hard-floor ✓(T6) temp band ✓(T6) citation parse ✓(T5) corpus ✓(T9) fixtures ✓(T11) harness ✓(T12) subscription ✓(T1,T2) flag integration ✓(T13). Promotion/absorption: config knob ✓(T4); detection deferred to Stream 2 (boundary, §12). Calibration human-set ✓(T14).
- [ ] No placeholders: all steps have concrete code + commands.
- [ ] Type consistency: `Claim`, `Verdict`, `Decision`, `Action`, `Source`, `GateResult`, `run_gate`, `judge_claims`, `decide`, `demote_to_question`, `segment`, `load_config`, `RegressionCorpus` — names consistent across tasks.

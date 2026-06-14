# Grounding Gate (Stream 0)

Per-claim grounding for the coach: **assertions about you must cite an entailing
source or be demoted to a question; interpretations are throttled by a
temperature knob.** Catches confabulations like the PoC's "you were punishing
yourself" before they reach you.

Spec: `docs/superpowers/specs/2026-06-05-grounding-gate-precision-eval-design.md`
Plan: `docs/superpowers/plans/2026-06-05-grounding-gate-precision-eval.md`

## Pieces

| Module | Role |
|--------|------|
| `llm.py` | Subscription LLM seam — `claude -p --output-format json` (no API key). |
| `types.py` | `ClaimType`, `Claim`, `Source`, `Verdict`, `Decision`, `GateResult`. |
| `config.py` | `temperature` + `absorption` knobs (env, 1–10, default 5). |
| `segmenter.py` | Parse a `[[claim ...]]`-tagged draft → claims + passthrough. |
| `judge.py` | Re-derive each claim's type + check grounding (one batched LLM call). |
| `decide.py` | **Pure** routing: hard floor (facts) + temperature band (interpretations). |
| `rewriter.py` | Demote a blocked assertion → an open question. |
| `corpus.py` | Regression corpus (jsonl + md): catches, corrections, validations. |
| `gate.py` | Orchestrates segment → judge → decide → rewrite → log → reassemble; opens pending validations. |
| `harness.py` | Offline precision harness over `cases/*.json`. |
| `integration.py` | `maybe_gate(...)` + `detect_validation_signals(...)` adapters for the coach send-path (flag-gated). |
| `cases/` | Synthetic self-labeling fixtures (planted allow/deny lists). |
| `validations.py` | Pending-validation store (Stream 2): the coach's open questions/reads per thread. |
| `validation_detector.py` | Semantic match of a user reply against open validations → confirmed / corrected / unaddressed. |
| `validation_harness.py` | Detector calibration over `validation_cases/cases.json` — live 12/12, see `VALIDATION_DETECTOR_CALIBRATION.md`. |

## The claim contract

The coach emits each claim tagged (adopted in Stream 2):

```
[[claim id=c1 type=fact cite=S1]] You missed Thursday and Friday.
[[claim id=c2 type=interpretation conf=7]] This reads like self-punishment.
```

`type` ∈ {fact, interpretation, conceptual}; `cite` required for fact/conceptual;
`conf` 1–10 required for interpretation. Untagged lines pass through.

**Stream 2 (built, flag-gated):** with `COACH_GROUNDING_GATE=1` the coach's
system prompt appends `coach_prompt_claims.md` (the tagging contract — `cite=S1`
= the supplied vault context), retrieval plumbs real chunk text into the judge's
S1 source, the gate opens a pending validation for every demoted question and
stated read, and each INCOMING turn is matched against the thread's open items
(`detect_validation_signals`): confirmations log a `validation` signal,
corrections a `correction` signal (kept in the user's own words), both close the
item. Flag OFF → all of it is a no-op and the prompt is byte-identical to
before. Detector calibration: 12/12 live
(`VALIDATION_DETECTOR_CALIBRATION.md`). Design:
`docs/superpowers/specs/2026-06-10-stream2-validation-loop-design.md`.

## Stream 3 (built, flag-gated): doctrine vs. memory

The gate originally treated "grounded in the vault" as sufficient warrant for any
fact — so it would *bless* the coach pulling a biographical fact from the vault
and asserting it about whoever it's talking to (the "four months of recovery"
leak). Stream 3 splits the coach's knowledge into two sources with different
warrants, and makes the gate enforce the split:

- **Doctrine (`D1`, kind `doctrine`, tier 2):** distilled, de-personalised
  principles (`doctrine/` package — `distill.py` + `build_corpus.py`), retrieved
  into a `<doctrine>` block. Grounds **conceptual/principle** claims only.
- **Memory (`M1`, kind `memory`, tier 1):** conversation-only per-user facts
  (mem0, `agent/memory.py`), retrieved into `<about_this_user>`. The ONLY source
  that can ground a **fact about the user**.

`decide()` runs in *typed mode* whenever a `doctrine`/`memory` source is present:
a FACT must be entailed by a MEMORY source (else demote); a CONCEPTUAL claim may
be entailed by doctrine. Untyped sources keep legacy behaviour. The claim
contract (`coach_prompt_claims.md`) now cites `M1` for facts, `D1` for principles.

**Deploy prerequisite:** build the doctrine corpus + index BEFORE enabling, else
`<doctrine>` is empty and the coach is ungrounded:
```
# 1. distill raw vault -> clean principle corpus (subscription LLM)
COACH_VAULT_PATH=/vault COACH_DOCTRINE_CORPUS_DIR=/data/doctrine_corpus \
  python -m doctrine.build_corpus
# 2. index the clean corpus (OpenAI embeddings)
python -c "from pathlib import Path; from vault_rag.builder import build_index; \
  build_index(Path('/data/doctrine_corpus'), Path('/data/doctrine_index'))"
# 3. set COACH_DOCTRINE_INDEX_DIR=/data/doctrine_index, then enable the gate
```
Design: `docs/superpowers/specs/2026-06-14-doctrine-memory-separation-design.md`.

## Run the harness (real subscription judge)

```bash
PYTHONPATH=services/coach python3 -m eval.grounding.harness --temperature 6
```

Latest calibration: `CALIBRATION.md` (8/8, f000 demotes "punishing yourself").

## Run the tests (offline, monkeypatched LLM)

```bash
PYTHONPATH=services/coach python3 -m pytest services/coach/tests/test_grounding_*.py services/coach/tests/test_eval_*.py -q
```

(Service-level tests need `fastapi`/`claude_agent_sdk` and `importorskip` on the
host — they run in docker/CI.)

## Enabling in the coach

Off by default. To turn the gate on for a turn:

```
COACH_GROUNDING_GATE=1          # 1|true|on|yes
COACH_GROUNDING_TEMPERATURE=6   # optional, 1-10
COACH_GROUNDING_ABSORPTION=5    # optional, 1-10
COACH_GROUNDING_CORPUS_DIR=/data/grounding_corpus
```

When on, `/turn` gates the reply and `/turn/stream` buffers → gates → sends. Any
gate error falls back to the ungated reply (never breaks a live turn).
**Do not enable in prod until** the coach emits the claim contract (Stream 2) and
the human calibration set (`CALIBRATION_TODO.md`) validates the judge on
borderline cases.

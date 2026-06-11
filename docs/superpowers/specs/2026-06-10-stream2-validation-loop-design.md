# Stream 2 (slice 1): validate-then-analyze — the validation loop

**Date:** 2026-06-10 · **Status:** building (overnight, autonomous)
**Parent spec:** `2026-06-05-grounding-gate-precision-eval-design.md` (§B, §C, §10)
**Driving finding:** `services/coach/eval/grounding/CALIBRATION_RESULT_V2.md` — the
gate is safe but structurally over-hedged; the ~6% lived-contradiction bucket and
the endorsement signal that could unlock plain-spoken asserts both belong to the
validation loop. This slice builds that loop.

## What Stream 2 owns (from the parent spec)

1. Citation-bearing coach output — the `[[claim ...]]` contract (§2.4)
2. Send-path draft → gate → send (plumbing landed in Stream 0, flag-gated)
3. **Validation/correction-signal detection** on user replies — semantic match,
   not claim-ID reference (§276: "the user won't have context of the linkage")
4. Proactive reach-out + thread creation
5. Promotion/correction signals logged with provenance (storage = Stream 3)

## This slice (overnight scope)

Builds 1, 3, 5 + the missing source-text plumbing. Defers 4 (proactive/threads —
blocked on Stream 1's debrief artifact as trigger input) and the assert-upgrade
from endorsements (needs Stream 3 storage; design note below).

### A. Pending-validation store — `eval/grounding/validations.py`

When the gate demotes a claim to a question, or states it as a read, the coach has
implicitly *asked* something. That open question must be remembered, or the user's
answer is signal lost on the floor — exactly what happened with the PoC
confabulation. jsonl store, same pattern as `corpus.py` (caller supplies ts).

Record: `{ts, thread_id, user_id, claim_id, claim_text, claim_type, confidence,
action: demote|state_as_read, question, status: open|confirmed|corrected|expired,
resolved_ts, resolution_note}`.

API: `PendingValidations(dir)` · `.open(record)` · `.list_open(thread_id)` ·
`.resolve(thread_id, claim_id, *, status, ts, note)`.

### B. Validation detector — `eval/grounding/validation_detector.py`

`detect(user_text, open_items, *, llm_fn=call_llm)` → per open item:
`confirmed | corrected | unaddressed` (+ `correction_text` extracted verbatim-ish
when corrected). One batched subscription LLM call (`claude -p`), JSON out,
defensive parse (malformed → all unaddressed; never breaks the turn). Semantic
matching is THE riskiest assumption of Stream 2 → fixture cases + live-judge run
tonight (same evals-first pattern as Stream 0's `cases/` + 8/8 calibration).

Outcomes feed the corpus: `confirmed` → `validation` signal (the future
endorsement/promotion input), `corrected` → `correction` signal (the
contradiction bucket, captured live with the user's words).

### C. Gate records opens — `gate.py`

`run_gate(..., validations=None, ts="")`: DEMOTE → open with the question;
STATE_AS_READ → open with the stated read (a read invites confirmation too — v2
calibration showed these are the cases where his answer carries the signal).
ASSERT opens nothing.

### D. Wiring — `integration.py` + `agent/service.py`

- `maybe_gate(...)` gains the validations store (env `COACH_VALIDATIONS_DIR`,
  default alongside corpus) and stamps ts at the service boundary (runtime owns
  the clock; eval modules never invent time — corpus principle).
- New `detect_validation_signals(user_text, *, thread_id, user_id)` in
  `integration.py`: list opens → detect → log corpus signals → resolve items.
  Same fail-safe contract as `maybe_gate`: any error → log + no-op, never breaks
  a live turn. Called from `/turn` and `/turn/stream` on the INCOMING text,
  before the agent runs, only when the gate flag is on.
- Service logic stays thin (host can't run fastapi tests); all logic lives in
  eval/grounding modules, unit-tested offline.

### E. Claim contract emission — `coach_prompt_claims.md`

Appended to the system prompt in `_build_agent_options` ONLY when
`gate_enabled()`. Flag off (prod default) → prompt byte-identical to today, no
tags ever leak. Tags are internal: segmenter strips them, gate reassembles clean
text. Contract: `[[claim id=cN type=fact cite=S1]]` / `type=interpretation
conf=1-10` / `type=conceptual cite=S1`; untagged lines pass through.

### F. Source-text plumbing (fixes Stream 0 debt)

`service._slugs_to_sources` currently builds Sources with `text=""` — the judge
cannot check entailment against an empty string. Fix: `retrieval.py` exposes the
retrieved chunks' text (slug → text map) alongside the context block; service
threads the map into the Sources. Tool-result path (`_extract_source_slugs`)
extends best-effort: capture `text` fields when present.

## Explicitly deferred

- **Proactive reach-out + threads** — needs Stream 1's debrief artifact as the
  trigger. Thread store design folds into that slice.
- **Assert-upgrade from endorsements** (the v2 action-space lever): `validation`
  corpus records accumulate from this slice; Stream 3 turns them into stored,
  provenance-bearing endorsements that `decide()` can consult. Do NOT shortcut
  by tuning `decide()` on the n=18 calibration (overfit).
- **Feeding detection outcomes into the turn prompt** (coach explicitly
  acknowledges a correction). The coach sees the user's words naturally; an
  explicit acknowledgment block is a prompt change worth its own eval.

## Safety

Everything lands behind `COACH_GROUNDING_GATE` (default OFF). No deploy (user-
gated). Any failure in store/detector/gate → ungated, un-detected turn — never a
broken one.

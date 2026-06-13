# Wakeup — Stream 2 slice 1 built overnight (2026-06-10 → 11)

You said "proceed with stream 2 while I go to sleep." Here's what exists now.

## TL;DR

The **validate-then-analyze loop is built, tested, and calibrated** — behind
`COACH_GROUNDING_GATE` (default OFF, nothing deployed, prod untouched):

1. Gate on → coach emits `[[claim]]` tags (contract appended to its system
   prompt), gate verifies each claim against REAL retrieved chunk text (the
   empty-text debt is fixed), demotes/states-as-read as calibrated.
2. Every demoted question + stated read is remembered as an **open validation**
   for the thread.
3. Your NEXT message is semantically matched against those open items:
   agreement → `validation` signal; pushback → `correction` signal **in your
   own words**; off-topic → item stays open. This is the loop that owns the
   ~6% lived-contradiction bucket from the v2 calibration — and accumulates
   the endorsement evidence that can later unlock plain-spoken asserts.

## Detector calibration (the riskiest assumption): 12/12 live

`VALIDATION_DETECTOR_CALIBRATION.md`. Hard cases all caught: "indeed… but
actually…" → correction; correction with no negation words; enthusiasm about an
unrelated part of the reply → stays open. Hedges ("hmm, maybe") stay open by
design. Corrections seeded from your real calibration answers.

## Where it lives

- Branch **`stream2-validation-loop`** (off main, PR open) — includes the v2
  calibration commits cherry-picked from the old worktree branch. The old
  branch is untouched as archive.
- Design: `docs/superpowers/specs/2026-06-10-stream2-validation-loop-design.md`
- New: `validations.py`, `validation_detector.py`, `validation_harness.py`,
  `validation_cases/cases.json`, `coach_prompt_claims.md`, `agent/prompts.py`
- Touched: `gate.py` (opens validations), `integration.py` (wiring + ts),
  `service.py` (incoming detection, contract append, source-text), `retrieval.py`
  (`retrieve_for_turn_rich`)

## Verified

- Host: 82 passed / 2 skipped (grounding + plumbing)
- **Docker full coach suite: 181 passed / 3 skipped** (service-level included)
- Live detector calibration: 12/12 (subscription, sonnet)
- Flag OFF safety: prompt byte-identical, all hooks no-op (existing
  flag-off tests still green)

## Deliberate cuts (documented in the design doc, not silent)

- **Proactive reach-out + threads** — blocked on Stream 1's debrief artifact as
  the trigger; folds into that slice.
- **Assert-upgrade from endorsements** — needs Stream 3 storage; `validation`
  records accumulate from day one so the evidence will be there.
- **Tool-result chunk text** (search_vault mid-turn) — only pre-retrieved
  chunks carry text for the judge so far.
- **Coach acknowledging corrections explicitly in-prompt** — worth its own eval.

## Your decisions when you're up

1. Review/merge the PR (merge is yours, as always).
2. When to flip `COACH_GROUNDING_GATE=1` in a test env — the contract +
   loop are ready for a supervised live conversation.
3. Whether Stream 1 (grounded debrief) or a live gate trial comes next.

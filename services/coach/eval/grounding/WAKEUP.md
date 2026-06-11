# Good morning — Stream 0 overnight build summary

**Date:** 2026-06-05 (autonomous run while you slept)
**Branch:** `worktree-audio-feedback-loop-poc` (pushed; PR #52)

## What got built

The **grounding gate + precision eval** (Stream 0), TDD, all on your Max
subscription (no API key). Package: `services/coach/eval/grounding/`.

The whole point in one line: **the coach can no longer assert a fact about you
that isn't in its sources — "you were punishing yourself" gets demoted to a
question instead of stated.**

- 14 tasks, ~13 commits, one per green step.
- **42 tests pass + 2 skipped** on host (`pytest test_grounding_*.py test_eval_*.py`).
  The 2 skips are the service-level tests — they need `fastapi`/`claude_agent_sdk`
  (docker/CI only) and `importorskip` on the host.
- **Calibration: 8/8, agreement 1.00 with the live subscription judge** — including
  `f000`, which reconstructs the exact PoC hallucination. The judge ruled
  "punishing yourself" ungrounded and the gate demoted it. See `CALIBRATION.md`.
- Existing `eval/judge.py` migrated to the subscription seam too (was API key).

## What's live vs inert

- The gate is wired into the coach send-path **behind `COACH_GROUNDING_GATE`
  (default OFF)**. With the flag off, the coach behaves exactly as before.
- **I did NOT deploy or restart the coach.** Code is on the branch only.
- Even with the flag ON, it's a **safe no-op today**: the coach doesn't yet emit
  the `[[claim ...]]` tags the gate reads (that's the Stream-2 output contract), so
  there are no claims to gate → reply passes through unchanged.

## What's NOT done (by design / needs you)

1. **Human calibration set** — `CALIBRATION_TODO.md`. ~8 borderline interpretation
   cases for you to label so we can verify the judge agrees with *you* on the fuzzy
   ones before trusting the gate live. ~10 min.
2. **Stream 2** makes it actually fire: coach prompt emits the claim contract
   (`type`/`cite`/`conf`/`id`), retrieved chunk **text** is plumbed to the judge (today
   only slugs reach it — flagged in `service.py`), proactive reach-out, thread
   creation, and validation/correction-signal detection (promotion/absorption).
3. **Deploying** the gate — separate supervised step, your call.

## Recommended next step

Spend 10 min on `CALIBRATION_TODO.md`, then we start **Stream 1 (grounded debrief)**
or **Stream 2 (coach output contract)** — Stream 2 is what flips this from inert to
live, so it's the natural follow-on.

## Map

- Spec: `docs/superpowers/specs/2026-06-05-grounding-gate-precision-eval-design.md`
- Plan: `docs/superpowers/plans/2026-06-05-grounding-gate-precision-eval.md`
- Package README: `services/coach/eval/grounding/README.md`
- Calibration result: `services/coach/eval/grounding/CALIBRATION.md`

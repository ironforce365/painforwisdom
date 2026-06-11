# Validation detector — live calibration (Stream 2 riskiest assumption)

**Run:** 2026-06-10 (overnight) · `python -m eval.grounding.validation_harness`
**Detector:** `claude -p --model claude-sonnet-4-6` (subscription), one batched
call per turn · **Cases:** `validation_cases/cases.json` (10 cases / 12 items;
corrections built from Gonzalo's REAL calibration corrections)

## Result: 12/12

| case | tests | got |
|---|---|---|
| v01 explicit confirm | "yes, exactly" | confirmed ✓ |
| v02 substance confirm, no "yes" | restates read in own words | confirmed ✓ |
| v03 real correction (foot-in-the-door, his words) | corrected ✓ + extraction |
| v04 **agree-then-redirect** ("indeed… but the goal was…") | corrected ✓ |
| v05 topic change | unaddressed ✓ |
| v06 partial: 2 items, reply hits one | confirmed + unaddressed ✓ |
| v07 mixed in one reply | confirmed + corrected ✓ |
| v08 ambiguous hedge ("hmm, maybe") | unaddressed ✓ (conservative bias holds) |
| v09 **correction without negation words** | corrected ✓ |
| v10 enthusiasm about untracked text | unaddressed ✓ |

Correction extraction stays close to the user's words (v03/v04/v07/v09) — usable
directly as `user_correction` in the corpus and later as Stream 3 memory input.

## Why this was the riskiest assumption

The user never references claim IDs (parent spec §276) — if semantic matching
were unreliable, the whole validate-then-analyze loop would mis-log signals or
close items on the wrong evidence. The hard cases (v04 agree-then-redirect, v09
no-negation correction, v10 enthusiasm-about-other-text) are exactly the
failure shapes that would poison the corpus. All caught.

## Caveats

- n=12 items, synthetic replies (corrections seeded from his real words, but
  replies composed). The live loop will produce real pairs; the corpus accretes
  them as regression cases.
- Conservative bias confirmed (hedges stay open) — by design: a wrongly-closed
  item is worse than one asked again.

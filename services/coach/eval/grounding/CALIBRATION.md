# Grounding Judge — Calibration

**Run:** 2026-06-05 (overnight autonomous build, Stream 0)
**Command:** `PYTHONPATH=services/coach python3 -m eval.grounding.harness --temperature 6`
**Judge backend:** `claude -p --model claude-sonnet-4-6` (Max subscription, no API key)

## Result

```
f000_punishing_yourself: 2/2 agreement=1.00 mismatches=[]
f001_grounded_facts:      3/3 agreement=1.00 mismatches=[]
f002_confident_read:      3/3 agreement=1.00 mismatches=[]
OVERALL: 8/8 agreement=1.00
```

## What this means

- **The acceptance gate passes:** `f000` reconstructs the PoC react-audio hallucination
  ("you were out there running the math of the miles you didn't log, punishing yourself").
  The source plants the missed runs + heaviness but omits any emotional cause. The live
  judge ruled the self-punishment claim an **ungrounded fact** and the gate **demoted it to
  a question** — the exact failure that motivated this stream is now caught automatically.
- `f001` confirms grounded facts (all cited + entailed) are asserted, not over-blocked.
- `f002` confirms the temperature band: a confident interpretation (conf 9) is stated as the
  coach's read; a shaky one (conf 2) is demoted — at temperature 6.

## Caveats / next

- Judge accuracy ≥ ~90% on the **synthetic** fixtures (self-labeling). Still owed:
  the small **human-labeled** borderline set (see `CALIBRATION_TODO.md`) to validate the
  judge on the fuzzy interpretation cases the fixtures can't cover before the gate is
  trusted live.
- `f002` confidences are deliberately far from the boundary (9 / 2); add boundary-hugging
  fixtures (conf 5/6/7 at temp 6) once the human set exists, to probe the temperature edge.
- Re-run this harness after any change to `judge._SYSTEM`; it must stay 1.00 on these fixtures
  (they are now regression fixtures).

# Judge baseline — vault calibration set (before human labels)

**Run:** 2026-06-05 · `python -m eval.grounding.score_calibration --temperature 6`
**Judge:** `claude -p --model claude-sonnet-4-6` (subscription)

## Distribution at temperature 6 (20 cases)

| Judge action | n | which |
|---|---|---|
| `state_as_read` | 14 | most interpretations (conf ≥ 6) |
| `demote` | 4 | #2, #9, #14, #15 — incl. the **no-story-anchor** entries (#9, #15), correctly ungrounded |
| `assert` | 2 | #7, #17 — grounded facts |

## The signal to resolve with your labels

On the **manufactured** set (`CALIBRATION_TODO.md`) you labeled most conf 5–7
interpretations as *"need more data / would need to inquire"* — i.e. **demote**.
But at temperature 6 the judge calls most of those **state_as_read** (it treats a
confident-enough read as fair to state as the coach's read).

**Your bar looks stricter than temp 6.** Two levers, and your vault labels decide:

1. **Raise the temperature** (e.g. 8–9) so more interpretations fall below it and
   get demoted to questions. The `decide()` logic already supports this — it's a
   config change, no code.
2. **Reframe `state_as_read`** so that even a stated read invites confirmation
   ("my read is X — does that land?"), collapsing the gap between "state" and "ask".
   This is a rewriter/prompt change.

## Next step

Fill the **Your verdict** column in `CALIBRATION_VAULT.md` (`assert` /
`state_as_read` / `demote`), then re-run:

```
PYTHONPATH=services/coach python3 -m eval.grounding.score_calibration --temperature 6
```

It prints per-case agreement (✓/✗) + an overall number. We sweep temperature and
tune `judge._SYSTEM` until agreement clears ~90%, then lock the default temperature.

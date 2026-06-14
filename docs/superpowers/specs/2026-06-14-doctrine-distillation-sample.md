# Doctrine distillation — live sample validation (2026-06-14)

Real subscription run (`claude -p`, sonnet) of `doctrine.distill.extract_with_stats`
on 3 representative vault files. Validates the riskiest assumption: **can we
distill clean, de-personalised doctrine from raw (often first-person) entries?**
Answer: **yes.** Every kept principle passed the QA gate (no `<<< LEAK!`).

## File 1 — `thoughts/2026-05-09-04-pain-is-the-currency-of-growth.md` (17KB, dense first person)
The hardest case: heavy autobiography ("what happened to me… my mid-30s, 34, 35",
his sub-4 marathon goal). Result: **9 principles kept, 1 QA-rejected.** Biography
fully stripped; wisdom preserved. Examples:
- *Distinguish growth from progress: progress is advancing along a pre-set
  trajectory; growth is changing the trajectory itself…*
- *Two structurally different kinds of pain… currency pain (price of movement) vs
  expectation-reality contrast pain (price of disappointment)… each requires
  different preparation.*
- *The force that sustains a trajectory change is not motivation but an intrinsic
  drive… Build for drive, not for motivation.*

The "mid-30s career change" and "sub-4 marathon" specifics — exactly the kind of
biography that leaked as "four months of recovery" — did NOT survive. They became
impersonal principles. This is the fix working at the source.

## File 2 — `deep-dive/.../voluntary-discomfort.../theory.md` (5.5KB)
**8 principles kept, 0 rejected.** E.g. *The only mechanism that updates a
catastrophic prior is direct, real inhabitation of the feared state…*

## File 3 — `gonzalo-book/entries/2026-04-29-name-the-adaptive-purpose.md` (2.7KB)
**4 principles kept, 0 rejected.** E.g. *Before adding an environmental stressor,
name one specific adaptation you are targeting… else the stressor is decorating
an identity, not building a capacity.*

## Takeaways
- 21 clean principles from 3 files; 0 leaks past the gate.
- Quality is genuinely usable as coaching doctrine — keeps Gonzalo's distinctive
  framing ("currency pain", "asymmetric return", "name the adaptation") while
  dropping the personal history.
- The deep-dives were NOT clean at source (as Gonzalo said), but distillation
  cleans them. Confirms: distill, don't just reindex a subset.

## Full-corpus build (operational step, not committed — derived artifact)
~280 source files across the 5 dirs. Run in the coach image (has OPENAI key for
the index embeddings):
```
PYTHONPATH=/app COACH_VAULT_PATH=/vault \
  COACH_DOCTRINE_CORPUS_DIR=/data/doctrine_corpus \
  python -m doctrine.build_corpus
# then index the clean corpus:
python -c "from pathlib import Path; from vault_rag.builder import build_index; \
  build_index(Path('/data/doctrine_corpus'), Path('/data/doctrine_index'))"
```
Forecast: ~280 sonnet calls (~430K input / ~170K output tokens) + ~280 small
embeddings. Modest on the Ultra plan; wall-clock ~serial latency.

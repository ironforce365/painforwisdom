# Wakeup — doctrine vs. memory built overnight (2026-06-14)

You flagged the bug: the coach pulled "four months of recovery" from your vault
and asserted it about the user — treating the vault (doctrine) as the user's
memory. You asked for the structural fix: real distillation, conversation-only
memory, no same-day mitigation. **It's built, tested, and on a branch. PR open.
Merge + deploy are yours.**

## What it does now (flag ON)

1. **Doctrine, not journals.** The coach no longer retrieves your raw vault. A
   distillation pipeline turns vault entries into **de-personalised principles**
   (biography stripped) → a `<doctrine>` block. Grounds *principles* only.
2. **Memory = conversation only.** mem0 (already deployed, was dormant) is now
   read before the turn (`<about_this_user>`) and written from your OWN words
   after it. It's the ONLY source for facts about the person.
3. **The gate enforces the split.** A FACT about the user is asserted only if a
   MEMORY source entails it; a fact only doctrine supports is **demoted to a
   question**. "Four months of recovery" → "Has a flare like this cost you a long
   recovery before?" — proven end-to-end in a test.

Flag OFF → byte-identical to today (raw vault, no mem0, no gate).

## The riskiest assumption — validated live

Distillation on 3 real files (incl. your dense first-person "pain is the currency
of growth") → 21 clean principles, **zero biography leaks** past the QA gate.
Sample: `2026-06-14-doctrine-distillation-sample.md`. The deep-dives weren't clean
at source (as you said) — distillation cleans them.

## Verified

- Source-typed gate + biography-leak regression: green.
- **Full docker coach suite: 219 passed / 3 skipped, zero regressions.**
- Host-safe set: 110 passed / 2 skipped.

## BEFORE you enable the flag on this branch (required)

The coach now grounds on a doctrine **index** that must exist first, or
`<doctrine>` is empty and it's ungrounded. In the coach image:
```
COACH_VAULT_PATH=/vault COACH_DOCTRINE_CORPUS_DIR=/data/doctrine_corpus \
  python -m doctrine.build_corpus           # distill (~280 files, subscription)
python -c "from pathlib import Path; from vault_rag.builder import build_index; \
  build_index(Path('/data/doctrine_corpus'), Path('/data/doctrine_index'))"
# set COACH_DOCTRINE_INDEX_DIR=/data/doctrine_index in .env.coach
```
(I ran the distill on the host overnight to validate — see the PR for the corpus
count. The prod index build is the one operational step left, and it's yours
because it writes to the prod data volume.)

## Deliberate cuts (not silent — see design §8)

- **`search_vault` still hits the raw vault.** The gate demotes any tagged fact
  only the raw vault supports, but raw biography could still bleed into untagged
  text if the model calls the tool. Follow-up: repoint it to doctrine under the
  flag.
- mem0 extraction quality not eval'd yet.
- Flag-on prompt still carries the legacy `<vault_context>` paragraph (overridden
  by the appended contract) to keep flag-off byte-identical.

## Files

- Design: `2026-06-14-doctrine-memory-separation-design.md` (+ assumptions §7)
- New: `doctrine/distill.py`, `doctrine/build_corpus.py`, `agent/memory.py`
- Touched: gate `types/decide/judge/gate.py`, `agent/retrieval.py`,
  `agent/service.py`, `coach_prompt_claims.md`
- Tests: `test_grounding_source_typed.py`, `test_agent_user_memory.py`,
  `test_doctrine_*.py`, `test_agent_doctrine_memory.py`

## Your decisions

1. Review/merge the PR.
2. Build the doctrine index (above), set `COACH_DOCTRINE_INDEX_DIR`, deploy.
3. Note: prod is currently on v0.1.5 with the gate ON but the OLD (leaky,
   over-hedged) behavior. This branch is the fix — until it ships, prod can still
   leak vault biography. If that matters tonight, `COACH_GROUNDING_GATE=0` in
   `painforwisdom-live/.../.env.coach` + `up -d coach-agent` reverts to plain.

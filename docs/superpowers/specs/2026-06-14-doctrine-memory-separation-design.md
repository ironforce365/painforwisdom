# Doctrine vs. Memory — design + build (overnight 2026-06-14)

**Status:** building autonomously while Gonzalo sleeps. This doc is the anchor +
the assumptions log. Decisions he confirmed are in §0; assumptions I made on my
own are flagged **[ASSUMPTION]** throughout and collected in §7.

Companion brainstorm: `2026-06-13-doctrine-vs-memory-separation-brainstorm.md`.

## 0. Confirmed decisions (from Gonzalo)

1. **Real distillation.** Deep-dives are NOT clean doctrine → we extract
   de-personalized principles, we don't just reindex a subset.
2. **Memory = conversation only.** The coach remembers the person it's talking to
   using ONLY what was said in conversation with that person. Nothing vault-seeded,
   for anyone (including Gonzalo). No opt-in vault import (M2 dropped).
3. **No same-day mitigation.** One structural fix, shipped as a piece.
4. Merge + deploy remain Gonzalo's. I build on a branch, open a PR, touch no prod.

## 1. The invariant we are enforcing

> A claim of the form "**you** did/felt/experienced X" may be asserted only if X
> is entailed by **conversation-derived user memory**. The doctrine corpus may
> only ground "**the pattern/lesson here is** Y" (principles) — never biography
> about the interlocutor.

The "four months of recovery" leak violated this: a biographical fact warranted
by the vault (doctrine), asserted about the user. The shipped gate blesses it
because its rule is "fact must have *a* vault source." We sharpen that to "a
fact about the user must have a *memory* source."

## 2. Target architecture

Two retrieval sources per turn, distinct provenance and warrant:

| Block | Source id | Source kind | tier | Built from | Warrants |
|---|---|---|---|---|---|
| `<doctrine>` | `D1` | `doctrine` | 2 | distilled de-personalized principles | conceptual/principle claims |
| `<about_this_user>` | `M1` | `memory` | 1 | this user's prior conversation (mem0) | facts about the user |

- The **raw vault is never retrieved at coach-time** anymore. It is only the
  *input* to distillation.
- mem0 (already deployed, per-user, currently dormant) becomes the live memory
  store: read before the turn, written after — conversation only.
- The grounding gate becomes **source-kind aware**: facts must be entailed by a
  `memory` source; conceptual may be entailed by a `doctrine` source.

All of this rides the existing `COACH_GROUNDING_GATE` flag (the "new coaching
epistemics" switch). **Flag OFF → byte-identical legacy behavior** (raw vault
retrieval, no mem0, no gate). Flag ON → the new world. **[ASSUMPTION]** one flag
governs all three capabilities because they are mutually dependent (the typed
gate needs D+M sources; D needs the doctrine index; M needs mem0). Splitting
them would allow incoherent half-states.

## 3. Components & files

### 3a. Source-typed gate (enforcement) — `eval/grounding/`
- `types.py`: add `Verdict.grounded_by: list[str]` (source ids that entail the
  claim); add kind constants `KIND_DOCTRINE="doctrine"`, `KIND_MEMORY="memory"`.
- `judge.py`: prompt also returns `grounded_by` (which cited sources entail);
  parse it. `grounded` stays = `bool(grounded_by)` for fact/conceptual.
- `decide.py`: new optional `source_kinds: dict[str,str]` param.
  - FACT: assert iff some entailing source is `memory`; else demote.
  - CONCEPTUAL: assert iff entailed by any source; else demote.
  - INTERPRETATION: temperature band (unchanged).
  - `source_kinds=None` → legacy behavior (preserves existing tests).
- `gate.py`: build `source_kinds` from `sources`, pass to `decide`.

### 3b. User memory (conversation-only) — `agent/memory.py` (new, host-testable)
- `read_user_memory(user_id, query, *, client, limit) -> (block, source_text)`:
  mem0 search → `<about_this_user>` block + concatenated text for the M1 source.
- `write_user_memory(user_id, user_text, *, client)`: `client.add(user_id,
  user_text)` — **only the user's own words**, never the coach's reply
  (**[ASSUMPTION]**: feeding the coach's inferences back as "memory" would
  re-introduce the contamination; mem0 internally extracts durable facts).
- `format_about_user(memories) -> str`. Failures degrade to empty (never break a
  turn).

### 3c. Doctrine distillation — `doctrine/` (new)
- `distill.py`: `extract_principles(text, *, llm_fn, model) -> list[Principle]`.
  LLM prompt: extract transferable principles in impersonal/second-person form,
  NO first person, NO names, NO dated events, NO biography. `Principle{id, text,
  theme}`. `is_depersonalized(text) -> bool`: rejects first-person/biographical
  markers (`I `, `my `, `me `, `Gonzalo`, Spanish `yo `, `mi `, dates). QA filter
  drops principles that fail the check.
- `build_corpus.py`: walk source dirs → distill each file → write the doctrine
  corpus as `.md` files (so the **existing** `vault_rag.builder` can index it
  unchanged — doctrine is "just a clean vault"). CLI: `python -m
  doctrine.build_corpus`. Index → `COACH_DOCTRINE_INDEX_DIR`.
  - **[ASSUMPTION]** source dirs to distill: `gonzalo-book/deep-dive`,
    `gonzalo-book/themes`, `gonzalo-book/frameworks`, `thoughts`,
    `gonzalo-book/entries`. Excludes `_inbox` (coach's own logs) and bibliography
    files. Configurable via `COACH_DOCTRINE_SOURCE_DIRS`.

### 3d. Retrieval split — `agent/retrieval.py`
- `retrieve_doctrine_for_turn(text) -> (block, source_text)`: same retriever
  stack, index at `COACH_DOCTRINE_INDEX_DIR`, renders `<doctrine>`.
- Legacy `retrieve_for_turn_rich` (raw vault) kept for flag-off.

### 3e. Contract + prompt — `coach_prompt_claims.md`, `coach_prompt.md`
- Claims: `type=fact` → `cite=M1` (memory); `type=conceptual` → `cite=D1`
  (doctrine); `type=interpretation` → `conf`. Facts may ONLY cite M1.
- System prompt: doctrine is *teaching material* (someone else's distilled
  lessons); the only facts you know about this person are in `<about_this_user>`
  + this conversation; never attribute doctrine content to the user as biography.

### 3f. Service wiring — `agent/service.py`
- Flag ON: `_compose_turn_prompt` injects `<doctrine>` (D1) + `<about_this_user>`
  (M1); `_slugs_to_sources` → `[Source(D1,doctrine,tier2), Source(M1,memory,
  tier1)]`; after the turn, `write_user_memory`. Flag OFF: unchanged.

## 4. Test plan (TDD)

- decide: fact grounded only by doctrine → DEMOTE; fact grounded by memory →
  ASSERT; conceptual grounded by doctrine → ASSERT; legacy (no kinds) unchanged.
- judge: parses `grounded_by`; offline monkeypatched.
- gate: end-to-end with a doctrine-only fact → demoted question; **regression
  test reproducing "four months"** (fact, cite vault/doctrine only → demoted).
- memory: read formats block + source; write feeds only user text; failures
  degrade.
- distill: extraction shape; `is_depersonalized` rejects first-person;
  build_corpus writes clean `.md`, drops contaminated principles.
- service plumbing (docker): flag-on injects both blocks + builds D/M sources +
  writes memory; flag-off byte-identical.

## 5. Build order

1. Gate source-typing + regression (host). ← highest correctness leverage
2. Memory module (host).
3. Doctrine distill + build_corpus (host, monkeypatched LLM).
4. Contract + prompt.
5. Service wiring (docker validation).
6. Real distillation run (sample → validate de-personalization → full if feasible).
7. Full docker suite + integration.
8. Docs, PR, memory, wakeup.

## 6. Progress log

(updated as I go — newest last)

- **Step 2 DONE — user memory.** `agent/memory.py` read/write/format, fake-client
  tested (9 passed). Conversation-only by construction; degrades on failure.
- **Step 3 DONE — doctrine distillation + corpus.** `doctrine/distill.py`
  (`is_depersonalized` QA gate, `extract_with_stats` one-call kept+dropped),
  `doctrine/build_corpus.py` (scope dirs, skip `_`/`_inbox`, write clean `.md`).
  19 tests. **Live validation** (`2026-06-14-doctrine-distillation-sample.md`): 3
  real files → 21 clean principles, 0 gate leaks, biography stripped from the
  hardest first-person entry. Riskiest assumption CONFIRMED.
- **Step 1 DONE — source-typed gate.** `types.py` (+`grounded_by`, KIND_* consts),
  `decide.py` (typed mode: fact needs MEMORY source; conceptual needs any;
  legacy preserved when no typed kinds), `judge.py` (emits/parses `grounded_by`),
  `gate.py` (builds source_kinds → decide). New `test_grounding_source_typed.py`
  incl. end-to-end "four months" biography-leak regression. 33 passed / 2 skipped,
  zero regressions across decide/judge/gate/types/integration/validations.

- **Step 4 DONE — contract + prompt.** `coach_prompt_claims.md` rewritten to the
  doctrine/memory world (facts cite `M1`, principles cite `D1`, hard line:
  doctrine can never warrant biography). Base `coach_prompt.md` UNCHANGED →
  flag-off still byte-identical. Plumbing test updated.
- **Step 5 DONE — retrieval + service wiring.** `retrieval.py`
  (`retrieve_doctrine_for_turn` + `format_doctrine_context`, doctrine retriever
  singleton at `COACH_DOCTRINE_INDEX_DIR`). `service.py`: gate-aware
  `_compose_turn_prompt` (flag-on → `<doctrine>`+`<about_this_user>`; flag-off →
  legacy vault, byte-identical), `_slugs_to_sources` builds typed D1/M1 sources,
  conversation-only `write_user_memory` after the turn, two new ContextVars.
  New `test_agent_doctrine_memory.py` (5, docker) incl. end-to-end demotion of a
  doctrine-only fact + conversation-only memory write. **Full docker coach suite:
  219 passed / 3 skipped, zero regressions.** Host-safe set: 110 passed / 2.

## 7. Assumptions to review (collected)

- One flag (`COACH_GROUNDING_GATE`) governs doctrine + memory + typed gate.
- Memory write = user's words only, not the coach's reply.
- Doctrine source dirs include `thoughts` + `entries` (distilled, so safe) and
  exclude `_inbox` + bibliography.
- mem0 `.add` per turn does the fact-extraction; we pass raw user text.
- Doctrine tier=2, memory tier=1 (memory = primary truth about the user).
- The full doctrine reindex is a deploy prerequisite; enabling the flag without a
  built doctrine index degrades to ungrounded (documented, not silent).

## 8. Explicitly NOT done in this slice (no silent drops)

- **Full doctrine corpus + index not built into prod.** Code + a validated
  3-file sample are done; the full ~280-file distill ran overnight for validation
  (host). Building the prod index (corpus → OpenAI embeddings → `/data/
  doctrine_index`) is an operational step, documented in the README + wakeup. The
  gate must not be enabled on this branch until it exists.
- ~~**`search_vault` MCP tool still points at the RAW vault.**~~ **FIXED in
  v0.1.7** (caught by the first prod smoke test — the coach called `search_vault`,
  hit the raw vault, and narrated the author's biography incl. "four months"). When
  the gate is on, `_build_agent_options` now points the `search_vault` MCP server's
  `COACH_INDEX_STORAGE_DIR` at the doctrine index, so a mid-turn dig-deeper returns
  de-personalised principles, never raw biography. Capability preserved.
- **mem0 extraction quality not eval'd.** We pass the user's raw text and let
  mem0 extract; no eval yet on what it stores / recalls. Worth a small eval.
- **Flag-on prompt still carries the legacy `<vault_context>` paragraph** from
  the unchanged base `coach_prompt.md` (the appended contract overrides it). Kept
  this way to preserve byte-identical flag-off; a clean unified flag-on base
  prompt is a follow-up.
- **No live end-to-end run** with a real doctrine index + gate on a real
  conversation yet (needs the index built; it's a supervised step for Gonzalo).

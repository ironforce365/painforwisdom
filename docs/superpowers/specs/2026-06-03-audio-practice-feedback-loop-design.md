# Audio Practice Feedback Loop — Design

**Author:** brainstorming session (background job "feedback-loop")
**Date:** 2026-06-03
**Status:** DESIGN — approved in brainstorm; no code yet. PoC-gated.
**Trigger:** Gonzalo's feedback that daily NotebookLM audios are isolated deep dives with no continuity and no feedback loop.
**Related docs:**
- `.planning/pipeline-evolution-2026-06.md` (June doc — entry-relation finder + quarterly progress audio; shares the graph schema)

---

## 1. Problem (grounded in current code)

The daily audio overviews work as an MVP but have two structural defects, both confirmed in the pipeline:

1. **Isolated deep dives.** `pipeline/summarize_daily/notebooklm_publisher.py:publish()` builds each audio from exactly four sources — `deep-dive.md`, `application.md`, `audio-prompts.md`, and **one** vault entry. No prior audio, no theme files, no book outline, no `_index`. Each audio is blind to every other audio, so concepts get re-explained every time and nothing connects to the vault graph.
2. **No feedback loop.** `render_focus_prompt()` emits "Three concrete adjustments to the practice" and "One question the brief leaves on Gonzalo's desk" *into* the audio, but nothing captures whether Gonzalo acted on them. The next audio is selected by `clusterer.py` greedily (the Notion theme with the most pending Research Tasks) — blind to prior questions, adjustments, or order.

Enabler defect: **no deterministic order.** "Build on prior audios" is meaningless while selection is greedy-by-pending-count.

## 2. Goal — turn the audios into a practice

A continuous loop:

1. Gonzalo produces content (runs, voice memos) → full pipeline → vault entry + audio.
2. The audio is generated with **context + memory** of what was already covered and what's open.
3. Gonzalo acts on the audio (answers its question / tries its protocol) and **records a response**.
4. The response re-enters as a lightweight input, seeds the next audio, which **reacts** — repeat.

Both halves (context-carrying memory AND closed feedback loop) are required; neither alone is the practice.

## 3. Locked decisions (from brainstorm)

| # | Decision | Choice |
|---|---|---|
| 1 | Primary goal | Context **and** feedback, as one integrated system |
| 2 | Feedback re-entry | A **new lightweight input type** — a "response" recording that skips the full pipeline (no blog, no research), is captured, linked to its parent entry+audio, and seeds the next audio |
| 3 | Response routing | **Telegram reply** — reply to the audio-drop message with a voice memo; `reply_to_message_id` gives the parent link for free |
| 4 | Trigger & order | **Event-driven** — an audio fires on a real beat; a response pulls its react-audio ahead of new-content audios; loop moves only when there's something to say |
| 5 | Memory substrate | **Global file-memory over the vault**, modeled on Claude Code's memory primitives; knowledge graph materialized in markdown; **neo4j deferred** |
| 6 | Op-memory home | **Inside the vault** (`gonzalo-book/_memory/`) — single source of truth, readable in Obsidian |
| 7 | React-audio sources | **Research-free** — react using existing corpus + memory; new research only on the deep-dive path |
| 8 | Spec scope | **PoC + MVP, PoC-gated** — prove the bet manually before automating |

## 4. Architecture

### 4.1 Knowledge graph (logical model, materialized in vault markdown)

Today's graph is implicit (wikilinks). Make it **explicit and typed** so the loop can reason over node *kinds*. Stored in markdown + frontmatter; **not** neo4j.

**Node kinds**
- `Entry` — episodic, from the full pipeline (existing `entries/`).
- `Audio` — a generated overview; carries what it **covered**, the **questions** it asked, the **protocols** it proposed.
- `Response` — **NEW**; a Telegram voice reply.
- `Concept` — a named idea (aMCC, override, pre-justification); the unit of repetition control.
- `Protocol` — a proposed practice change, with **status**: proposed / tried / dropped + result.
- `Theme` / `Framework` — existing.

**Edges**
- `Audio —derived-from→ Entry | Response`
- `Audio —covers→ Concept`
- `Audio —asks→ Question`
- `Audio —proposes→ Protocol`
- `Response —responds-to→ Audio`
- `Response —answers→ Question`
- `Response —reports→ Protocol(status, result)`
- everything `—belongs-to→ Theme`

**A "thread" is a view, not storage** — the `derived-from`/`responds-to` chain traversed on demand:

```
E1 ──derived-from──▶ A1  (covers C1,C2 · asks Q1 · proposes P1)
                      │  you listen, reply in Telegram ▼
                      └─◀ R1 (responds-to A1 · answers Q1 · reports P1=tried,result)
                              │ R1 fires next audio ▼
                              A2 (derived-from R1 · recall: C1,C2 already covered →
                                  don't re-explain · reacts to Q1+P1 · asks Q2 · proposes P2)
                              │
                              R2 ──▶ A3 ──▶ …
```

Storage is **global** (one store, not per-thread silos). The schema is **graph-ready** so storage can migrate to neo4j later without remodeling, only if multi-hop queries become the bottleneck (per PoC-before-migration rule).

### 4.2 Memory layer — Claude Code's 6 primitives, instantiated

The method extracted from how Claude Code memory works: `distill → type → index → recall → reconcile → link`. The valuable parts are distillation and hygiene, not the database.

Two new vault files become the loop's **always-on index** (the `MEMORY.md` analog):

- `gonzalo-book/_memory/concepts.md` — catalog of every `Concept`: name, depth-level, `last covered in [[A-…]]`, times-touched. **Kills repetition** — recall reads this to tell the next audio "aMCC is covered at depth 3, do not re-explain."
- `gonzalo-book/_memory/open-loops.md` — registry of every open `Question` and every `Protocol` with status. **The unresolved set** — what makes the loop feel alive: the next audio advances what's actually open, and a loop *closes visibly* when a response resolves it.

| Primitive | Who does it | When |
|---|---|---|
| distill | extend `coaching-thought-extractor` (Entries) + a new light extractor (Responses) | per beat |
| type | node-kind tag in frontmatter | on write |
| index | `_memory/concepts.md` + `_memory/open-loops.md` | per beat |
| recall | new selector step at audio-gen (§4.5) | per audio |
| hygiene | curator updates concept depth, flips loops open→closed, ages stale | per beat |
| link | wikilinks = the edges in §4.1 | on write |

No new infra, no DB. Node-kind frontmatter + two index files turn the vault into the global memory.

### 4.3 Response ingestion path (the new light lane)

```
Telegram audio-drop msg ──(you reply, voice memo)──▶ reply_to_message_id
   │
   ▼
1. Ingest: poller detects a voice message that is a reply to a tracked audio-drop message
2. Resolve parent: reply_to_message_id → stored map → parent Audio (→ Entry, open Qs, proposed Ps)
3. Transcribe: reuse extract_transcription.sh (whisper)
4. Light extract (NEW small step — NOT the full pipeline): from transcript produce a Response node →
      Qs answered (+answer), Ps reported (+status tried/dropped +result), fresh-content flag
5. Write: gonzalo-book/responses/YYYY-MM-DD-slug.md
      frontmatter: kind: response, responds-to: [[A-..]], answers: [...], reports: [...], has_fresh_content: bool
6. Update _memory: flip answered Qs / reported Ps in open-loops.md; add new concepts to concepts.md
7. Emit event: "response landed → generate react-audio"
```

- **Parent map**: `notebooklm_publisher` already sends the Telegram drop (`pipeline/telegram.py:send`); record the sent `message_id` next to the audio artifact. `telegram_io.sh` already polls `getUpdates` with offset tracking — the response lane extends it to detect `voice` + `reply_to_message_id` and `getFile`-download the memo. No fuzzy matching.
- **Fresh content**: if a response carries genuinely new material (not just answering), set `has_fresh_content: true` and **stop** — no auto-promotion to blog/research. Gonzalo decides later if it graduates to the full pipeline. Default light, per Decision 2.

### 4.4 Audio generation, made memory-aware

The audio (m4a) is **not retrievable text**, so each audio writes a **`coverage.md`** when generated — its covered-concepts + questions asked + protocols proposed. *That* is what future audios read, never the m4a.

**Source set** (was: `deep-dive.md` + `application.md` + `audio-prompts.md` + 1 entry):
- `+ memory-brief.md` — the recall output (§4.5): "Already covered, don't re-explain: {…}. Open loops being advanced: {…}. Protocols tried + results: {…}."
- `+` the parent **Response** (react-audios only) — Gonzalo's actual words back in.
- `+` the prior **`coverage.md`** of the thread — what the last audio said.

**Focus prompt** (added to the existing anchor→walk→close template in `render_focus_prompt`):
- **CONTINUITY block**: "Audio N in an ongoing practice. These concepts are covered at depth — do NOT re-explain: {concepts}. Build forward."
- **REACT block** (react-audios): "He responded. He said {summary}; answered Q with {…}; tried P, reported {result}. Open by engaging THAT, not by restarting."
- Existing close-on-open-question stays — but the new question/protocol is **captured back into `open-loops.md`** so the loop stays well-formed.

### 4.5 Recall algorithm (reuse, don't rebuild)

Deterministic-first, no embeddings:
1. From trigger node (Entry|Response) → its themes + concepts.
2. From `concepts.md`: covered concepts in those themes → "don't re-explain" list.
3. From `open-loops.md`: open Qs/Ps in those themes + this thread's chain → "advance these."
4. Rank related entries/audios by theme∩concept overlap + recency + same-thread bonus — **this is June doc Feature A's retrieval; same code.**
5. One Sonnet call compresses the slice → `memory-brief.md` (~1–2k tokens, bounded).

### 4.6 Event-driven trigger & ordering

No fixed daily cron drives the loop. Two beats fire audios:
- **Response lands** → react-audio (top priority; pulls ahead).
- **Fresh original content lands** (new Entry) → deep-dive audio.

A response always pulls its react-audio ahead of queued new-content audios (causal order). The loop only moves when there's a real beat.

## 5. Two audio types

| | Deep-dive audio (existing, upgraded) | React audio (new) |
|---|---|---|
| Fires on | fresh original content (new Entry) | a Response |
| Research | yes (existing research-cluster path) | **none** (Decision 7) |
| Sources | deep-dive + application + audio-prompts + entry **+ memory-brief + prior coverage** | response + parent coverage + memory-brief (+ entry) |
| Focus prompt | anchor→walk→close **+ CONTINUITY** | **REACT + CONTINUITY** + close |
| Speed | normal | fast/light |

The existing research machinery (entry → research → cluster → `deep-dive.md`) **stays** as the producer of deep-dive audios; it becomes memory-aware and order-aware. The greedy `clusterer.py` picker is superseded for ordering by the event model, but research clustering itself is retained.

## 6. PoC (gated — build this first)

**Riskiest assumption (the whole bet):** does feeding memory + a response into NotebookLM actually yield audios that feel *continuous* and *reactive* instead of isolated/repetitive? If it fails, all machinery is moot.

**PoC steps (manual glue, no automation):**
1. Pick one existing audio that has open Qs/Ps.
2. Record (or write) a response to it.
3. Hand-build `memory-brief.md` + add the response + the prior `coverage.md` as NotebookLM sources.
4. Generate the next audio with the upgraded focus prompt (CONTINUITY + REACT blocks).
5. Listen on a run. Pass criteria: feels continuous, reacts to the response, does **not** re-explain covered concepts.

**Gate:** only proceed to MVP if the PoC audio lands. If not, tune prompt/source mix before writing any pipeline code.

## 7. MVP (only after PoC passes)

Build order:
1. **Graph schema** — node-kind frontmatter convention + `responses/` dir + `_memory/` files.
2. **Response lane** — extend `telegram_io.sh` getUpdates to catch voice replies; resolve parent via `message_id` map; transcribe; light extractor → Response node.
3. **Memory curator** — distill/hygiene step that maintains `concepts.md` + `open-loops.md` after every beat (Entry, Audio, Response).
4. **Recall selector** — port June Feature A retrieval → `memory-brief.md`.
5. **Memory-aware generation** — `coverage.md` write on every audio; upgraded source set + focus prompt in `notebooklm_publisher.py`.
6. **Event trigger** — response-landed → react-audio; new-entry → deep-dive audio.

### Integration points (reuse, don't fork)
- `pipeline/summarize_daily/notebooklm_publisher.py` — `publish()`, `render_focus_prompt()`, `_add_source()`, `_trigger_audio()` (upgrade source set + focus prompt; write `coverage.md` + record Telegram `message_id`).
- `pipeline/telegram.py` — `send()` (send side, exists).
- `telegram_io.sh` — `getUpdates` poller with offset (extend for voice replies).
- `extract_transcription.sh` — whisper transcription (reuse for response memos).
- `.claude/agents/coaching-thought-extractor.md`, `kb-curator.md` — distillation (extend; add a light response-extractor).
- `obsidian-vault/gonzalo-book/` — `entries/`, `themes/`, `frameworks/`, `_index.md` (read); `responses/`, `_memory/` (new, write).
- `.planning/pipeline-evolution-2026-06.md` Feature A retrieval — the recall ranker.

## 8. Relationship to the June doc

The June doc (entry-relation finder + quarterly progress audio) and this loop are **two consumers of one graph**. Same node/edge schema (§4.1) serves both. Specifically:
- June Feature A's hybrid retrieval = this loop's recall ranker (§4.5). Build once.
- June Feature B/C (quarterly progress) stays a separate cadence but reads the same `_memory/` + graph.
- Design the schema now so both fit; do not build two graphs.

## 9. Deferred / out of scope

- **neo4j** — graph stays in markdown until multi-hop queries are the bottleneck.
- **Vector RAG / embeddings** — recall is deterministic-first; add fuzzy concept-dedup only past ~500 entries.
- **Response → full-pipeline promotion** — responses with `has_fresh_content` are flagged, not auto-promoted.
- **Replacing the research-cluster machinery** — retained for deep-dive audios.
- **Daily cron heartbeat** — superseded by the event model.

## 10. Open questions & risks

| # | Item | Note / mitigation |
|---|---|---|
| 1 | Concept identity | What counts as "the same Concept" across entries (for dedup)? Start with the existing kebab-case wikilink anchors + a small curated lexicon (as June doc does); refine after PoC. |
| 2 | Telegram voice quality | Whisper accuracy on a phone voice memo recorded outdoors. Mitigation: the light extractor tolerates rough transcripts; Gonzalo can re-reply. |
| 3 | `message_id` map durability | Where the audio-drop→message_id map lives (likely `_memory/` or the brief dir). Decide at MVP step 2. |
| 4 | Loop never closes | A question may never get a response. open-loops.md ages stale loops; they resurface as deep-dive material rather than blocking. |
| 5 | React-audio feels thin | Research-free react-audios may feel evidence-light. Revisit Decision 7 if the PoC/early MVP audios feel hollow (light top-up is the fallback). |
| 6 | NotebookLM source-set size | Adding memory-brief + coverage + response grows the source set; watch the NotebookLM per-notebook source cap. |

## 11. Success criteria

- **PoC:** one generated audio that, on a listen, clearly continues from the prior one and reacts to the recorded response without re-explaining covered ground. Binary pass/fail; it's the gate.
- **MVP:** a full turn of the loop runs without manual glue — Gonzalo replies in Telegram → a react-audio drops back into the same Telegram queue → it references his response and an open loop visibly closes in `open-loops.md`.
- **Cross-cutting:** no regression to the existing daily/per-video pipelines.

---

*End of design. PoC-gated: prove the bet before automating.*

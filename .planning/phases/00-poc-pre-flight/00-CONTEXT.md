# Phase 0: PoC & Pre-Flight - Context

**Gathered:** 2026-05-18
**Status:** Ready for planning

<domain>
## Phase Boundary

Validate the two HIGH-risk technical assumptions (Spanish Whisper quality on `large-v3`/`-turbo` + LLM splitter reliability on conversational ES) and the two HIGH-risk operational gates (Voicepal kill-list with 7-day no-op observation + `pipeline.cost_forecast --voicenote` extension) on real audio fixtures **before any `voicenote/` module scaffolding lands**. Also close the pre-rollout retry-bound gap (`pipeline/retry.py:_resume_graph` `_ask_indefinitely` → `_ask_bounded`) so voicenote rollout does not compound the existing CONCERNS.md `[med]` scar.

Phase 0 is a **hard go/no-go gate** — if Whisper, splitter, or extractor fails on real fixtures, PROJECT.md is updated and the architecture pivots; NO scaffolding starts. Pass = the three quality bars in success criteria 1-3 are met on ≥3 real Spanish voice notes AND criteria 4-5 ship.

</domain>

<decisions>
## Implementation Decisions

### PoC fixtures — audio source and count

- **D-01:** Fixtures are **fresh recordings**, not pulled from existing archive or replayed from Voicepal subpages. Reason: POC-01 needs real audio to validate Whisper, and the 29 Voicepal subpages are text-only — they cannot validate the Whisper assumption.
- **D-02:** **Record 4-5 fixtures, use ≥3 valid** in the gate evaluation. The buffer protects the gate against one bad recording (noisy environment, too few atomic thoughts, Whisper failure) without forcing a mid-PoC re-record.
- **D-03:** **No duration gating** on individual fixtures — record whatever comes out naturally. Phase 0 success criteria reference ~10-min notes as a baseline, but the PoC accepts whatever the fresh recordings happen to be. Document the actual duration of each fixture in its sidecar.

### PoC fixtures — capture path

- **D-04:** **Capture via a throwaway PTB script that calls `Bot.get_updates` + `get_file().download_to_drive()`** into the fixtures directory. Send recordings to the new Voicenote bot via Telegram. This validates the `.ogg`/Opus 16 kHz mono native format end-to-end as a side-effect, and tests the `getFile` 20 MB cap on real recordings.
- **D-05:** The PTB capture script is **explicitly NOT module scaffolding** — it does not live under `voicenote/`, does not produce a `LongNote`, does not persist any state. It writes raw `.ogg` to the fixtures directory and exits. The proper `voicenote/sources/telegram.py` lives in Phase 1. This keeps Phase 0 honest to the `feedback_poc_before_migration` constraint.

### PoC fixtures — storage layout

- **D-06:** All PoC fixture artifacts live under `.planning/phases/00-poc-pre-flight/fixtures/`. Colocated with the phase; cleanly disposable post-Phase-0; clean diff for the planner.
- **D-07:** Per-fixture layout: `fixture-N/audio.ogg` (gitignored — add to `.gitignore` under `.planning/phases/00-poc-pre-flight/fixtures/**/audio.*`), `fixture-N/transcript.txt` (Whisper output, committed), `fixture-N/boundaries.md` (ground-truth boundary sidecar, committed), `fixture-N/notes.md` (per-fixture WER sample + qualitative observations, committed).

### PoC fixtures — ground-truth boundary capture

- **D-08:** **Per-fixture markdown sidecar `fixture-N/boundaries.md`** is the ground truth for POC-02's ≥80% boundary agreement. Format: ordered list of `[sentence_start_idx, sentence_end_idx]` ranges with a 1-line topic label per chunk. Sentence indices reference the Whisper transcript line numbers.
- **D-09:** **Boundary agreement metric:** for each fixture, count how many of Gonzalo's ground-truth chunks the LLM splitter also produces (chunks agree if the LLM's boundary lands within ±1 sentence of the ground-truth boundary). Aggregate: ≥80% of ground-truth chunks recovered across the ≥3 valid fixtures.

### PoC fixtures — Whisper signal capture

- **D-10:** **POC-01 'readable enough' signal = gut-check + 1 WER sample paragraph per fixture.** Pick the densest ~100-word paragraph per fixture, hand-correct, compute WER, record the number in `fixture-N/notes.md` alongside an overall qualitative readability call ("Can a human identify coaching-thought boundaries with the same confidence as the original audio?"). Documented per-fixture WER becomes the posterity signal for any future Whisper model revisit.

### Claude's Discretion

- **Splitter prompt placement during PoC.** The PoC needs a working splitter prompt to evaluate POC-02. Whether that prompt lives as a scratch string inside a Phase-0 PoC script, OR as the first draft of `.claude/agents/voicenote-splitter.md` (which then becomes Phase 1 input), is Claude's call during planning. Either is acceptable as long as the prompt + LLM call shape used for evaluation is faithful to what Phase 1 will run.
- **POC-03 'indistinguishable from manual' comparison method.** Whether to compare merged chunk extractions against (a) a manually-written entry Gonzalo would have written from the same source, (b) an existing vault entry on the same theme, or (c) the existing kb_curator Strong/Adequate/Weak quality flag — Claude picks the cheapest faithful signal during planning.
- **Cost forecast `--voicenote` shape (POC-05).** Success criterion 4 already specifies the two scenarios (29-page backfill + typical weekly Telegram cadence) and outputs (tokens, USD-equivalent, Anthropic-Ultra-quota share). Whether the forecast prints-only or hard-gates at a quota threshold (e.g., >80% share aborts), where the snapshot lives (file vs stdout-only), and the precise heuristic for "typical weekly cadence" — Claude's discretion during planning.
- **Voicepal kill-list enforcement mechanism.** POC-04 lands the kill-list in `OPERATIONS.md` with enumerated webhooks / scheduled syncs / Notion automations. How the 7-day no-op observation window is tracked — calendar reminder, dated entry in OPERATIONS.md, cron status check, or just a manual followup — is Claude's call. Constraint: cancellation does not happen until the 7 days elapse with no observed Voicepal activity.
- **Retry-bound gap close (`_resume_graph`).** ROADMAP Phase 0 description and STATE.md both say this lands in Phase 0 (before voicenote rollout). Whether it is its own plan or bundled with POC-05 (both touch `pipeline/`), and whether the fix mirrors the existing `_ask_bounded` shape exactly (`MAX_REMINDERS=5`) — Claude's call during planning.
- **PoC results consolidation document.** A summary that consolidates the 5 PoC outputs into a single "PoC verdict" doc (suggested location: `.planning/research/poc-results.md`) is useful for the go/no-go decision but is Claude's discretion in shape and location.

</decisions>

<canonical_refs>
## Canonical References

**Downstream agents MUST read these before planning or implementing.**

### Phase 0 framing and locked decisions

- `.planning/PROJECT.md` — Voicenote scope, Core Value, 16 locked Key Decisions (especially: plain Python, PTB sole new dep, ES preservation, PoC-first ordering, cost forecast pre-flight)
- `.planning/REQUIREMENTS.md` §Pre-Flight (POC) — POC-01 through POC-05 with exact wording, plus traceability table
- `.planning/ROADMAP.md` §"Phase 0: PoC & Pre-Flight" — Goal, Depends on, Requirements, the five Success Criteria
- `.planning/STATE.md` — Current decisions surface for Phase 0 + reminder that `_resume_graph` retry-bound must close in Phase 0 before voicenote rollout

### Research grounding (what's been investigated, what's still gray)

- `.planning/research/SUMMARY.md` — §1 Headline, §4 Architecture build order (PoC-first), §6 Reuse-vs-Build matrix, §8 Gaps to address during execution (items 1-4 are exactly the PoC's job)
- `.planning/research/PITFALLS.md` Pitfalls 1, 2 (Whisper ES hallucination + code-switching — POC-01), Pitfall 3 (chunking — POC-02), Pitfall 4 (translation flattens voice — POC-03), Pitfall 15 (LLM quota burn — POC-05 + retry-bound), Pitfall 16 (Voicepal residual integration — POC-04)
- `.planning/research/STACK.md` — Whisper `large-v3` rationale, LLM split rationale, what is NOT being added (no faster-whisper, no LangChain splitters)
- `.planning/research/ARCHITECTURE.md` — Module layout (for context on what Phase 0 is gating against), state machine (not built in Phase 0)
- `.planning/research/FEATURES.md` — Table-stakes vs differentiators (Phase 0 only de-risks table-stakes assumptions)

### Existing codebase scar tissue Phase 0 must respect

- `.planning/codebase/CONCERNS.md` — `[high]` vault submodule dirty (Phase 4 closes, not Phase 0), `[med]` `pipeline/retry.py:_resume_graph` `_ask_indefinitely` (Phase 0 CLOSES), `[med]` Notion centralised pacing (Phase 2 closes), `[high]` extract_transcription.sh stderr capture (Phase 1)
- `.planning/codebase/CONVENTIONS.md` — naming, state-field discipline, prompt-loading pattern, tier-1 loud-fail discipline (Phase 0 PTB capture script + cost forecast extension must respect)
- `.planning/codebase/STACK.md` — current pipeline stack (LangGraph stays untouched, voicenote sits alongside)
- `.planning/codebase/INTEGRATIONS.md` — Anthropic OAuth refresh, Notion REST, Telegram bot, Whisper subprocess (Phase 0 reuses Anthropic + Whisper, validates Telegram getFile)
- `.planning/codebase/ARCHITECTURE.md` — existing per-node `State -> Dict[str, Any]` contract (Phase 0 does NOT scaffold a new node)

### Operational + tooling references

- `OPERATIONS.md` — destination for POC-04 Voicepal kill-list (enumerated webhooks / scheduled syncs / Notion automations + 7-day no-op observation tracking)
- `pipeline/cost_forecast.py` — extension point for POC-05 `--voicenote` mode (existing forecast pattern + Anthropic Pro/Max quota constants)
- `pipeline/retry.py:_resume_graph` + `_ask_bounded` — retry-bound gap fix target (mirror `_ask_bounded` shape; `MAX_REMINDERS=5` baseline)
- `extract_transcription.sh` — Whisper invocation entry; PoC uses with `--lang Spanish --model large-v3` (or `-turbo` if conda env has weights)
- `.claude/agents/coaching-thought-extractor.md` — agent prompt reused for POC-03 per-chunk extraction; fork-vs-reuse decision recorded post-PoC

### User-memory rules that govern Phase 0

- `user_ultra_subscription` — talk tokens/quota/wall-clock, not USD (cost forecast still outputs USD-equivalent for posterity)
- `feedback_poc_before_migration` — non-negotiable PoC-first ordering before any scaffolding
- `feedback_cost_forecast_before_replay` — POC-05 implements this for voicenote
- `feedback_no_silent_feature_drops` — if any PoC bar fails, surface explicitly (no silent substitution); PROJECT.md updates
- `pipeline_perf_baseline` — wall-clock context for cost forecast outputs

</canonical_refs>

<code_context>
## Existing Code Insights

### Reusable Assets

- **`extract_transcription.sh`** — Whisper invocation with confidence gating. POC-01 reuses with `WHISPER_MODEL=large-v3 LANGUAGE=Spanish` env override; no fork needed. The `--initial_prompt` slot is the natural carrier for any code-switch glossary if PoC surfaces drift.
- **`pipeline/cost_forecast.py`** — existing token + USD + Anthropic quota forecasting harness (`StageForecast` dataclass, `PRICES` table, `PRO_MAX5_SONNET_MSGS_PER_5H` baseline). POC-05 extends with a `--voicenote` mode that adds voicenote stages (split, translate, extract × N) to the existing per-stage rollup.
- **`pipeline/llm.call_llm`** — Anthropic Sonnet 4.6 via LiteLLM with OAuth refresh, web_search gating, 1M-context flag. PoC splitter + extractor LLM calls go through this — no separate auth path, free cost telemetry, no surprises.
- **`pipeline/retry.py:_ask_bounded`** — bounded-reminder helper (`MAX_REMINDERS=5`). The retry-bound fix lifts this shape into `_resume_graph` to replace the unbounded `_ask_indefinitely`.
- **`.claude/agents/coaching-thought-extractor.md`** — agent prompt used in PoC-03 to validate per-chunk extraction; tuned for 200-600 word raw transcripts, so PoC also surfaces whether the 200-800 word chunk distribution warrants a `coaching-thought-extractor-chunk.md` fork.
- **`python-telegram-bot==22.7`** — already locked as Phase 1 dep; PoC capture script uses a minimal subset (`Bot.get_updates`, `get_file().download_to_drive`) to pull `.ogg` recordings into the fixtures dir.

### Established Patterns

- **Stdlib `unittest`, no `pytest` pin** — Phase 0 introduces no new tests (no module scaffolding to test), but the retry-bound fix needs a unit test mirroring the `_ask_bounded` coverage. Plain `unittest.TestCase` per `tests/` conventions.
- **`PROJECT_ROOT / ".claude" / "agents"` prompt loading** via `pipeline/runtime.py:load_agent_prompt` — if PoC drafts a splitter prompt at `.claude/agents/voicenote-splitter.md`, it loads through the same path stripping YAML frontmatter + `## OUTPUT`.
- **Tier-1 loud-fail discipline** (CONVENTIONS.md) — PoC outputs are advisory not enforcement, but the retry-bound fix and cost-forecast extension must follow loud-fail semantics (e.g., refuse to proceed if cost forecast detects quota >80% share).
- **No new top-level deps** — PoC uses only what's already in `pipeline/requirements.txt` plus PTB (already targeted for Phase 1). Capture script does NOT add new deps.

### Integration Points

- **PoC fixtures dir → `extract_transcription.sh`** — `.ogg` in, `transcript.txt` out, same flow as production.
- **PoC splitter prompt → `pipeline.llm.call_llm`** — ES transcript in, chunk boundaries out via `tool_use` JSON. No state, no checkpointing.
- **POC-05 `--voicenote` → `pipeline.cost_forecast` CLI** — extends existing argparse + per-stage rollup; output format follows existing forecast text layout.
- **Retry-bound fix → `pipeline/retry.py`** — single-file edit + a test. Existing video pipeline must continue green (no behavior change for short-thought path; `_resume_graph` only changes from unbounded to bounded wait).
- **Voicepal kill-list → `OPERATIONS.md`** — new section under existing runbook layout; no new file.

</code_context>

<specifics>
## Specific Ideas

- Recording capture via the new Voicenote bot doubles as a smoke-test of `python-telegram-bot==22.7` `Bot.get_updates` + `get_file().download_to_drive` on Gonzalo's actual ES audio. If the .ogg format / 20 MB cap / Opus 16 kHz mono assumption breaks here, Phase 1's `voicenote/sources/telegram.py` is forewarned.
- "Whatever's available" duration policy is permissive on purpose: real voice-note distribution will be uneven; pre-judging the duration would bias the fixture corpus away from realistic.
- Per-fixture markdown sidecar (`fixture-N/boundaries.md`) is the ground-truth artifact downstream Phase 1 splitter iteration can diff against — it's not throwaway, it graduates to regression input.

</specifics>

<deferred>
## Deferred Ideas

- **Splitter prompt formal versioning + few-shot examples from PoC fixtures** — Phase 1 lifts the PoC splitter prompt into `.claude/agents/voicenote-splitter.md` with a stable schema + 2-3 few-shot examples drawn from Phase 0 fixtures.
- **`coaching-thought-extractor-chunk.md` fork** — only triggered if PoC-03 shows the existing extractor degrades on chunk-shape input; decision recorded in PoC results, fork happens in Phase 1 or Phase 2 if needed (Pitfall 10).
- **`large-v3-turbo` weight installation in conda env** — if conda env lacks the turbo weights, ship Phase 0 with `large-v3` and revisit turbo as a Phase-1 quality tightening (5× speedup at same WER).
- **Code-switch glossary file (30-term ES↔EN)** — Phase 1 deliverable; Phase 0 only flags whether code-switching drift shows up on the fresh recordings.
- **Boilerplate blocklist regex (post-Whisper)** — Phase 1 deliverable; Phase 0 surfaces silence-hallucination signal but does not implement the fix.
- **Pre-VAD silence trim via ffmpeg `silenceremove`** — Phase 1 transcribe-node integration; Phase 0 may apply manually if a fixture has problematic silences.
- **Theme-cap discipline (≤14)** — Phase 4 / Phase 5 work; Phase 0 does not touch `kb-curator.md`.

</deferred>

---

*Phase: 00-poc-pre-flight*
*Context gathered: 2026-05-18*

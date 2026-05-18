# Pitfalls Research — Long-Form Voice-Note → Personal-Vault Pipeline

**Domain:** Spanish-language long-form voice-note ingestion → Whisper transcription → LLM chunk-split → ES→EN translation → coaching-thought-extractor → kb-curator → Obsidian-vault git submodule (single-user, single-host, Telegram HITL)
**Researched:** 2026-05-18
**Confidence:** HIGH for items intersecting existing codebase (CONCERNS.md / INTEGRATIONS.md / CONVENTIONS.md verified). MEDIUM for net-new Telegram-voice + ES→EN translation pieces (web-sourced, not yet exercised in repo).

This document focuses on **what is new to this milestone** (Telegram voice intake, dual-source ingest, ES→EN translation, long-transcript chunking, async draft-approval state) and **how the existing pipeline's scar tissue applies to those new paths**. Items already documented in `.planning/codebase/CONCERNS.md` are cross-referenced, not duplicated.

---

## Critical Pitfalls

### Pitfall 1: Whisper Spanish hallucination on silence and padding offset

**What goes wrong:**
A long voice note (5–15 min) contains "thinking pauses" — 3-10 s silences between sentences. On those silences Whisper either (a) inserts hallucinated boilerplate ("Gracias por ver el video", "Suscríbete al canal", or repeated previous phrases) or (b) silently shifts segment timestamps so subtitles land before the audio. With `medium` Spanish, both happen.

**Why it happens:**
Whisper's decoding is autoregressive; on a low-energy window it samples from the language model prior, which over-represents YouTube outro phrases. The silence-padding issue is a known timestamp bug: leading silence is collapsed during VAD, but the model emits the same start_ts regardless. Documented in `openai/whisper` discussions #679, ggml-org/whisper.cpp #1724.

**How to avoid:**
1. **Pre-VAD trim** the OGG before Whisper: `ffmpeg -i in.ogg -af "silenceremove=stop_periods=-1:stop_duration=2:stop_threshold=-50dB" -ar 16000 -ac 1 out.wav` — collapses silences >2 s with -50 dB floor.
2. **Force language** via `--language es` and **force `--task transcribe`** (never `translate` — that path through Whisper drops voice fidelity badly; do EN translation as a separate downstream LLM call where the prompt controls style).
3. **Confidence gate hallucinations:** reuse the existing `no_speech_prob>0.5 / avg_logprob<-1.0 / compression_ratio>2.4` gate from `pipeline/nodes/transcribe.py`. The compression_ratio gate catches repeated-phrase hallucinations specifically.
4. **Add a "boilerplate blocklist"** post-Whisper: strip segments matching `^(Gracias por (ver|escuchar)|Suscríb|Subscribe|♪|\[Music\])` — this catches >90% of silence hallucinations in conversational Spanish.
5. **Sample rate consistency:** Telegram OGG/Opus is 16 kHz mono natively (verified — matches Whisper's training distribution); video extraction (.wav from ffmpeg) is typically 44.1 kHz stereo. ALWAYS resample to 16 kHz mono before invoking Whisper; do not assume `extract_transcription.sh` does it for both paths.

**Warning signs:**
- Telegram alert "extraction.quality=Weak with theme_count=0" on a transcript that the user knows had content
- `runs.jsonl` shows transcript size >2× expected words/minute (Whisper inserted boilerplate to fill silences)
- Segments with `compression_ratio>2.0` clustered in the back half of the transcript
- Identical phrases repeating 3+ times — classic Whisper loop

**Phase to address:** Phase 1 (Telegram voice intake + Whisper-Opus path). MUST land before any user-facing voice flow.

**Severity:** HIGH

---

### Pitfall 2: Whisper code-switching Spanish↔English — gets "stuck" in English

**What goes wrong:**
User says: *"...la clave acá es **growth**, lo que llamo el **breakthrough moment**..."* Whisper correctly switches to English for those tokens, but for the NEXT 30-60 s continues to transcribe in English even when the speaker is back in Spanish. Often produces a phonetic-English garbage transcription of Spanish speech. The narrator's signature voice (which mixes ES + EN coaching vocabulary intentionally) becomes unrecoverable.

**Why it happens:**
Whisper detects language **per decoding window** (~30 s) and biases the entire window's token distribution. After a strong English signal (a clear English word), the language LM prior shifts and lingers. `medium` model is more prone than `large-v3` because its multilingual representation is weaker. Documented in openai/whisper discussion #2009.

**How to avoid:**
1. **Hard-pin `--language es`** for voice notes (never `auto`). This forces the Spanish prior every window, so English insertions stay verbatim (still transcribed correctly) but the next Spanish chunk resumes.
2. **Preserve code-switched English terms literally**: do NOT rely on Whisper "translating" them. The downstream ES→EN translation step is where voice gets controlled. Pinning ES means English terms come through as English text in a Spanish transcript — exactly what we want.
3. **Build a curated `code_switch_glossary` file** (~30 terms: growth, breakthrough, currency, leverage, framework, deliberate practice, comfort zone, edge, identity, leverage points…) and pass it to Whisper's `--initial_prompt`. The prompt biases the model to recognise those tokens as English-in-Spanish, not "Spanish phonetic garbage that sounds vaguely English."
4. **Upgrade to `large-v3` for the voice-note path** if `medium` continues to slip — the WER gap on conversational ES with English insertions is large (Common Voice 15 ES medium ≈ 11–13% WER, large-v3 ≈ 7–9%). Cost: ~3× GPU memory + 2× wall-clock. Gate via `WHISPER_MODEL_VOICENOTE=large-v3`.

**Warning signs:**
- Transcript contains long stretches of phonetic-English nonsense ("yo de seek the breakthrough is the key" instead of "yo busco el breakthrough es la clave")
- After a clearly English word, the next 60–90 s of Spanish has WER >30%
- `kb_curator` flags `Weak` quality on transcripts the user knows are dense
- The user manually reports "the transcript missed half my Spanish" — this is THE diagnostic

**Phase to address:** Phase 1 (Whisper-Opus path). Glossary file ships with the phase; large-v3 upgrade is a Phase 2 quality tightening.

**Severity:** HIGH

---

### Pitfall 3: Long-transcript chunking splits one coaching idea across two LLM calls

**What goes wrong:**
A 10-min voice note is ~1500 words after Whisper. The current `coaching-thought-extractor` was tuned for 200-600 word raw transcripts (per fixture set `tests/fixtures/transcript_*.txt`). Naive chunking by paragraph or by N-word window splits a coaching idea mid-thought — preamble in chunk 1, framework analogy in chunk 2, application in chunk 3 — and each chunk gets extracted independently. Result: three "Weak" extractions instead of one "Strong" one, or worse: three duplicate themes with subtle naming drift.

**Why it happens:**
Naive chunking strategies (fixed-window, sentence-count) are pitfalls documented across the RAG literature (Pinecone chunking guide, Weaviate RAG). Two failure modes:
- **Over-segmentation:** chunk size < idea size → splits a thought
- **Under-segmentation:** chunk size > 2× idea size → merges three thoughts, extractor picks the loudest and drops two

Voice-note ideas are 100–800 words each with no consistent paragraph breaks (Whisper output has minimal punctuation in Spanish).

**How to avoid:**
1. **Semantic-boundary chunking, not fixed-window.** Two-pass:
   - Pass 1: cheap LLM call (Haiku-tier or Sonnet with low max_tokens) emits chunk-boundary indices with a "topic shift score" per sentence pair. Prompt: "List the sentence indices where the speaker shifts to a new coaching idea. A new idea starts with: a new metaphor, a new client/personal anecdote, a new framework name, or a 'pause+restart' marker like 'bueno, y otra cosa…' / 'entonces, lo que sigue es…'"
   - Pass 2: chunk by those indices, with **120-word overlap** at each boundary to preserve preamble context.
2. **Cap per-chunk word count** at 800 words; if the boundary-LLM emits a chunk >800 words, force a sub-split at the longest internal sentence boundary. Loud-fail (per CONVENTIONS.md tier-1) if a single chunk exceeds 1200 words — likely a boundary-LLM regression.
3. **NEVER chunk on character or token count alone** for voice notes. The existing pipeline's "one transcript → one extraction" assumption breaks; the new chunk path needs its own state field (`voicenote_chunks: List[ChunkRecord]`) per CONVENTIONS.md state-field discipline.
4. **Preamble context propagation:** the first 200 chars of chunk N-1 are prepended to chunk N as `<previous_context>...</previous_context>` block — the extractor prompt treats it as orienting, not as content to extract. Prevents losing the "this section is about X" framing.

**Warning signs:**
- One voice-note produces ≥3 extractions, all "Weak" quality
- kb_curator HITL prompt offers ≥3 new themes that differ only by adjective ("deliberate-practice" vs "deliberate-discomfort" vs "deliberate-edge") — classic over-segmentation duplicate-theme drift
- `runs.jsonl` shows extraction stage running 3× per voice note while still hitting "Weak" — re-prompting on bad boundaries
- User reports "the entry says X but skipped Y which was the whole point" — preamble loss

**Phase to address:** Phase 2 (chunk-splitter). MUST land before reusing `coaching-thought-extractor` on long transcripts.

**Severity:** HIGH

---

### Pitfall 4: ES→EN translation flattens narrator's signature voice

**What goes wrong:**
The user's vault entries depend on signature phrasing: deliberate Spanglish ("la **breakthrough** real es…"), idiomatic Spanish ("le pone fichas a", "se la juega", "pura cáscara"), cultural framing (Argentine/Latin-American coaching register). A naïve "translate this Spanish to English" prompt produces fluent but generic English that loses every one of those markers. Downstream `painforwisdom-writer` (already prompt-engineered for the existing voice) then writes blog posts in a voice that is NOT the user's.

**Why it happens:**
Documented in `arxiv:2407.03518` ("Improving LLM Abilities in Idiomatic Translation") — LLMs default to literal translation, lose figurative meaning AND cultural style. Code-switched English terms in the Spanish source get "re-translated" back to alternative English wordings ("growth" → "expansion", "breakthrough" → "major progress") — destroying the user's own coaching lexicon.

**How to avoid:**
1. **Do NOT translate.** First option: skip translation entirely; feed Spanish transcript directly to `coaching-thought-extractor` with a prompt that accepts ES input and emits EN extraction. Sonnet 4.6 handles this without quality loss. This removes an entire failure surface. STRONG RECOMMENDATION — explicit feature decision per user memory `feedback_no_silent_feature_drops.md`.
2. **If translation is required** (e.g. because vault entries are EN-only by convention):
   - Pin code-switched terms via a `preserve_verbatim` glossary in the prompt: "Keep these tokens verbatim if they appear in the source: growth, breakthrough, currency, leverage, framework, edge, identity, comfort zone, deliberate practice." Same list as Pitfall 2's Whisper initial_prompt.
   - Add **3-5 few-shot examples** of the user's own prior voice-note ES + EN entry pairs (pull from existing vault entries that were originally Spanish-thought). The model imitates style from few-shot more reliably than from instruction prose.
   - Explicit prompt clause: "Preserve idiomatic Spanish phrasing where there is no direct English equivalent — render literally and append the literal Spanish in italics." (e.g. "*le pone fichas a esto* — he's betting on this").
3. **NEVER do round-trip translation** (ES → EN → ES check). Round-trips compound flattening.
4. **Cache translations** keyed on `sha256(transcript_text)` to avoid re-translating on retries — translation tokens are not free, and the loop "extract → quality=Weak → retry → re-translate" can burn 4× the necessary tokens.

**Warning signs:**
- Extraction reports lose user-specific framework names ("amcc-effect" replaced by "advantageous mental conditioning")
- Vault entries read like generic life-coaching content instead of the user's specific register
- kb_curator HITL flags "this looks like a new theme but actually it's `deliberate-discomfort` rephrased" — translation aliased an existing theme
- The user manually edits >30% of a translated entry — voice mismatch

**Phase to address:** Phase 2 (translation OR direct-ES extraction decision). User-facing feature decision — surface explicitly per `feedback_no_silent_feature_drops.md`.

**Severity:** HIGH

---

### Pitfall 5: Telegram `getUpdates` update_id offset persistence loss = re-process every voice note

**What goes wrong:**
The new voice-note intake polls `getUpdates` to discover incoming voice messages. If the offset is held in memory and the process restarts (cron tick, systemd unit, dev interrupt), the next poll re-reads the same updates and the pipeline re-transcribes + re-extracts + re-writes vault entries that already exist. With kb-curator's immutable-entry guard (`pipeline/nodes/kb_curator.py:316` raises on overwrite), the second run hard-fails — but only AFTER burning the Whisper + chunking + extraction LLM tokens.

**Why it happens:**
Telegram Bot API docs explicitly require: "Recalculate offset after each server response. Store the offset in persistent storage." Common pitfall documented in `telegrambots.github.io` and bothero.ai polling guides. The existing `telegram_io.sh:_wait_reply` is request-response (does NOT persist offset between sessions — it only consumes the FIRST reply per invocation) so the codebase has no offset-persistence pattern to copy from.

**How to avoid:**
1. **Persist `update_id_offset` in SQLite** alongside existing `pipeline/state/themes.db` (or a sibling file). Schema: `telegram_voice_intake(last_update_id INTEGER, updated_at TIMESTAMP)`. Write the offset BEFORE acknowledging the voice note as processed (write-ahead pattern).
2. **Idempotency key on Telegram `message_id` (NOT update_id):** `message_id` is per-chat stable, `update_id` is per-bot global. Hash key for "this voice was processed": `f"{chat_id}:{message_id}"`. Store in a `voicenotes_processed(chat_id, message_id, run_id, transcript_sha)` table; check BEFORE invoking Whisper.
3. **Process the longest-running stage (Whisper) only AFTER idempotency check passes.** Order: read message → check idempotency → download .ogg (small, ~3 MB for 10 min) → mark `processing` → invoke Whisper → on success mark `done` + bump offset.
4. **Crash-recovery on `processing` state:** on startup, query `voicenotes_processed WHERE state='processing' AND started_at < now - 30 min` → these were interrupted; re-queue them. Otherwise their `.ogg` sits orphaned.
5. **NEVER mix polling and webhooks.** Telegram allows only one — running both → updates silently disappear on the webhook side. The existing `telegram_io.sh` is poll-only; preserve that.

**Warning signs:**
- Same voice note transcribes twice with different `run_id`s
- kb_curator raises `RuntimeError: entry already exists` on a fresh-looking run
- `runs.jsonl` shows duplicate `transcribe` rows with identical `transcript_sha` minutes apart
- Telegram chat shows the bot acknowledging the same voice twice
- `pipeline/checkpoints.db` size grows unexpectedly fast (one checkpoint per redundant run)

**Phase to address:** Phase 1 (Telegram intake). Critical foundation.

**Severity:** HIGH

---

### Pitfall 6: Telegram voice file >20 MB silently fails on `getFile`

**What goes wrong:**
Bot API `getFile` returns files up to 20 MB only (not the 50 MB ceiling of `sendDocument` — different limit). A 30-min voice note at the higher Telegram quality preset can exceed 20 MB. The bot receives the `voice` or `audio` update with `file_id` and `file_size`, calls `getFile`, gets a 400 "file is too big" — but if the surrounding code only checks `ok==True` (per existing `telegram_io.sh:41` pattern), the failure surfaces as "no transcript produced" without diagnostic.

Per Telegram Bot API and `tdlib/telegram-bot-api#683`: getFile is capped at 20 MB on the public Bot API.

**Why it happens:**
The 50 MB / 20 MB distinction is asymmetric: bots can SEND up to 50 MB but DOWNLOAD via Bot API up to 20 MB only. Local TDLib Bot API server bypasses this, but the project uses the hosted Bot API (per `telegram_io.sh` using `https://api.telegram.org`). Common pitfall — Medium article "Bypassing 50MB Downloading Limit of Telegram Bot" documents it.

**How to avoid:**
1. **Pre-flight check `file_size` on the update payload** (Telegram includes it before `getFile`). If `file_size > 19_500_000` (5 % safety margin): send a Telegram reply *"Voice note too large for Bot API (>20 MB). Split into <20 min segments or upload as document. file_size=…"* and skip processing. Idempotency-mark it so it doesn't re-trigger.
2. **At 16 kbps Opus**, 20 MB ≈ ~165 min. At default Telegram Voice quality (~64 kbps), 20 MB ≈ ~41 min. Document the per-quality threshold in OPERATIONS.md. For typical 5–15 min coaching notes the cap is irrelevant — but flag it.
3. **Plan B if user wants longer notes:** spin up a local `tdlib/telegram-bot-api` server (Docker) — removes the 20 MB cap. Phase 3+ optionality, not Phase 1.
4. **Surface to Telegram per CONVENTIONS.md loud-fail tier-3:** the user explicitly sees the rejection, not a silent skip.

**Warning signs:**
- A voice update arrives, the bot acknowledges, then nothing happens — no transcript, no error
- `runs.jsonl` has a `transcribe` row with `error="file is too big"` or empty `transcript_path`
- User Telegram-tested a long note and got no entry

**Phase to address:** Phase 1 (Telegram voice intake — validation logic).

**Severity:** MEDIUM (rare for the 5-15 min use case, but loud-fail required)

---

### Pitfall 7: CallbackQuery 15-s answer timeout blows up draft-approval inline keyboards

**What goes wrong:**
If the voice-note pipeline uses inline keyboards for draft approval (Approve/Edit/Reject buttons under a draft preview), the bot has ~15 seconds to call `answerCallbackQuery` after the user taps the button before Telegram returns `QUERY_ID_INVALID`. If the approval handler kicks off an LLM call before answering, or if the process is briefly slow, the callback expires — the spinner on the user's phone hangs, and the user retries or gives up. Documented in `gist d-Rickyy-b/f789c75228bf...`.

**Why it happens:**
`answerCallbackQuery` is purely a UI ack (clears the loading spinner); it MUST be called within 15 s of the user tap regardless of how long the actual handling takes. Many naïve implementations: tap → handler → LLM call → answer. The LLM call alone is >15 s.

**How to avoid:**
1. **ALWAYS call `answerCallbackQuery` FIRST**, before any work. Pattern (Python):
   ```python
   def on_callback(query):
       answer_callback_query(query_id, text="Procesando…", show_alert=False)  # <100 ms
       # NOW do the slow work
       result = do_llm_thing(...)
       edit_message(message_id, new_text=result)
   ```
2. **Avoid LLM calls in the synchronous callback path entirely.** Pattern: callback updates state to `approved/edit_requested/rejected`, edits the message to "Procesando…", and a separate worker (or the same systemd unit's next tick) picks up the state change and acts. Decouples UX latency from LLM latency.
3. **If editing the message after**, note `editMessageText` has a **48-hour edit window** from the original message timestamp. If a draft sits >48 h before user approves, the edit fails. Either re-send a new message or expire pending drafts at 24 h with a Telegram alert.
4. **Existing codebase uses `telegram_io.sh:_wait_reply` (text-reply polling, not inline keyboards).** Switching to inline keyboards is a NEW surface — does not inherit `_wait_reply`'s tested behaviour. Build a separate `callback_query_loop` and unit-test the answer-first ordering.

**Warning signs:**
- User reports "the button just spun forever and never confirmed"
- Telegram returns `Bad Request: query is too old and response timeout expired or query ID is invalid`
- Approved drafts produce no follow-on action (the callback expired before state was written)
- A second tap of the same button works (because Telegram retries the update)

**Phase to address:** Phase 4 (HITL approval surface). Decide early whether to use inline keyboards or text-reply (`_wait_reply` style — already proven in repo).

**Severity:** MEDIUM (only if inline keyboards are chosen)

---

### Pitfall 8: Long-lived pending-draft state survives or doesn't survive process restart

**What goes wrong:**
User receives a "Approve this draft?" Telegram message, then goes to a meeting for 3 hours. Meanwhile: systemd-unit restart, host reboot, cron re-tick. When the user finally taps Approve, the pipeline has no in-memory recollection of which draft this was, what voice note it came from, what kb_curator's pending plan was. Either:
- (a) duplicate processing: the daemon woke up and re-issued the same draft, user is now confused which to approve
- (b) silent drop: callback handler logs `WARN: no pending draft for callback_id=...` and discards the click

**Why it happens:**
The existing pipeline solves this via LangGraph SqliteSaver (`pipeline/checkpoints.db`) — `interrupt()` pauses the run, the orchestrator re-resumes with the same `--run-id` and the checkpoint replays state up to the interrupt point. Documented in `pipeline/run.py:_get_pending_interrupt`. But the new voice-note intake is OUTSIDE the LangGraph DAG (it precedes the existing graph). It would need its own state durability.

**How to avoid:**
1. **Reuse LangGraph SqliteSaver from voice-note ingestion onward.** Treat "voice note received" as the new graph entry node; `interrupt()` for approval works exactly as kb_curator's existing pattern. NO new persistence layer needed. This is the recommendation.
2. **If a separate state store is needed** (e.g. for the pre-LangGraph intake step): use SQLite (`pipeline/state/voicenote_intake.db`), NOT JSONL. JSONL is fine for telemetry append-only but NOT for state mutation (`approved → false → true` requires atomic update + reads-after-writes). Schema: `pending_drafts(draft_id TEXT PRIMARY KEY, run_id TEXT, telegram_message_id INTEGER, state TEXT CHECK(state IN ('pending','approved','rejected','expired')), created_at, expires_at, plan_json TEXT)`.
3. **NEVER use Telegram's `reply_to_message_id` as state.** Tempting pattern: encode draft state in a markdown table inside the Telegram message, edit on approve. Fails because: (a) Telegram message ≠ source of truth, (b) message edits can fail, (c) the message can be deleted by the user. State lives in SQLite; Telegram is just a UI surface.
4. **Set a 24-h expiry** on pending drafts; a watchdog (sibling of `check_daily_brief_freshness.sh`) Telegram-pings the user when a draft is about to expire so they don't miss it.
5. **Crash-recovery on startup:** scan SQLite for `state='pending'`; if `created_at > 24h ago` → mark `expired` and Telegram the user "Draft from voice-note … expired without approval, dropping." Loud-fail per CONVENTIONS.md tier-2.

**Warning signs:**
- User: "I tapped Approve but nothing happened"
- Telegram shows two identical draft-approval messages
- `pipeline/checkpoints.db` has orphan threads with `state='pending_interrupt'` that never resume
- Pending draft messages older than 24 h still in the chat

**Phase to address:** Phase 4 (draft approval). Architectural decision in Phase 0/1 — reuse SqliteSaver vs new store.

**Severity:** HIGH

---

### Pitfall 9: Vault submodule write race — existing pipeline + voice-note module writing simultaneously

**What goes wrong:**
`CONCERNS.md [high]` already documents the vault submodule is dirty after every existing pipeline run (kb_curator writes, never commits). Adding the voice-note module compounds: both writers can land entries on the same date, same theme file, same `_index.md` simultaneously. Failure modes:
- Two `kb_curator` invocations writing `obsidian-vault/gonzalo-book/themes/deliberate-discomfort.md` concurrently → last-writer-wins, one entry's append is lost
- Both writing `_index.md` or `book-outline.md` → markdown table corruption (interleaved lines)
- Submodule `git add -A && git commit` from path A while path B is mid-write → partial commit; the next pull sees inconsistent vault

**Why it happens:**
The existing pipeline assumes single-writer (one video at a time through the DAG). The voice-note flow runs on Telegram cadence, which is user-driven and unsynchronized with the video flow. CONCERNS.md confirms there is NO write coordination today.

Git submodule docs note that operations across submodules can race; checkout-level parallelism has collision detection (`PC_ITEM_COLLIDED`) but `write_text` from Python has none.

**How to avoid:**
1. **Single-writer lock on the vault.** Use `flock(2)` on a file like `obsidian-vault/.kb-write.lock`:
   ```python
   import fcntl
   with open(VAULT_ROOT / ".kb-write.lock", "w") as lf:
       fcntl.flock(lf, fcntl.LOCK_EX)  # blocks
       try:
           # all vault writes here
       finally:
           fcntl.flock(lf, fcntl.LOCK_UN)
   ```
   Both pipelines respect the lock; whichever gets it first writes + commits + bumps pointer; the other waits.
2. **Atomic per-file writes.** `write_text` then `os.rename` to target (POSIX-atomic on same filesystem). For append-only files (`_index.md`, `book-outline.md`): read-modify-write inside the lock; never plain append from two processes.
3. **Auto-commit on every vault mutation** (close the existing CONCERNS.md `[high]` issue at the same time): each kb_curator invocation finishes with `cd VAULT_ROOT && git add -A && git commit -m "kb-curator: <entry-slug>" && git push`. Lock makes commits serial. Mainline repo bumps pointer in a separate step.
4. **NEVER write the vault from inside a LangGraph parallel branch.** Today `extract → {kb_curator, extract_image, youtube_upload}` parallel split is safe because only kb_curator writes the vault. Adding a second vault writer (e.g. a voice-note short-circuit branch) violates the disjoint-state-key invariant called out in CONCERNS.md `[med]` "Pipeline graph branches fan-out". Keep vault writes funnelled through ONE node.
5. **Single ownership of `book-outline.md` and `_index.md`**: these are global aggregates. Only one node updates them per run. The voice-note pipeline reuses the existing kb_curator node, not a sibling.

**Warning signs:**
- `git status` inside submodule shows merge-conflict-style markers (`<<<<<<< HEAD`) — interleaved writes
- A theme file is missing a recently-added entry row but the entry file exists
- `pipeline/checkpoints.db` shows two concurrent runs with vault writes
- `git log` inside the submodule has empty-bodied commits (raced commits with no diff)

**Phase to address:** Phase 3 (vault integration). Lock + auto-commit lands as one feature. Closes the `[high]` from CONCERNS.md.

**Severity:** HIGH

---

### Pitfall 10: Coaching-thought-extractor assumptions break on translated 200-word chunks

**What goes wrong:**
`coaching-thought-extractor.md` (236 lines) was prompt-engineered against fixtures in `tests/fixtures/transcript_2026-*.txt` — raw video transcripts of 200–600 words. Hidden assumptions: (a) chunk = one coherent thought, (b) input is raw Spanish (NOT translated EN), (c) Quality labels (`Strong/Moderate/Weak/Flagged`) are calibrated for video-length context. Feeding it a 200-word **chunk** of a translated 1500-word voice note breaks all three:
- Chunk may be preamble-only or application-only — Quality auto-degrades because the extractor "doesn't see the full arc"
- Translation has dampened the user's voice (Pitfall 4) — extractor flags as Generic/Weak
- Multi-chunk runs produce 3-5 extractions where the existing assumption is 1 → kb_curator HITL prompt floods the user with theme decisions

**Why it happens:**
Prompts couple silently to input distribution. CONCERNS.md `[med]` "prompts are versioned outside Python code" + CONVENTIONS.md "no CI signal on prompt edits" already flag this. Reusing without re-validating on the new distribution = silent feature drop.

**How to avoid:**
1. **Build a per-chunk extractor variant** or extend the prompt with an `<input_kind>chunk_n_of_m</input_kind>` block that the prompt understands ("If this is a chunk, do NOT mark Quality as Weak solely because the framework arc is incomplete — the orchestrator will merge chunks. Focus on the local insight.").
2. **Add a chunk-merge step** AFTER per-chunk extraction: a Sonnet call that takes the M chunk-extractions and emits ONE merged extraction-report matching the existing schema. The merged report is what kb_curator sees. Preserves the single-input contract `kb_curator` was built for.
3. **Fixture-driven validation BEFORE reuse:** add `tests/fixtures/voicenote_*_chunk_*.txt` (long-voice + its chunks + the manually-curated merged extraction). Smoke-run the extractor on every chunk + the merge step; verify the merged extraction matches the manually-curated one within tolerance. Per CONCERNS.md `[high]` "8 of 11 pipeline nodes have zero tests" — the extractor has fixture coverage; add to that set.
4. **Version-pin the prompt at the moment of reuse.** Add `version:` to frontmatter (per CONCERNS.md `[med]` "prompt versions"). Log prompt version into `runs.jsonl`. A regression on a chunk vs full-transcript path is then bisectable.
5. **NEVER silently broaden the extractor's input distribution.** If the chunk distribution requires meaningful prompt changes, fork the prompt (`coaching-thought-extractor-chunk.md`) — surfaces the change in `.claude/agents/` and in the load-site rather than hiding it in a prompt edit.

**Warning signs:**
- Quality distribution shifts from "mostly Strong on full videos" to "mostly Moderate/Weak" on voice notes
- kb_curator HITL surfaces 3-5 theme decisions per voice note (vs 1-2 per video)
- `runs.jsonl` shows extraction-retry counts >1.5× the video baseline
- Merged extraction loses signal present in any single chunk

**Phase to address:** Phase 2 (chunk pipeline). Prompt + fixtures + merge step land together.

**Severity:** HIGH

---

### Pitfall 11: kb-curator theme proliferation — 11 themes drift to 30 under voice-note volume

**What goes wrong:**
Per user memory `feedback_vault_vs_notion_themes.md`: vault stays coarse (~11 themes = book chapters). Voice notes will land entries at 3-5× the cadence of videos (Telegram lowers the friction). Each entry has a chance to propose a new theme. kb_curator HITL approves them one-by-one; without a global view, the user approves "deliberate-courage" today, "courage-as-practice" next week, "courageous-discomfort" the week after — three themes for one chapter's worth of material.

**Why it happens:**
kb_curator's vault snapshot (`pipeline/nodes/kb_curator.py:_vault_snapshot`) shows the LLM existing themes, but the *naming-similarity* judgment is human-mediated per-run. The HITL prompt does not enforce a hard cap. The existing pipeline runs slowly enough (one video / ~few days) that drift is gradual; voice notes accelerate it 5-10×.

**How to avoid:**
1. **Theme cap as a hard constraint in `kb_curator.md`:** "Vault MUST have ≤14 themes total. If proposing a new theme that would make the total >14, you MUST instead map the entry to the closest existing theme and explain the tension in the entry's `Theme Connection` section." Loud-fail per CONVENTIONS.md tier-1 if the LLM proposes one anyway.
2. **Semantic-dedup pre-check:** before HITL, compute embedding(proposed_theme.name + definition) and cosine vs every existing theme. If max similarity >0.85: surface to user as "PROPOSED: deliberate-courage / NEAREST EXISTING: deliberate-discomfort (0.91). Reuse existing?" with reuse as the default. Embedding is cheap (one OpenAI/Voyage call); the user explicitly approves a proliferation only when justified.
3. **Per-month theme-introduction quota:** track `theme_created_at` in `themes.db`; if >2 new themes were approved in the trailing 30 days, the next HITL prompt is decorated with a yellow flag "QUOTA: 2 new themes in 30 d. Proceeding to add a third — confirm proliferation is intentional."
4. **Periodic theme-consolidation report** (Notion or Telegram, weekly): list themes with <3 entries and ask "merge into a coarser theme?" Closes the drift loop.
5. **Per user memory: vault stays coarse, Notion stays fine.** Voice-note entries can attach to MANY Notion sub-themes without inflating vault themes. The kb_curator-vs-notion-research split (existing) does this correctly today; preserve it.

**Warning signs:**
- `ls obsidian-vault/gonzalo-book/themes/ | wc -l` trends upward week-over-week
- Three theme files with similar names ("incremental-progress" vs "incremental-exposure" — already exists in CONCERNS.md submodule-dirty list!)
- Themes with `Entry count: 1` accumulating
- User comment "wait, didn't I already have a theme for that?"

**Phase to address:** Phase 3 (kb_curator integration). Semantic-dedup is a new feature; ships before voice-note volume ramps.

**Severity:** HIGH (compounds; expensive to recover)

---

### Pitfall 12: Notion backfill — pagination + 3 req/s + recursive children = hours, not seconds

**What goes wrong:**
Backfilling old voice-note-equivalent entries from Notion (e.g. importing a year of legacy notes into the new pipeline) reads thousands of pages with nested toggles and embedded blocks. Notion enforces ~3 req/s per integration (HTTP 429 with Retry-After); every page requires (a) `pages.retrieve` for properties, (b) `blocks.children.list` (first-level only, paginated 100/req), (c) recursive `blocks.children.list` for each toggle/quote/callout. Documented in mymcpshelf.com "Notion 25-Reference Limit" research and dev.to "Notion API Rate Limits Are Breaking Your Automation". A page with 250 root blocks + 50 toggles × 150 children = 103 API calls = >34 s minimum at 3 req/s.

**Why it happens:**
The existing `pipeline/notion_client.py` paces at 0.4 s (per CONCERNS.md `[med]`) but is single-page-write biased (notion_blog, notion_research). Backfill is a fundamentally different access pattern — read-heavy, recursive, paginated. Reusing the same hard-coded pacing without exponential backoff on 429 means: a sustained backfill that touches 1000 pages hits 429 around minute 10, blows through Retry-After ignoring the hint, gets throttled to ~1 req/s for the rest of the run, and takes 4× longer than projected.

**How to avoid:**
1. **Respect `Retry-After` header on 429.** Use `tenacity` (already a CONCERNS.md fix recommendation):
   ```python
   @retry(retry=retry_if_exception_type(HTTPError), wait=wait_exponential_jitter(initial=1, max=60), stop=stop_after_attempt(8))
   def notion_call(...): ...
   ```
   Read `response.headers["Retry-After"]` on 429 and sleep that long specifically.
2. **Centralise pacing** — close CONCERNS.md `[med]` "_PACE_SECONDS = 0.4 is conservative but uniform" by moving pacing into one module. Both backfill and live writes import from it.
3. **Paginate correctly:** loop `while has_more: cursor = response.next_cursor`. 100 items max per request. NEVER ignore `has_more=true` (silent data loss is the canonical pagination bug).
4. **Recursive block fetch:** for any block with `has_children=true`, recurse. Cap recursion depth at 8 — Notion supports arbitrary nesting but >8 is almost always a corrupted page; loud-fail per CONVENTIONS.md.
5. **Idempotency on backfill re-runs:** key on `notion_page_id` + `last_edited_time`. If a page is already imported and its `last_edited_time` hasn't changed, skip. Crucial — backfill WILL be interrupted (cron tick, manual ctrl-C); resume MUST be cheap.
6. **Cost forecast BEFORE backfill** per user memory `feedback_cost_forecast_before_replay.md`. Project: N pages × ~5 API calls/page + LLM extraction tokens. Quota share against `PRO_MAX5_SONNET_MSGS_PER_5H`. Refuse to start if projection exceeds budget.

**Warning signs:**
- HTTP 429 in logs (the `print()`s from `pipeline/notion_client.py` will surface it)
- Backfill ETA estimate diverges >2× from actual progress
- Missing pages in backfill output → likely a `has_more=true` that was dropped
- Notion rate-limit cooldown propagates to the live daily-brief (concurrent integration use)

**Phase to address:** Phase 5+ (backfill — explicit user feature). Pacing centralisation can land earlier as a CONCERNS.md fix.

**Severity:** MEDIUM (only if backfill is implemented; existing live writes are fine)

---

### Pitfall 13: Cron-poll + systemd-unit overlap = double-processing

**What goes wrong:**
If the voice-note intake is added as a cron job (`*/2 * * * * python -m pipeline.voicenote_intake`) and the existing daily-brief systemd timer is independent, two issues:
- (a) Two `voicenote_intake` invocations overlap (slow Whisper run still going when next cron tick fires) → both poll `getUpdates`, both see the same un-ack'd update, both transcribe. Pitfall 5's update_id persistence helps but only if the offset is bumped BEFORE Whisper runs.
- (b) Voice-note intake conflicts with daily-brief on the vault submodule (Pitfall 9) — both want to commit.

**Why it happens:**
cron has no built-in serialisation. Documented widely — `flock`, `run-one`, Cronitor's "Prevent duplicate cron executions" — but easy to forget.

**How to avoid:**
1. **`flock` on every cron entry**, no exceptions:
   ```
   */2 * * * * /usr/bin/flock -n /tmp/painforwisdom-voicenote.lock /usr/bin/python -m pipeline.voicenote_intake
   ```
   `-n` exits immediately if lock held — better than queueing (a queued long-running cron job is its own pathology).
2. **Prefer systemd-user units over cron** for new long-running tasks (matches existing pattern per OPERATIONS.md). Systemd's `Type=oneshot` + `RemainAfterExit=no` + a `.timer` with `OnCalendar=*:0/2` + `Restart=no` is naturally non-overlapping (a timer won't fire a service that's already running). Existing daily-brief unit demonstrates the pattern.
3. **NEVER mix cron and systemd-timer for the same task.** The CONCERNS.md daily-brief watchdog uses cron deliberately (survives systemd-user failures), but the watchdog is read-only — no state mutation. Voice-note intake mutates state → use systemd-timer + flock as belt-and-suspenders.
4. **Hold the vault lock (Pitfall 9) for the shortest possible window.** Long-held locks magnify the overlap window.

**Warning signs:**
- `ps -ef | grep voicenote_intake` shows >1 process
- Same voice note transcribed twice (also Pitfall 5's signal)
- Vault submodule has interleaved commit messages
- `data/.voicenote_intake.lock` (if created without `flock`) accumulating stale lockfiles

**Phase to address:** Phase 1 (intake scheduling). Locking lands with the intake.

**Severity:** MEDIUM

---

### Pitfall 14: Privacy leak — voice contents into logs / Telegram errors

**What goes wrong:**
A Whisper failure, kb_curator format error, or LLM rate-limit surfaces an error message that includes the transcript prefix (or a chunk of it). Existing pattern in CONCERNS.md: `_format_error_prompt` truncates exception messages at 800 chars — but 800 chars of Spanish coaching content is ~140 words, which is identifying (names, places, specific situations). That truncated content is:
- Sent to Telegram on retry escalation
- Logged to `runs.jsonl` (gitignored, but readable to anyone with host access)
- Written to `processed/<run_id>/*.txt` (gitignored, persists indefinitely)
- Included in error-recovery prompts back to Anthropic (their data-retention policy applies)

**Why it happens:**
The existing pipeline's content (video transcripts) is mostly the same user, same risk. Voice notes raise the risk surface: more frequent, more personal, less curated.

**How to avoid:**
1. **Strip transcript content from error messages before they leave the pipeline boundary.** Add a `_redact_transcript(msg, transcript_path)` helper: replace any matching substring of length >40 chars with `<REDACTED transcript:NNN chars>`. Apply in `_format_error_prompt`, in Telegram-send wrappers, in `runs.jsonl` writers.
2. **Telegram error messages ONLY identify the run_id and the failed stage**; never include transcript content. The full content is in `processed/<run_id>/transcript.txt` on the host — the user reads it locally if needed.
3. **`.gitignore` audit:** verify `processed/`, `to_be_retried/`, `data/`, and any new `voicenote-intake/` dirs are gitignored. The CONCERNS.md `[high]` `.env` audit lists patterns; extend to all new artefact paths in Phase 1.
4. **Local .ogg retention policy:** keep raw .ogg for 30 days, then delete. (User can re-download from Telegram for ~24 h via Bot API; longer retention is a privacy liability with no benefit.)
5. **NEVER log full transcripts to stdout.** Existing convention (`[stage] start/done` summaries, telemetry in JSONL) is correct; extend to voice-note paths.
6. **Anthropic data-retention awareness:** subscription auth path (per INTEGRATIONS.md "Anthropic … OAuth subscription") subjects requests to Anthropic's standard retention. Sensitive content is processed regardless — acceptable for the user's personal use but document explicitly.

**Warning signs:**
- A Telegram error notification contains Spanish prose that looks like transcript content
- `grep "personal name|place name" runs.jsonl` returns hits
- `processed/` dir size growing without bound (no retention)
- Shared-host accidentally exposes `processed/` over a misconfigured web server

**Phase to address:** Phase 1 (intake + transcribe). Redaction helper lands with the first voice-note end-to-end.

**Severity:** HIGH (privacy is non-recoverable once leaked)

---

### Pitfall 15: LLM quota burn — un-forecast voice-note volume × N stages × retry storms

**What goes wrong:**
Per user memory `feedback_cost_forecast_before_replay.md`, the user does NOT want surprise quota burns. Voice notes are higher cadence than videos AND each voice note triggers a deeper pipeline (chunk-boundary LLM + translation LLM + per-chunk extractor + chunk-merge + kb_curator + writer). A bad day: 6 voice notes × 5 LLM stages × 1.5 retries × Sonnet pricing ≈ a meaningful share of the 5-hour message cap. Existing 1M-context-beta guardrail (CONCERNS.md `[high]` "context-1m-2025-08-07 beta header gating") catches misuse but does not catch cumulative volume.

**Why it happens:**
Existing pipeline runs ~1-2 videos / day; quota was never near the cap. New voice-note path could shift to 5-10 invocations / day, each triggering more LLM stages than a video. Subscription has no $-gate but has ITPM + msgs-per-5h caps (CONCERNS.md `[med]` "No quota forecast before daily-brief").

**How to avoid:**
1. **Extend `pipeline.cost_forecast`** (per user memory + CONCERNS.md `[med]`) with a `--voicenote` mode that projects per-note tokens (boundary + translation + extraction × N chunks + merge + kb_curator). Run before kicking off a batch (e.g. catching up a backlog of 12 voice notes after a trip).
2. **Daily-budget gate**: before each new voice-note triggers, sum tokens-spent-in-trailing-5h from `runs.jsonl`. If projected next-run pushes over 80 % of `PRO_MAX5_SONNET_MSGS_PER_5H`, defer the run (queue it) and Telegram the user "Voice note received, deferred 30 min for quota". Loud-fail per CONVENTIONS.md tier-3.
3. **Cache aggressively** — chunk-boundary LLM and translation are deterministic given input; key on `sha256(transcript_text)`. A retry that re-translates is a 1-line fix that saves 25 % of tokens.
4. **Bound retry counts at the orchestrator level** — CONCERNS.md already calls out `pipeline/retry.py:_resume_graph` uses `_ask_indefinitely` not `_ask_bounded`; fix that BEFORE adding voice-note paths or the unbounded-retry storm risk multiplies.
5. **Surface every cost forecast in Telegram** at run start, not just stage-done: "Voicenote received, est. 12 k input / 4 k output tokens, ~3 % of trailing-5h quota". Visibility prevents surprises.

**Warning signs:**
- Anthropic returns `rate_limit_error` (not `context-1m` flavor — the cumulative-messages flavor) — CONCERNS.md's classifier catches it, but the budget should have caught it 1 hr earlier
- `cost_usd` column in `runs.jsonl` summed for the day exceeds the user's mental budget
- Voice note triggers >2 extraction retries (format errors) — token-doubling per CONCERNS.md `[med]` kb_curator retry doubling

**Phase to address:** Phase 0 (cost-forecast extension), Phase 2 (per-stage budget gates). Both block Phase 4+ user-facing rollout.

**Severity:** HIGH (per explicit user memory rule)

---

### Pitfall 16: Voicepal residual integration — webhooks, scheduled syncs, callbacks

**What goes wrong:**
The user is migrating off Voicepal (the existing third-party voice-note app). If Voicepal had any of: (a) a webhook into the user's host or a cloud function, (b) a scheduled "sync to Notion/Obsidian" that the user enabled, (c) email digests, (d) a calendar integration — those will continue to fire after subscription kill if not explicitly disabled. Symptoms: duplicate vault entries (Voicepal-written + new-pipeline-written for the same voice note), confused Notion state, mysterious Notion rows authored by an unfamiliar integration user.

**Why it happens:**
SaaS apps often retain webhooks/callbacks beyond subscription cancellation, especially scheduled syncs that the SaaS doesn't remove on its own. Common discovery pattern: "I cancelled the sub but stuff is still happening."

**How to avoid:**
1. **Inventory Voicepal integrations BEFORE killing the sub.** Concrete checklist (Phase 0):
   - Notion: integrations list (Settings → Connections) — revoke any Voicepal integration
   - Obsidian: plugin list — uninstall Voicepal sync plugin if present
   - Email: search inbox for `voicepal.com` / `voicepal.app` → unsubscribe from digest
   - Google Calendar: integrations → revoke Voicepal calendar access
   - Telegram: BotFather → list bots → remove any Voicepal bot from chats
   - Webhooks: search `~/.config/`, `~/.local/`, `crontab -l`, `systemctl --user list-units` for `voicepal`
   - Cloud Functions: if any (Zapier, Make.com, n8n) → list workflows touching Voicepal
2. **Run a no-op observation week BEFORE migration:** disable Voicepal sync to Notion/vault, observe for 7 days — anything that still writes to those surfaces is residual and must be cleaned up.
3. **Document the kill list in OPERATIONS.md** so it's reproducible (and so the user can verify nothing was missed).
4. **Tag new pipeline writes** distinctly: vault entries get a `Created by: kb-curator (voicenote pipeline)` line; Notion rows get a `Source: painforwisdom-voicenote` tag. Easy to distinguish from Voicepal artefacts during audit.
5. **One-time audit after kill:** `find obsidian-vault -newer <kill-date>` → manually review each new file's provenance. Catches any Voicepal write that slipped past the inventory.

**Warning signs:**
- A vault entry appears that the new pipeline did not create (no `runs.jsonl` row for it)
- Notion row authored by "Voicepal integration" after sub cancellation
- Email digest from Voicepal arrives after cancellation date
- Calendar event tied to a Voicepal sync that you don't recognise

**Phase to address:** Phase 0 (kickoff / pre-flight). MUST land before the new pipeline writes anywhere — otherwise dual-write contamination is unrecoverable per-entry.

**Severity:** MEDIUM (low probability but high cleanup cost if missed)

---

## Technical Debt Patterns

Specific to this milestone's surface (existing-codebase debt is in CONCERNS.md).

| Shortcut | Immediate Benefit | Long-term Cost | When Acceptable |
|----------|-------------------|----------------|-----------------|
| Skip update_id persistence; rely on in-memory offset | Phase 1 ships 1 day faster | Re-process loop = token waste + immutable-entry crash; debug pain (Pitfall 5) | Never. Persist in SQLite from day 1. |
| Reuse `coaching-thought-extractor.md` without fixture validation on chunks | Phase 2 ships 2 days faster | Silent quality degradation across all voice notes; theme drift (Pitfall 10, 11) | Never for voice-note path. Fork prompt or extend with fixtures. |
| Naive sentence-count chunking (e.g. 20 sentences = 1 chunk) | Phase 2 ships fast, no LLM chunk-boundary call | Quality collapse on conversational Spanish; merged-three-thoughts under-segmentation (Pitfall 3) | Acceptable only as PoC validator per user memory `feedback_poc_before_migration.md`, then replaced. |
| Telegram chat reply text-only (no inline keyboard) | Phase 4 ships faster; reuses tested `_wait_reply` | Less polished UX, but avoids CallbackQuery 15-s timeout (Pitfall 7); arguably the RIGHT default | Acceptable indefinitely. Inline keyboards are an upgrade, not a requirement. |
| Skip ES→EN translation (extract directly from Spanish) | Removes an entire LLM stage; no voice-flattening risk | Vault entries become bilingual (or fully Spanish) — breaks current EN-only convention | Recommended primary path per Pitfall 4. Explicit user decision required (no silent drop). |
| No semantic-dedup on theme proposals; rely on HITL only | Phase 3 ships 1 day faster | 11 themes → 30 themes within months (Pitfall 11); manual consolidation cost | Acceptable for first 2 weeks of rollout; observe drift, add semantic-dedup as Phase 3.5 hardening. |
| Reuse existing vault-write codepath without lock | Phase 3 ships faster; matches existing single-writer assumption | Race conditions when daily-brief + voice-note concurrent (Pitfall 9) | Never. flock is 10 lines of code. |
| JSONL for pending-draft state (append-only) | Append-friendly, easy to inspect | Concurrent reads-after-writes inconsistent; state mutation is a SQLite job (Pitfall 8) | Never for state. JSONL only for telemetry. |
| Skip Voicepal integration inventory | Phase 0 ships fast; sub-kill is "just cancel the card" | Residual-write contamination, unrecoverable per-entry (Pitfall 16) | Never. 1 hr of inventory work. |
| Same `WHISPER_MODEL=medium` for voice + video | One config to manage | Code-switching Spanish quality on voice notes is worse than on rehearsed video speech (Pitfall 2) | Acceptable until quality complaints; have `WHISPER_MODEL_VOICENOTE=large-v3` ready as a 1-env-var swap. |

---

## Integration Gotchas

| Integration | Common Mistake | Correct Approach |
|-------------|----------------|------------------|
| Telegram Bot API `getFile` | Assume 50 MB ceiling matches `sendDocument` | 20 MB cap on getFile; pre-flight `file_size`; reject early with Telegram reply (Pitfall 6) |
| Telegram `getUpdates` | Re-poll without `offset` after process restart → reprocess everything | Persist `update_id` to SQLite write-ahead of any side-effect (Pitfall 5) |
| Telegram `answerCallbackQuery` | Run LLM call before answering → 15-s timeout, spinner hangs | Answer-first pattern; LLM in async worker (Pitfall 7) |
| Telegram message edits | Edit a draft 49 h later → 400 | `editMessageText` 48-h window; expire pending drafts at 24 h |
| Whisper local | Run `medium` on code-switched Spanish without `--language es` | Hard-pin `--language es`, `--initial_prompt` with code_switch_glossary (Pitfall 2) |
| Whisper local | Trust segment timestamps after silence-padded audio | Pre-VAD trim silences ≥2 s; boilerplate blocklist post-strip (Pitfall 1) |
| Whisper sample rate | Feed 44.1 kHz wav and 16 kHz ogg through same pipeline | Always resample to 16 kHz mono before `extract_transcription.sh` invocation |
| Notion REST API | Single global rate budget shared across daily-brief + voice-note backfill | Centralise pacing; exponential-backoff on 429 reading `Retry-After` (Pitfall 12, also CONCERNS.md `[med]`) |
| Notion REST API | Ignore `has_more=true` → silent data loss | Loop on `next_cursor` until `has_more=false` (Pitfall 12) |
| Notion block recursion | Read first-level children only; miss content nested in toggles | Recurse on `has_children=true` with depth cap (Pitfall 12) |
| Git submodule | `kb_curator` writes but never commits; voice-note module compounds dirtiness | Auto-commit per node + flock for serialisation (Pitfall 9 + CONCERNS.md `[high]`) |
| LangGraph parallel branches | Add a second vault-writer branch in `pipeline/graph.py` | Funnel ALL vault writes through one node; new state keys for new branches (CONCERNS.md `[med]`) |
| LangGraph SqliteSaver | Use a separate state store for voice-note intake instead of SqliteSaver | Reuse SqliteSaver as the durability story for ALL pending-state (Pitfall 8) |
| Anthropic API | Forget `long_context=True` on a >180 k input voice note | Per CONCERNS.md `[high]` 1M-context gating — add the guardrail warning |
| Anthropic API | No subscription budget gate; rely only on Anthropic returning 429 | Local 5-h running token budget; defer + Telegram before hitting cap (Pitfall 15) |
| Cron | Add voice-note intake to crontab without `flock` | `flock -n` always, OR systemd-user timer (Pitfall 13) |
| Voicepal (legacy) | Cancel subscription without revoking integrations | Pre-kill inventory of Notion/Calendar/Telegram/webhook integrations (Pitfall 16) |

---

## Performance Traps

Voice notes will run faster than videos (no 30-min Whisper baseline) but several traps grow with volume.

| Trap | Symptoms | Prevention | When It Breaks |
|------|----------|------------|----------------|
| Re-translate same transcript on every retry | Token cost 2-3× expected | Cache by `sha256(transcript_text)` (Pitfall 4) | First batch retry storm — likely week 1 of rollout |
| Whisper CPU fallback on contended GPU | Voice note >10 min wall-clock; user notices "the bot is slow" | CONCERNS.md `[high]` already calls out — surface CPU-fallback flag in validator | First time another process holds the GPU |
| Vault `git push` on every entry serialised through flock | If 4 voice notes arrive in 1 hour, 4 pushes serially | Batch-commit: hold the lock briefly, commit, push at end of a debounced 60-s window | At ~5+ notes/hour cadence |
| Notion 3 req/s shared budget | Daily-brief stalls when voice-note backfill is running | Centralised pacer with priority lanes (live writes > backfill reads); pause backfill during brief window (Pitfall 12) | Anytime concurrent backfill + brief |
| Theme proliferation forces longer kb_curator HITL prompts | More themes → larger vault snapshot → more tokens per kb_curator call | Theme cap + semantic dedup (Pitfall 11) | At ~20+ themes |
| `processed/` directory growth | Disk fills; backups slow | 30-day retention sweep (Pitfall 14) | At ~6 months of voice-note volume |
| Chunk-boundary LLM call per voice note | Adds an LLM round-trip to every note | Cheap model (Haiku/cheap-Sonnet); cache by content hash | When voice-note cadence ramps |
| SqliteSaver checkpoint DB unbounded | CONCERNS.md `[med]` already calls out — voice notes 3-5× the checkpoint volume | GC sweep on terminal verdicts older than N days (CONCERNS.md fix) | At ~3 months of voice-note volume |
| Re-poll Telegram every 5 s with HTTP keep-alive churn | Telegram tightens quota; bot starts seeing 429 on getUpdates | Long-poll with `timeout=25-30` (Telegram-recommended); ~2,880 polls/day vs 17 k (Pitfall 5 sources) | At ~ chat-volume tipping points |

---

## Security Mistakes

| Mistake | Risk | Prevention |
|---------|------|------------|
| Log full transcript prose in error escalation Telegram message | Names/places/personal content visible to anyone with chat access | Redact transcript substrings from all outbound error messages (Pitfall 14) |
| Persist .ogg files indefinitely in `data/voicenotes/` | One disk-share misconfig = full voice archive exposed | 30-day rolling delete; verify `.gitignore` covers it (Pitfall 14) |
| Ship a forked repo with hardcoded Notion DB UUIDs (CONCERNS.md `[low]`) | New voice-note paths could re-introduce; downstream forks write to user's prod DBs | Drop all default-DB fallbacks; require explicit env (CONCERNS.md fix) |
| Embed Telegram bot token in command-line args (visible in `ps -ef`) | Token theft on shared host | Use env vars only; `telegram_io.sh` already does this correctly — preserve pattern |
| Allow inline-keyboard `callback_data` to contain user-supplied content | Crafted callback could trick the handler into writing arbitrary content | `callback_data` is bot-controlled; treat user button taps as opaque tokens; lookup state by token in SQLite |
| Trust the Telegram message author without filter | Bot in multiple chats can be triggered by any chat (CONCERNS.md `[med]`) | Filter by `chat.id == VOICE_NOTE_CHAT_ID` — close the existing gap |
| Cache OAuth tokens in process for "speed" | OAuth refresh expectations break (CONCERNS.md `[med]` already calls out — re-read on every call is intentional) | Preserve the existing re-read pattern; do not optimise this away |
| Sub-kill Voicepal without revoking integrations | Residual writes (Pitfall 16) or, worse, retained API keys | Phase 0 inventory + revoke (Pitfall 16) |

---

## UX Pitfalls

| Pitfall | User Impact | Better Approach |
|---------|-------------|-----------------|
| Async draft approval with no expiry feedback | User forgets a draft from yesterday; tomorrow's draft adds to the queue | 24-h expiry + Telegram nudge at 20 h "Draft from 'voicenote 2026-05-17' expires in 4 h" (Pitfall 8) |
| Telegram confirmation "Procesando…" with no progress | User waits 8 min for Whisper; thinks bot is broken | Stage-by-stage updates: "Transcribing… (1/4)", "Extracting… (2/4)" — edit the same message |
| Approving a voice-note draft triggers 30 s of "thinking spinner" on the inline button | Pitfall 7 = QUERY_ID_INVALID; user retries; double-processes | Answer-first pattern (Pitfall 7) |
| User sees "Voice note rejected: file too large" with no remediation | User doesn't know what to do | Telegram message includes "Try splitting at the natural pause around the X:XX mark, or upload as document attachment instead" |
| Per-voice-note kb_curator HITL flood: 3 themes + 2 frameworks + 1 framework-update | User-fatigue → reflex Approve → silent theme drift (Pitfall 11) | Per-merge step (Pitfall 10) collapses M chunks → 1 extraction → 1 HITL prompt |
| Vault entry appears immediately but `Themes:` field is empty until kb_curator finishes | User opens Obsidian, sees broken entry | Atomic write — entry doesn't appear until fully populated (existing kb_curator pattern; preserve) |
| ES→EN translation drops user's signature mid-sentence | Generic-sounding entries | Translation prompt with preserve_verbatim + few-shot voice examples (Pitfall 4) |
| Bot silently ignores a voice note (Telegram says "delivered" but no transcription starts) | Confusion; user re-sends; double-process | ALWAYS send a "voice note received, processing…" reply within 2 s of getUpdates seeing it (per CONVENTIONS.md tier-3 loud-fail visibility) |

---

## "Looks Done But Isn't" Checklist

- [ ] **Telegram voice intake:** verify `update_id` is persisted BEFORE Whisper runs. Restart the process mid-Whisper → resume should NOT re-process. (Pitfall 5)
- [ ] **Whisper voice path:** verify `--language es` + initial_prompt glossary applied; verify resampling to 16 kHz mono happens for BOTH .ogg and .wav inputs. (Pitfall 1, 2)
- [ ] **Whisper hallucination gate:** verify boilerplate blocklist regex catches "Gracias por ver" / "Suscríbete" in a synthetic silence-padded fixture. (Pitfall 1)
- [ ] **Chunking:** verify boundary-LLM runs and produces chunks ≤800 words with 120-word overlap. Verify a "long preamble + framework + application" fixture extracts as 1 merged report, not 3 weak ones. (Pitfall 3, 10)
- [ ] **Translation OR direct-ES extraction:** explicit decision recorded in PROJECT.md or roadmap. No silent default. (Pitfall 4, user memory)
- [ ] **Pending draft:** verify SqliteSaver checkpoint survives a `systemctl --user restart` mid-pause; resume sees the same `interrupt()`. (Pitfall 8)
- [ ] **Vault lock:** trigger concurrent kb_curator (voice + video paths) at same second; verify both succeed and no file is corrupted. (Pitfall 9)
- [ ] **Vault auto-commit:** verify each voice-note run leaves submodule clean (`git status` shows no uncommitted vault changes). Closes CONCERNS.md `[high]`. (Pitfall 9)
- [ ] **Theme cap:** verify kb_curator HITL refuses to propose theme #15 when 14 already exist. (Pitfall 11)
- [ ] **Semantic dedup:** verify proposed "deliberate-courage" is auto-mapped to existing "deliberate-discomfort" when cosine >0.85. (Pitfall 11)
- [ ] **Notion pacing:** verify a synthetic 429 from Notion is retried with `Retry-After` honoured, not the local 0.4 s default. (Pitfall 12)
- [ ] **Cron + flock:** verify `flock -n` on every entry; verify systemd-timer-driven runs don't overlap. (Pitfall 13)
- [ ] **Redaction:** verify a forced error in transcribe → Telegram error message has no transcript prose. (Pitfall 14)
- [ ] **Cost forecast:** verify `pipeline.cost_forecast --voicenote` exists and runs pre-flight in the orchestration entry point. (Pitfall 15)
- [ ] **Voicepal inventory:** verify the kill-list checklist is in OPERATIONS.md and was executed before voice-note rollout. (Pitfall 16)
- [ ] **CallbackQuery (if inline keyboards used):** verify answer-first pattern via a synthetic 30-s-slow handler. (Pitfall 7)
- [ ] **Telegram `chat.id` filter:** verify a message in the wrong chat does NOT satisfy a pending voice-note interrupt. Closes CONCERNS.md `[med]`. (Security mistakes)
- [ ] **Validator surfaces voice-note path:** verify `validator.py` has new findings for `whisper_voice_device_fallback`, `voicenote_chunks_count`, `translation_used`, `vault_lock_wait_s`. Avoid CONCERNS.md silent-feature-drop pattern.

---

## Recovery Strategies

| Pitfall | Recovery Cost | Recovery Steps |
|---------|---------------|----------------|
| Update_id offset lost; voice notes re-processed | LOW | Restore SQLite from `data/state-backup/`; or accept ~5-10 duplicate entries, manually delete the second copy via `kb_curator` overwrite-refusal log |
| Whisper boilerplate hallucination polluted an entry | LOW (per entry) | Re-run from `to_be_retried/`; manually edit the transcript first to strip boilerplate; re-extract |
| Chunk over-segmentation produced N weak entries | MEDIUM | Delete the N vault entries (manual `rm` + submodule commit); re-run with merged transcript; re-do HITL once |
| ES→EN translation flattened voice on weeks of entries | HIGH | No automated fix. Manually re-translate and re-issue affected entries from raw transcripts in `processed/<run_id>/transcript.txt` |
| Vault submodule write race corrupted a theme file | MEDIUM | `git -C obsidian-vault checkout HEAD -- themes/<slug>.md`; manually re-append the lost entry row by reading the entry file's wikilink target |
| Theme drift: 15+ themes accumulated | HIGH | Manual consolidation: pick a target N≤14, write a consolidation plan, run a one-shot `pipeline.scripts.consolidate_themes` migration over the vault. No automated fix. |
| Notion 429 storm during backfill | LOW | Stop backfill; wait 60 s; resume with exponential backoff. State should be checkpointable per page. |
| Pending draft lost in process crash | LOW (if Pitfall 8 mitigation in place) — HIGH (if not) | SqliteSaver replays the interrupt; user re-taps approve. Without it, the user re-records the voice note. |
| Voicepal residual write into vault after kill | MEDIUM | Identify Voicepal-authored entries by `Created by:` tag absence; manually delete or migrate into kb-curator schema |
| Privacy leak: transcript prose in Telegram chat history | HIGH | Telegram history is server-side; messages can be deleted (24 h window) but not unsent. Mitigation = prevention (Pitfall 14). |
| Quota cap hit mid-run | LOW | Existing classifier (CONCERNS.md `[low]`) catches the message and aborts cleanly; resume in 5 h. With budget gate (Pitfall 15), this should be a rare event. |
| `kb_curator` HITL prompt drowning user | LOW (per-event) | Cancel run; switch to less-aggressive chunk-merge until merged-report-per-note ratio = 1 |

---

## Pitfall-to-Phase Mapping

| Pitfall | Prevention Phase | Severity | Verification |
|---------|------------------|----------|--------------|
| 1: Whisper silence hallucination + padding | Phase 1 (intake + Whisper) | HIGH | Synthetic silence-padded ES fixture; assert no boilerplate in output |
| 2: Whisper code-switching stuck in English | Phase 1 (Whisper config + glossary) | HIGH | Code-switched ES+EN fixture; WER <12 % on Spanish segments after English insertions |
| 3: Chunk over-/under-segmentation | Phase 2 (chunk-splitter) | HIGH | Long-voice fixture → assert chunk count ∈ [2..5] for 10-min input, overlap present |
| 4: ES→EN voice flattening | Phase 2 (translation OR direct-ES) | HIGH | User-explicit decision in roadmap; if translation: few-shot eval against held-out vault entries |
| 5: update_id persistence | Phase 1 (intake) | HIGH | Restart-mid-Whisper test; assert no re-processing |
| 6: 20 MB getFile cap | Phase 1 (intake validation) | MEDIUM | Synthetic large-file update payload → assert pre-flight reject + Telegram reply |
| 7: CallbackQuery 15-s timeout | Phase 4 (HITL UI, IF inline keyboards) | MEDIUM | Synthetic 30-s handler; assert answer-first pattern |
| 8: Pending-draft state durability | Phase 0 (architecture) + Phase 4 (HITL) | HIGH | SqliteSaver reuse decision; restart-mid-pause test |
| 9: Vault submodule write race | Phase 3 (vault integration) + closes CONCERNS.md `[high]` | HIGH | Concurrent run test; assert no file corruption; auto-commit on each run |
| 10: Extractor reuse on chunks | Phase 2 (chunk pipeline) | HIGH | Chunk-fixture set; merged-extraction-equals-manual-curation |
| 11: Theme proliferation | Phase 3 (kb_curator integration) | HIGH | Hard cap + semantic-dedup integration test |
| 12: Notion backfill rate-limit | Phase 5+ (backfill, if scoped) | MEDIUM | Synthetic 429 + Retry-After honoured; pagination loop on `has_more` |
| 13: Cron overlap | Phase 1 (intake scheduling) | MEDIUM | `flock` invocation in unit definition; concurrent-trigger test |
| 14: Privacy leak in logs/Telegram | Phase 1 (intake + transcribe) | HIGH | Force error; assert redaction applied to all egress paths |
| 15: LLM quota burn | Phase 0 (cost-forecast extension) + Phase 2 (budget gates) | HIGH | `pipeline.cost_forecast --voicenote` exists; per-run budget gate fires in synthetic over-budget scenario |
| 16: Voicepal residual integration | Phase 0 (pre-flight) | MEDIUM | OPERATIONS.md kill-list checklist; manual sign-off before any new pipeline write |

---

## Sources

**Domain references (web-sourced, MEDIUM confidence unless noted):**
- [Hallucination on silence — whisper.cpp #1724](https://github.com/ggml-org/whisper.cpp/issues/1724)
- [A possible solution to Whisper hallucination — openai/whisper #679](https://github.com/openai/whisper/discussions/679)
- [How Accurate Is Whisper in 2026? (WER by language) — NovaScribe](https://novascribe.ai/how-accurate-is-whisper)
- [Multi-Language Audio and Transcription Inconsistencies — openai/whisper #2009](https://github.com/openai/whisper/discussions/2009)
- [Exploring Code-Switching Translation with Whisper — Medium](https://medium.com/@komalb2002/exploring-code-switching-translation-with-whisper-2ed10d4577f0)
- [Subgen: Whisper silence-padding offset detection](https://github.com/McCloudS/subgen)
- [Telegram Bot API official docs](https://core.telegram.org/bots/api)
- [Telegram Bots FAQ](https://core.telegram.org/bots/faq)
- [getUpdates Telegram Bot: 5 Proven Fixes — BotHero](https://blog.bothero.ai/getupdates-telegram-bot-the-polling-method-that-powers-43-of-small-business-bots-why-it-breaks-at-scale-and-what-to-do-about-it)
- [tdlib/telegram-bot-api #683 — 50 MB upload / 20 MB download asymmetry](https://github.com/tdlib/telegram-bot-api/issues/683)
- [CallbackQuery 15-s timeout reproduction gist](https://gist.github.com/d-Rickyy-b/f789c75228bf00f572eec4450ed0d7c9)
- [Notion API Rate Limits Are Breaking Your Automation — DEV](https://dev.to/kanta13jp1/notion-api-rate-limits-are-breaking-your-automation-heres-the-real-fix-o5p)
- [Notion API Rate Limits 2026 Complete Guide — UnBanAI](https://www.unbanai.org/blog/notion-api-rate-limits-explained-2026)
- [Solving the Notion 25-Reference Limit in MCP — mymcpshelf](https://www.mymcpshelf.com/blog/solving-notion-25-reference-limit-mcp/)
- [Notion get-block-children official docs](https://developers.notion.com/reference/get-block-children)
- [Improving LLM Abilities in Idiomatic Translation — arXiv 2407.03518](https://arxiv.org/abs/2407.03518)
- [Chunking Strategies for LLM Applications — Pinecone](https://www.pinecone.io/learn/chunking-strategies/)
- [Chunking Strategies to Improve LLM RAG Pipeline — Weaviate](https://weaviate.io/blog/chunking-strategies-for-rag)
- [Prevent overlapping cron jobs with flock — ma.ttias.be](https://ma.ttias.be/prevent-cronjobs-from-overlapping-in-linux/)
- [How to prevent duplicate cron jobs — Cronitor](https://cronitor.io/guides/how-to-prevent-duplicate-cron-executions)
- [Telegram OGG/Opus 16 kHz format docs — voicegram README](https://github.com/frymex/voicegram)

**Codebase references (HIGH confidence — verified against the repo):**
- `/home/gonzalo/workspace/painforwisdom/painforwisdom/.planning/codebase/CONCERNS.md` — existing scar tissue (vault submodule dirty, Telegram silent degradation, 1M-context gating, error-recovery bounds, kb_curator HITL token growth)
- `/home/gonzalo/workspace/painforwisdom/painforwisdom/.planning/codebase/INTEGRATIONS.md` — auth modes, rate limits, env-var matrix
- `/home/gonzalo/workspace/painforwisdom/painforwisdom/.planning/codebase/CONVENTIONS.md` — loud-fail tiers, state-key discipline, vault-entry immutability, prompt-loading model

**User-memory references (HIGH confidence — explicit user-stated rules):**
- `user_ultra_subscription.md` — quota matters, $ does not
- `pipeline_perf_baseline.md` — performance targets framing
- `feedback_poc_before_migration.md` — PoC required for >2-day refactors
- `feedback_no_silent_feature_drops.md` — translation-vs-direct-ES is a surfaced decision, not a default
- `feedback_cost_forecast_before_replay.md` — pipeline.cost_forecast extension before voice-note batches
- `feedback_audio_overview_format.md` — orthogonal but informs the daily-brief audio path
- `feedback_vault_vs_notion_themes.md` — vault stays coarse (~11), Notion stays fine (~49); enforces theme cap

---

*Pitfalls research for: long-form voice-note → personal-vault pipeline (Telegram + Whisper-ES + LLM chunking + ES→EN + Obsidian-submodule)*
*Researched: 2026-05-18*

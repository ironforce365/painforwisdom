# Phase 0: PoC & Pre-Flight - Discussion Log

> **Audit trail only.** Do not use as input to planning, research, or execution agents.
> Decisions are captured in `00-CONTEXT.md` — this log preserves the alternatives considered.

**Date:** 2026-05-18
**Phase:** 00-poc-pre-flight
**Areas discussed:** PoC fixtures

---

## PoC fixtures

### Q1: Audio source

POC-01 needs real audio (Whisper test). 29 Voicepal subpages are text-only — cannot validate Whisper from them. What is the audio source for the ≥3 fixtures?

| Option | Description | Selected |
|--------|-------------|----------|
| Fresh recordings | Record ~3 new ES voice notes specifically for PoC. Controlled content + known ground-truth boundaries. Slowest to start, cleanest signal. | ✓ |
| Existing voice memos archive | Pull 3 long ES recordings from existing voice memo apps / Telegram history. Faster, no recording session. | |
| Mix: 1 fresh + 2 archive | 1 controlled fresh for known-boundary judging, 2 archive for variance. More org work. | |
| Voicepal subpage replay | Pick 3 Voicepal subpages whose original audio still exists, re-upload `.ogg`. Only works if audio still archived. | |

**User's choice:** Fresh recordings.
**Notes:** Gonzalo controls content, can identify ground-truth boundaries cleanly. Best signal for PoC gate.

---

### Q2: Target duration per fixture

Success criterion 1 mentions ~10-min notes; research baseline assumed 5-15 min. What duration target?

| Option | Description | Selected |
|--------|-------------|----------|
| ~10 min each | Matches success criterion 1 spec. 3 × 10 min = 30 min total. | |
| Mix: short / medium / long | 1 × ~3 min, 1 × ~10 min, 1 × ~15 min. Tests Whisper + splitter across length variance. | |
| Whatever's available | Don't gate on duration. Use what naturally comes out. | ✓ |

**User's choice:** Whatever's available.
**Notes:** Pragmatic, lower friction on PoC start. Real voice-note distribution is uneven; permissive duration policy avoids biasing the corpus.

---

### Q3: Storage location

Where do PoC audio + transcripts + hand-judged boundaries live?

| Option | Description | Selected |
|--------|-------------|----------|
| `.planning/phases/00-poc-pre-flight/fixtures/` | Colocated with phase. Audio gitignored, transcripts + boundaries committed. Disposable post-Phase-0. | ✓ |
| `voicenote/poc-fixtures/` | Inside eventual voicenote module dir. Stays as regression corpus. But pollutes module before scaffolding. | |
| `data/poc-voicenote/` | Top-level `data/` sibling. Decoupled. Good if fixtures graduate to Phase 1 test fixtures. | |

**User's choice:** `.planning/phases/00-poc-pre-flight/fixtures/`.
**Notes:** Clean separation from yet-to-be-built voicenote module. Boundary sidecars graduate as regression input for Phase 1 splitter iteration.

---

### Q4: Ground-truth boundary capture format

How is hand-judgment for POC-02's ≥80% agreement captured?

| Option | Description | Selected |
|--------|-------------|----------|
| Per-fixture markdown sidecar | `fixture-N/boundaries.md`: ordered list of `[sentence_start_idx, sentence_end_idx]` ranges with 1-line topic per chunk. | ✓ |
| JSON schema sidecar | `fixture-N/boundaries.json` with `[{start_sentence, end_sentence, topic}]`. Machine-comparable. | |
| Inline transcript annotation | Edit transcript itself with `## CHUNK 1: <topic>` markers. Visual, no separate file. | |

**User's choice:** Per-fixture markdown sidecar.
**Notes:** Human-readable + diffable + commitable. Sidecar pattern leaves transcript pristine for Whisper-output-faithful regression input.

---

### Q5: Fixture count (3 vs buffered)

Exactly 3 (success criterion minimum) or buffer to 4-5?

| Option | Description | Selected |
|--------|-------------|----------|
| Record 4-5, use ≥3 valid | Buffer protects the gate if 1 fixture is unusable. No mid-PoC re-record forced. | ✓ |
| Exactly 3 | Tight to spec. Fastest. Risk on bad fixture. | |
| Decide after first batch | Record 3, judge whether more needed. Adaptive but adds decision point mid-PoC. | |

**User's choice:** Record 4-5, use ≥3 valid.
**Notes:** Risk-buffered. Cheap insurance against one bad recording (noisy environment, sparse atomic thoughts).

---

### Q6: Recording capture path

How do PoC audio files get created? Affects Whisper input format per Pitfall 1.

| Option | Description | Selected |
|--------|-------------|----------|
| Send to new Voicenote bot via Telegram | Throwaway PTB script downloads `.ogg`. Production format end-to-end (Opus 16 kHz mono native). Validates getFile flow as side-effect. | ✓ |
| Voice memo app → file copy | Record in phone's voice memo app, transfer `.m4a/.wav` to fixtures. Different sample rate, needs ffmpeg resample. Doesn't validate Telegram path. | |
| Either / mixed | Use whatever's quickest per fixture. Pragmatic, loses Telegram-native signal. | |

**User's choice:** Send to new Voicenote bot via Telegram.
**Notes:** PTB capture script is throwaway (`Bot.get_updates` + `get_file().download_to_drive`) — NOT module scaffolding. Validates `.ogg`/Opus 16 kHz format end-to-end. Tests getFile 20 MB cap on real recordings.

---

### Q7: POC-01 'readable enough' signal

Quantitative WER sample or pure gut-check?

| Option | Description | Selected |
|--------|-------------|----------|
| Gut-check + 1 WER sample paragraph per fixture | Hand-correct ~100 words densest paragraph per fixture, compute WER, record in notes. Plus overall qualitative readability. | ✓ |
| Pure gut-check, no WER number | Yes/no per fixture on whether boundaries are identifiable. Fastest. Less defensible. | |
| Full WER on all 3 fixtures | Hand-correct every fixture. Strongest signal. Hours of work for 30 min of ES transcript. | |

**User's choice:** Gut-check + 1 WER sample paragraph per fixture.
**Notes:** Modest cost, defensible signal. Documented per-fixture WER becomes posterity signal for any future Whisper model revisit.

---

## Claude's Discretion

User explicitly deferred during PoC fixtures discussion:

- Splitter prompt placement during PoC (scratch string in PoC script vs first draft of `.claude/agents/voicenote-splitter.md`)
- POC-03 'indistinguishable from manual' comparison method (manually-written vs existing vault entry vs kb_curator quality flag)
- Cost forecast `--voicenote` shape beyond what success criterion 4 already specifies (gate-block vs print-only)
- Voicepal kill-list 7-day no-op observation tracking mechanism (calendar reminder vs dated entry vs cron status check)
- Retry-bound gap fix organization (own plan vs bundled with POC-05; `MAX_REMINDERS` value)
- PoC results consolidation document (location and shape)

User chose "I'm ready for context" instead of exploring further gray areas (pass/fail bars sharpening, retry-bound + cost-forecast scope, PoC artifacts location).

## Deferred Ideas

(See `<deferred>` section in `00-CONTEXT.md` for items deferred to Phase 1+.)

# Audio Feedback Loop — PoC Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Prove the bet — that feeding a memory-brief + the listener's response into NotebookLM yields an audio that is *continuous* (doesn't re-explain covered concepts) and *reactive* (engages what the listener actually did), instead of an isolated deep dive.

**Architecture:** A slim, throwaway experiment harness (`pipeline/scripts/poc_react_audio.py`) that reuses the existing NotebookLM publisher primitives (`_read_or_create_notebook`, `_add_source`, `_trigger_audio`, `_poll_artifact`, `_fetch_audio_url`) to upload an *arbitrary* hand-authored source set and trigger one audio with a hand-written react focus prompt. No production state, no `_memory` writes, no automation — that is the MVP, gated on this PoC passing. The PoC reuses the real `body-literacy` notebook (`5bec492c-501e-4806-81eb-a1ab808259d5`) and reacts to a real prior audio (`briefs/body-literacy/2026-06-02--mixed-angles/`, vault entry `2026-04-13-passion-as-high-performance`).

**Tech Stack:** Python 3, the existing `nlm` CLI wrappers in `pipeline/summarize_daily/notebooklm_publisher.py`, `pytest`, hand-authored markdown sources.

**Spec:** `docs/superpowers/specs/2026-06-03-audio-practice-feedback-loop-design.md` (§6 PoC).

**Why this isn't TDD-heavy:** the only unit-testable code is source-arg parsing (one test below). The `nlm` calls are glue over already-tested helpers, and the *real* test is a human listen against a rubric (Task 7). Forcing pytest onto a NotebookLM listening experiment would be theater. The gate is Task 7, not a green test bar.

---

## File Structure

| Path | Responsibility | Mode |
|---|---|---|
| `pipeline/scripts/poc_react_audio.py` | Experiment harness — assemble source set + focus prompt, trigger one audio, poll, print URL | Create (throwaway) |
| `tests/test_poc_react_audio.py` | Unit test for `parse_sources` | Create (throwaway) |
| `briefs/poc-react/coverage.md` | Hand-derived coverage of the PARENT audio (concepts + Q/P IDs) | Create (content) |
| `briefs/poc-react/memory-brief.md` | Recall slice: already-covered + open loop + protocol status | Create (content) |
| `briefs/poc-react/focus-prompt.md` | The react focus prompt (CONTINUITY + REACT + CLOSE) — the key variable | Create (content) |
| `briefs/poc-react/response.md` | The listener's response (example, replaced by Gonzalo's real reply) | Create (content) |
| `briefs/poc-react/RESULT.md` | The gate verdict + learnings | Create (Task 7-8) |

---

## Pre-flight

- [ ] **Step 0: Verify `nlm` auth and the parent notebook are reachable**

Run:
```bash
nlm studio status 5bec492c-501e-4806-81eb-a1ab808259d5 -p painforwisdom | head -c 300
```
Expected: a JSON array of artifacts (not an auth error). If it errors with auth, run `nlm auth` (interactive — type `! nlm auth` in the session) before continuing.

---

## Task 1: Experiment harness

**Files:**
- Create: `pipeline/scripts/poc_react_audio.py`
- Test: `tests/test_poc_react_audio.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_poc_react_audio.py
"""Unit test for the PoC react-audio harness source parsing."""
from pathlib import Path

import pytest

from pipeline.scripts.poc_react_audio import parse_sources


def test_parse_sources_returns_title_path_pairs(tmp_path):
    f = tmp_path / "resp.md"
    f.write_text("hi")
    pairs = parse_sources([f"response={f}"])
    assert pairs == [("response", f)]


def test_parse_sources_rejects_missing_equals(tmp_path):
    with pytest.raises(ValueError):
        parse_sources(["no-equals-sign"])


def test_parse_sources_rejects_missing_file(tmp_path):
    with pytest.raises(FileNotFoundError):
        parse_sources([f"x={tmp_path/'nope.md'}"])
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=. pytest tests/test_poc_react_audio.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'pipeline.scripts.poc_react_audio'`

- [ ] **Step 3: Write the harness**

```python
# pipeline/scripts/poc_react_audio.py
#!/usr/bin/env python3
"""PoC: generate a memory-aware REACT audio from hand-authored sources.

Throwaway experiment harness for
docs/superpowers/specs/2026-06-03-audio-practice-feedback-loop-design.md (§6).

Reuses the existing NotebookLM publisher primitives; assembles an arbitrary
source set + a hand-written focus prompt, triggers ONE audio, polls, prints the
notebook URL. NOT production: no state writes, no _memory updates. Proves the bet.

Run (from repo root):
  PYTHONPATH=. python pipeline/scripts/poc_react_audio.py \
    --theme body-literacy \
    --source "response=briefs/poc-react/response.md" \
    --source "coverage=briefs/poc-react/coverage.md" \
    --source "memory-brief=briefs/poc-react/memory-brief.md" \
    --source "vault-entry=obsidian-vault/gonzalo-book/entries/2026-04-13-passion-as-high-performance.md" \
    --focus-file briefs/poc-react/focus-prompt.md
Add --dry-run to print the assembled plan without calling nlm.
"""
from __future__ import annotations

import argparse
from pathlib import Path
from typing import List, Tuple

from pipeline.summarize_daily.notebooklm_publisher import (
    NLM_PROFILE,
    _add_source,
    _fetch_audio_url,
    _poll_artifact,
    _read_or_create_notebook,
    _trigger_audio,
)


def parse_sources(specs: List[str]) -> List[Tuple[str, Path]]:
    """Parse repeated 'TITLE=PATH' specs into (title, Path) pairs.

    Raises ValueError if a spec has no '=', FileNotFoundError if PATH missing.
    """
    pairs: List[Tuple[str, Path]] = []
    for spec in specs:
        title, sep, path = spec.partition("=")
        if not sep or not path:
            raise ValueError(f"--source must be TITLE=PATH, got: {spec!r}")
        p = Path(path)
        if not p.exists():
            raise FileNotFoundError(f"source file not found: {p}")
        pairs.append((title, p))
    return pairs


def main() -> None:
    ap = argparse.ArgumentParser(description="PoC react-audio generator")
    ap.add_argument("--theme", required=True,
                    help="theme whose notebook to reuse (briefs/<theme>/.notebooklm-id)")
    ap.add_argument("--source", action="append", required=True, metavar="TITLE=PATH",
                    help="repeatable; e.g. --source 'response=briefs/poc-react/response.md'")
    ap.add_argument("--focus-file", required=True, type=Path,
                    help="path to the focus-prompt markdown")
    ap.add_argument("--length", default="long", choices=["short", "default", "long"],
                    help="audio length (default: long)")
    ap.add_argument("--dry-run", action="store_true", help="print plan without calling nlm")
    args = ap.parse_args()

    pairs = parse_sources(args.source)
    focus = args.focus_file.read_text().strip()

    print(f"profile : {NLM_PROFILE}")
    print(f"theme   : {args.theme}")
    print(f"length  : {args.length}")
    print("sources :")
    for title, p in pairs:
        print(f"  - {title}: {p}")
    print(f"focus   : {len(focus)} chars")

    if args.dry_run:
        print("\n[dry-run] not calling nlm.\n--- focus prompt ---")
        print(focus)
        return

    nb_id = _read_or_create_notebook(args.theme)
    print(f"notebook: {nb_id}")
    source_ids: List[str] = []
    for title, p in pairs:
        sid = _add_source(nb_id, p, f"poc-react: {title}")
        print(f"  added {title} -> {sid}")
        source_ids.append(sid)

    artifact_id = _trigger_audio(nb_id, source_ids, focus, length=args.length, fmt="deep_dive")
    url = f"https://notebooklm.google.com/notebook/{nb_id}"
    print(f"artifact: {artifact_id}")
    print(f"notebook url: {url}")

    status = _poll_artifact(nb_id, artifact_id)
    print(f"status  : {status}")
    if status == "completed":
        try:
            print(f"audio   : {_fetch_audio_url(nb_id, artifact_id)}")
        except Exception as exc:  # noqa: BLE001 - non-fatal in a PoC
            print(f"audio url fetch failed (non-fatal): {exc}")


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONPATH=. pytest tests/test_poc_react_audio.py -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add pipeline/scripts/poc_react_audio.py tests/test_poc_react_audio.py
git commit -m "poc: react-audio experiment harness (source-set + focus over nlm)"
```

---

## Task 2: Hand-derive the parent audio's `coverage.md`

**Files:**
- Create: `briefs/poc-react/coverage.md`

This is the new `coverage.md` artifact, hand-derived for the PoC from the parent audio's `deep-dive.md`/`application.md` (in MVP it is auto-written at trigger-time). Questions and protocols get stable IDs.

- [ ] **Step 1: Write the file**

```markdown
---
kind: audio-coverage
audio: A-2026-06-02-body-literacy-mixed-angles
derived-from: "[[2026-04-13-passion-as-high-performance]]"
theme: body-literacy
generated: 2026-06-02
---

# Coverage — body-literacy / mixed angles (2026-06-02)

Hand-derived for the PoC from this audio's deep-dive.md + application.md.

## Concepts covered (do NOT re-explain — already at depth)
- self-compassion vs self-esteem (Neff) — the inner critic collapses motivation exactly when execution is needed
- action-awareness merging / flow (Csikszentmihalyi) — psychic energy stops leaking into self-monitoring; Hamilton's decreased-cortical-activation finding under concentration
- β2-adrenergic downregulation; the plateau / overtraining / taper-readiness diagnostic split (Galpin)
- the calibration problem — productive discomfort vs self-punishing entropy, both feel hard
- the outward-perceptual-bandwidth test — in-body flow check

## Protocols proposed
- P-2026-06-02-a — add a diagnostic layer before applying ease: under-recovered (unload) / under-challenged (novel stress) / self-evaluation-leaking (Neff trio). Same symptom, three protocols; self-compassion is the stance, the effort dose changes.
- P-2026-06-02-b — operationalize the whisper as Neff's THREE moves (self-kindness, common humanity, mindfulness), not one.
- P-2026-06-02-c — use the outward-perceptual-bandwidth test on the trail as the real-time flow signal: if he can't notice breeze/dirt/breath, he's leaking to self-monitoring.

## Open question asked (the close)
- Q-2026-06-02-a — when the body whispers "don't be too hard on yourself," is it self-evaluation leakage (drop the critic, keep the effort), endocrine collapse (drop the effort, take a week), or under-challenge (raise the stakes) — and what's the somatic tell that distinguishes the three BEFORE he commits to a response?
```

- [ ] **Step 2: Commit**

```bash
git add briefs/poc-react/coverage.md
git commit -m "poc: hand-derived coverage for parent body-literacy audio"
```

---

## Task 3: Author the response (the listener's reply)

**Files:**
- Create: `briefs/poc-react/response.md`

This stands in for the transcribed Telegram voice reply. The example below is written to genuinely advance the open question (it proposes a candidate somatic tell), so the react-audio has real material. **For the felt-practice test, Gonzalo replaces the body with his actual recorded reply.**

- [ ] **Step 1: Write the file**

```markdown
---
kind: response
id: R-2026-06-02-a
responds-to: "[[A-2026-06-02-body-literacy-mixed-angles]]"
answers: [Q-2026-06-02-a]
reports:
  - id: P-2026-06-02-c
    status: tried
    result: see body
has_fresh_content: true
recorded: 2026-06-03
---

# Response to the body-literacy / mixed-angles audio

> EXAMPLE response for the PoC mechanism test. Replace this body with Gonzalo's
> actual recorded Telegram reply (transcribed) for the felt-practice test.

I tried the outward-perceptual-bandwidth test on Saturday's long run. About 90
minutes in, the guilt spiral hit — I'd missed Thursday and Friday and my legs
felt heavy. I asked the test question: could I notice the breeze, the dirt, the
breath? At first, no — I was completely inside my head, scoring myself. So I
deliberately widened attention to the trail. Within about two minutes the legs
felt lighter, "more air, more fuel," like the April entry. So the bandwidth test
worked as a SIGNAL: I genuinely couldn't perceive outward while I was leaking to
self-monitoring.

But here's where the open question bites. After I dropped the critic and kept the
effort, the heaviness came back twenty minutes later — and this time widening
attention did nothing. That second wave didn't feel like self-evaluation. It felt
physical. The same intervention that worked at minute 90 failed at minute 110. I
think the first was self-evaluation leakage and the second was actual fatigue. The
somatic tell I noticed: when it was leakage, the heaviness LIFTED as soon as
perception widened; when it was real, widening changed nothing. So "does it
respond to attention?" might be the tell — leakage is attention-responsive,
endocrine collapse is not.
```

- [ ] **Step 2: Commit**

```bash
git add briefs/poc-react/response.md
git commit -m "poc: example response advancing the calibration open-loop"
```

---

## Task 4: Author the recall slice (`memory-brief.md`)

**Files:**
- Create: `briefs/poc-react/memory-brief.md`

- [ ] **Step 1: Write the file**

```markdown
---
kind: memory-brief
for-audio: react to R-2026-06-02-a
theme: body-literacy
generated: 2026-06-03
---

# Memory brief — react-audio for body-literacy

Recall slice. In MVP this is auto-generated by the recall selector; hand-built
for the PoC.

## Already covered — do NOT re-explain (assume the listener has these)
- Neff: self-compassion vs self-esteem; inner-critic-collapses-motivation
- Csikszentmihalyi: action-awareness merging + Hamilton decreased-cortical-activation
- Galpin: β2-adrenergic / taper / overtraining / plateau distinctions
- the outward-perceptual-bandwidth test (mechanism already taught)

State these as settled ground. Reference by name; do NOT define them again.

## Open loop being advanced
- Q-2026-06-02-a — the three-way calibration question (self-evaluation leakage vs
  endocrine collapse vs under-challenge) and the missing somatic tell.

## Protocol status (from the response source, R-2026-06-02-a)
- P-2026-06-02-c (outward-perceptual-bandwidth test) — TRIED. Result is in the
  response source. Build on what actually happened; do NOT re-pitch the protocol.

## Forward mandate
Open by engaging his reported result. Advance Q-2026-06-02-a using HIS lived data
(he proposes "attention-responsiveness" as the tell — test and sharpen it). Close
on a NEW, sharper question or a refined protocol — never Q-2026-06-02-a verbatim.
```

- [ ] **Step 2: Commit**

```bash
git add briefs/poc-react/memory-brief.md
git commit -m "poc: hand-built memory-brief recall slice"
```

---

## Task 5: Author the react focus prompt (the key variable)

**Files:**
- Create: `briefs/poc-react/focus-prompt.md`

This is the prompt under test. If the PoC fails, this is the first thing to tune.

- [ ] **Step 1: Write the file**

```markdown
You are generating a REACT audio overview for ONE specific listener: GONZALO, an ultra-distance runner and father of three children whose deliberate-discomfort practice grew out of his daily life. This is NOT a standalone deep dive. It is the NEXT BEAT in an ongoing practice on the `body-literacy` theme.

CONTINUITY (read the "memory-brief" source first):
- The listener has ALREADY covered, at depth, every concept under "Already covered" in the memory brief. Treat them as settled ground. Reference them by name. Do NOT define or re-explain them. If you start introducing Neff, Csikszentmihalyi, Galpin, or the outward-perceptual-bandwidth test as if they were new, STOP — assume he knows them cold.

REACT (the "response" source is the spine):
- The listener responded to the last audio. OPEN by engaging what he actually reported in the "response" source — his real experience and his take on the open question. Quote his words. Do NOT open with a recap, a definition, or "welcome back."
- He TRIED protocol P-2026-06-02-c (the outward-perceptual-bandwidth test). Engage his reported RESULT directly. Do NOT re-pitch the protocol as if proposing it for the first time.

ADVANCE (the open loop):
- The open question Q-2026-06-02-a is the three-way calibration problem (self-evaluation leakage vs endocrine collapse vs under-challenge) and the missing somatic tell. He proposes a candidate tell: "does the heaviness respond to attention?" Take that seriously, pressure-test it, and push it forward using his lived data. Where would attention-responsiveness fail as a tell?

CLOSE:
- End on a SHARPER, NEW question or a refined protocol that follows from his result. Do NOT restate Q-2026-06-02-a verbatim. The close becomes his next open loop.

Reference Gonzalo BY NAME at least eight times. No motivational framing. No hedging. No "the lesson here is." Dense, direct, and continuous with what came before. The audio is FOR him, ABOUT him, and assumes everything the practice has already established.
```

- [ ] **Step 2: Commit**

```bash
git add briefs/poc-react/focus-prompt.md
git commit -m "poc: react focus prompt (continuity + react + close)"
```

---

## Task 6: Dry-run the harness (verify assembly before spending an audio)

- [ ] **Step 1: Dry-run**

Run:
```bash
PYTHONPATH=. python pipeline/scripts/poc_react_audio.py \
  --theme body-literacy \
  --source "response=briefs/poc-react/response.md" \
  --source "coverage=briefs/poc-react/coverage.md" \
  --source "memory-brief=briefs/poc-react/memory-brief.md" \
  --source "vault-entry=obsidian-vault/gonzalo-book/entries/2026-04-13-passion-as-high-performance.md" \
  --focus-file briefs/poc-react/focus-prompt.md \
  --dry-run
```
Expected: prints `profile`, `theme`, four `sources` lines, a `focus : NNN chars` line, then the full focus prompt under `--- focus prompt ---`. No errors, no `nlm` call.

---

## Task 7: Generate and evaluate (THE GATE)

**Files:**
- Create: `briefs/poc-react/RESULT.md`

- [ ] **Step 1: Generate the react-audio**

Run the same command as Task 6 **without** `--dry-run`. Expected: prints `notebook`, four `added … -> <id>` lines, `artifact`, `notebook url`, then polls for up to ~15 min and prints `status : completed` and an `audio` URL. (If it prints `status : rendering`, that's fine — open the notebook URL and wait.)

- [ ] **Step 2: Listen on a run, score against the rubric**

The gate is a human listen. Score each (yes/no):
1. **Continuity** — does it treat Neff / Csikszentmihalyi / Galpin / the bandwidth test as established, WITHOUT re-defining them?
2. **Reaction** — does it open on the listener's reported result, in his words, not a recap?
3. **Non-repetition** — does it avoid re-pitching P-2026-06-02-c and re-explaining covered concepts?
4. **Forward motion** — does it pressure-test the proposed tell and close on a NEW question (not Q-2026-06-02-a verbatim)?

**PASS = yes on 1, 2, 3.** (4 is strongly desired but not the gate.)

- [ ] **Step 3: Record the verdict**

Write `briefs/poc-react/RESULT.md` with: the notebook URL, the four yes/no scores with a one-line note each, and a one-line VERDICT (PASS / FAIL).

```bash
git add briefs/poc-react/RESULT.md
git commit -m "poc: react-audio result + rubric verdict"
```

---

## Task 8: Decide — iterate or green-light MVP

- [ ] **Step 1: Branch on the verdict**

- **If FAIL on continuity/non-repetition:** the focus prompt isn't holding. Tune `briefs/poc-react/focus-prompt.md` (strengthen the "do NOT re-explain" framing; consider dropping the full vault-entry source if it's pulling the audio back into first-principles) and re-run Task 7. NotebookLM also weights source SIZE — if `coverage.md`/`memory-brief.md` are dwarfed by the vault entry, trim the entry. Log each iteration's change + result in `RESULT.md`.
- **If FAIL on reaction:** the response source isn't being treated as the spine. Move it first in `--source` order, sharpen the REACT block, and re-run.
- **If PASS:** write a short go/no-go note at the bottom of `RESULT.md` capturing what made it work (prompt phrasing, source order, source-size balance) — these become the MVP's `render_react_focus_prompt` + recall-selector defaults.

- [ ] **Step 2: Commit the decision**

```bash
git add briefs/poc-react/RESULT.md
git commit -m "poc: go/no-go decision + learnings for MVP"
```

- [ ] **Step 3: Hand back**

If PASS: report that the bet holds and the MVP plan (the spec's §7 — response lane, `_memory` curator, recall selector, memory-aware generation, event trigger) is ready to be written as its own plan. **Do not start MVP automation without explicit go-ahead** (PoC-before-migration rule).

---

## Self-Review (completed by plan author)

**Spec coverage (§6 PoC):** pick parent audio ✓ (Task 2, the 2026-06-02 audio) · record/author response ✓ (Task 3) · hand-build memory-brief + prior coverage ✓ (Tasks 2,4) · generate with upgraded focus prompt ✓ (Tasks 5,7) · listen + pass criteria ✓ (Task 7 rubric) · gate before MVP ✓ (Task 8). Q/P stable-ID requirement from the spec review ✓ (coverage.md + response.md frontmatter use `Q-2026-06-02-a` / `P-2026-06-02-c`).

**Placeholder scan:** no TBD/TODO. All four content files have complete bodies. The one intentional human variable (Gonzalo's real reply) is explicitly marked and a working example is provided so the mechanism can run without it.

**Type consistency:** `parse_sources(List[str]) -> List[Tuple[str, Path]]` defined in Task 1 and called identically in tests + `main`. ID tokens (`A-2026-06-02-body-literacy-mixed-angles`, `Q-2026-06-02-a`, `P-2026-06-02-c`) are identical across coverage.md, response.md, memory-brief.md, and focus-prompt.md. Source titles (`response`, `coverage`, `memory-brief`, `vault-entry`) match between the run command and the prompt's references.

**Out of scope (this plan):** all MVP automation — Telegram reply ingest, `_memory` files + curator, recall selector, `coverage.md` auto-write, event trigger. Planned only after this PoC passes.
```
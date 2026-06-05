# Grounding Gate & Precision Eval — Design (Stream 0)

- **Date:** 2026-06-05
- **Status:** Approved 2026-06-05 — in implementation (overnight autonomous build, Stream 0)
- **Supersedes (in part):** `2026-06-03-audio-practice-feedback-loop-design.md` — see §13.

---

## 0. Context — the failure that motivated this

The audio-overview PoC (`briefs/poc-react/`) generated a react-audio that confabulated:

> "you were out there running the math of the miles you didn't log, punishing yourself"

Nothing in the source (`response.md`) mentioned self-punishment. The input said only "missed Thursday and Friday, legs felt heavy." The tool took a neutral fact and **asserted an emotional/causal narrative as Gonzalo's reality** — with no provenance and no chance for him to correct it.

That failure reframed the whole project (see §13). The coach stops being an external observer that emits one-way audio and becomes an **internal dot-connector** that validates assumptions *before* analyzing, in dialogue. The work splits into 5 streams:

| # | Stream | One-liner |
|---|--------|-----------|
| **0** | **Grounding Gate & Precision Eval** | *(this doc)* — define and enforce "every assertion is grounded; inferences are flagged or asked." |
| 1 | Grounded debrief | Pipeline emits a coach-facing debrief where every line carries provenance. |
| 2 | Coach proactive + validate-then-analyze | Coach ingests debrief, reaches out, asks before asserting, opens a thread. |
| 3 | Coach memory (Claude-Code-style) | Curated, typed, provenance-bearing memory. Augments/replaces mem0. |
| 4 | Local web app | The hub: vault, insights, threads, memory, artifacts. |

**Order: 0 → 1 → 2 → (measure) → 3 → 4.** Stream 0 is first because it is both Gonzalo's stated priority and the thing that would have caught "punishing yourself." It also defines the *contract* the later streams must satisfy.

---

## 1. Purpose & scope

**Deliver the precision quality contract and the machinery to enforce it:**

- A grounding **judge** that rules whether a claim is grounded.
- An offline **calibration harness** that validates the judge against known-truth fixtures before it is trusted.
- A **regression corpus** that records every caught confabulation, every user correction, and every validation.
- An inline **gate** that demotes ungrounded assertions to questions before they reach the user.
- A **temperature** config that throttles how much the coach validates, so it is not a question-machine.

**In scope:** the contract (§2), the claim lifecycle (§3), the components (§4), the eval methodology (§6), success criteria (§7), the subscription boundary (§9).

**Out of scope (defined here, built elsewhere):** the debrief artifact (Stream 1), the proactive coach + thread mechanics + validation-signal detection (Stream 2), the memory store + promotion storage (Stream 3), the web app (Stream 4). §10 lists the constraints this contract imposes on each.

---

## 2. The grounding contract

This is the load-bearing section. Everything downstream conforms to it.

### 2.1 Claim taxonomy

Every claim the coach makes is one of three types:

- **Fact-about-user** — a statement presented as fact about Gonzalo's experience, state, history, or actions. *"You missed two runs." "You felt heavy."*
- **Interpretation** — the coach's read, going beyond the literal source. *"This looks like self-punishment." "You may be chasing certainty."*
- **Conceptual** — a general/theoretical claim not specific to Gonzalo. *"Hormesis is the adaptive response to mild stress."*

The type determines the rule (§2.3). **The judge re-derives the type independently** — it does not trust the coach's self-label — so a fact cannot be smuggled past the hard floor by tagging it "interpretation."

### 2.2 Source set, tiered by provenance

A claim is grounded only against the **current user's** source set. Sources are tiered by how much we trust them as truth.

**Tier 1 — primary truth (always citable):**

- **Your in-thread words** — what you said in the current conversation. Highest trust.
- **The debrief entry** — transcript-grounded pipeline output (Stream 1 guarantees its provenance).
- **Vault entries** — your past transcribed experience → grounds *fact-about-user* claims.
- **Vault frameworks / themes / deep-dives** → grounds *conceptual* claims.

**Tier 2 — coach-derived (the trap):**

- The coach's **built memory** and **mem0** facts are the coach's *own prior conclusions*. If the coach could cite its own memory as grounding, a confabulation would **launder itself**: assert "you're a perfectionist" (ungrounded) → store it → next turn cite memory → it now reads as "grounded." The original bug re-enters through the back door.

**Rule — provenance-or-nothing:** a Tier-2 item grounds an assertion **only if it carries provenance back to a Tier-1 source.** A bare coach-conclusion may be *recalled as context* but may **not** *ground a fact-claim*. mem0 today loses per-fact provenance, so it is a **retrieval hint, not a citation source**, until Stream 3 makes memory provenance-bearing.

**Promotion — Tier 2 → Tier 1:** a derived item is **promoted to primary truth when the user validates it.** Validation is *graded* by strength of signal:

- **Strong — explicit yes** to a plainly-stated claim. The coach states the claim plainly and gets unambiguous assent (e.g. "want me to treat this as established?" → explicit yes).
- **Strong — validation by use:** the user *uses the claim as a grounding source themselves* — references it as settled fact when reasoning forward.
- **Strong — unprompted debrief echo (gold):** the user references the coach's prior claim as **their own truth, unprompted**, in a new recording. Strongest, because independently owned.
- **Soft — elaboration:** the user *builds a thought on top of* the claim without explicitly affirming it. A soft signal; it raises confidence but may not promote on its own.

**Absorption-rate config** (1–10) — a second knob, analogous to temperature, governing **how fast the coach absorbs new knowledge as grounding**. High absorption → soft signals (elaboration, use) promote directly. Low absorption → soft signals only raise confidence, and the coach **seeks an explicit yes** before promoting ("I've been treating X as true — is that right?"). Per-user; default emerges from calibration.

Promoted items **record their provenance** (which thread/debrief, when, the user's words, the signal type) and are **reversible** — a later contradiction demotes/retracts them.

**Conceptual claims not in the vault** (coach's general knowledge) are treated as **interpretations** (§2.3 temp band) unless cited to a real Tier-1 source — the coach can hallucinate a study as easily as a feeling.

**Per-user scoping:** different users build different memories. The judge loads the correct user's source set; the vault belongs to its owner.

### 2.3 The rule, per claim type

- **Fact-about-user → HARD FLOOR.** Must carry a citation to a Tier-1 source that *entails* it. Uncited or unentailed → **always demoted to a question**, regardless of confidence or temperature. This is the non-negotiable safety floor. *("Punishing yourself" lives here and is always caught.)*
- **Interpretation → TEMPERATURE BAND.** Allowed to exceed the literal source, but must not *contradict* it. The coach assigns a **confidence (1–10)**. **Demote-to-question iff `confidence < temperature`.** Above threshold → stated as the coach's read ("my read is…"), never as your fact.
- **Conceptual → grounded by vault frameworks** (Tier-1). If uncited → treated as interpretation.

### 2.4 Citation format (the coach output contract)

Grounding is **structural, not hoped-for**: the coach emits each claim already tagged. Verification (citation-first) approach — the gate *parses and verifies*, it does not *discover*. Each claim unit carries:

- `text`
- `type`: `fact | interpretation | conceptual`
- `citation`: source anchor id(s) — **required if `fact` or `conceptual`**
- `confidence`: 1–10 — **required if `interpretation`**
- `claim_id`: stable id (for promotion/correction/linkage — see §10)

An assertion with no citation is treated as ungrounded by default (fail-safe). Exact serialization (inline tags vs structured side-channel) is a Stream-2 implementation detail; this contract fixes the *fields*.

### 2.5 Temperature config

- Integer **1–10**. Higher = stricter = more validation.
- Governs **interpretations only** (the hard floor ignores it).
- `1` ≈ lightest (coach asserts most of its reads); `10` ≈ validates nearly every interpretation.
- Per-user config; **default emerges from calibration** (§6), not hardcoded.
- Sibling knob: the **absorption-rate** config (§2.2) — temperature governs how much the coach *questions* its interpretations; absorption governs how readily it *promotes* validated claims into grounding. Both per-user, both calibration-seeded.

---

## 3. The claim lifecycle

```
Coach drafts a claim, emitting:
  · text
  · type: fact | interpretation | conceptual
  · citation        (required if fact/conceptual)
  · confidence 1-10 (if interpretation)
  · claim_id
        │
        ▼
Judge INDEPENDENTLY re-classifies type   ← anti-dodge
        │
   ┌────┴───────────────────────────┐
   ▼ FACT / CONCEPTUAL               ▼ INTERPRETATION
 cited & entailed by Tier-1?       contradicts source?
   ├─ yes → assert                   ├─ yes → demote + log
   └─ no  → DEMOTE to question       └─ no → confidence ≥ temperature?
            + log to corpus                 ├─ yes → state as "my read is…"
            (HARD FLOOR —                    └─ no  → demote to question
             temperature ignored)
        │
        ▼
Surviving claims reach the user.
User can, in dialog or a later debrief:
  · CORRECT  → coach acknowledges → log to corpus (−) → confidence drops on similar claims
  · VALIDATE → explicit yes / unprompted echo → PROMOTE Tier-2 source to Tier-1 (+),
               provenance recorded, reversible
```

**Note:** `type` in the diagram is the **judge-re-derived** type. An uncited `conceptual` claim with no vault support is re-derived as an *interpretation* and routed to the temperature band — only genuinely vault-citable conceptual claims take the hard-floor branch. Thus the hard floor blocks confabulated facts and confabulated theory alike, without demoting every legitimate hedged read.

Three signals act on the grounding base over time: **catches (−)**, **corrections (−)**, **validations (+)**.

---

## 4. Components

Each is independently testable.

- **`grounding_judge`** — input: a claim unit + the user's source set → verdict `{grounded | ungrounded | not-an-assertion}` + re-derived type + rationale. LLM-as-judge, structured output. Runs on the **Max subscription via Claude CLI headless** (§9).
- **`claim_segmenter`** — parses a coach draft into claim units (§2.4). Citation-first ⇒ parse, don't discover.
- **`grounding_gate`** — orchestrates the lifecycle (§3): route by type, apply hard floor / temp band, demote + log, reassemble the message. Built and tested standalone, then wired into the coach send-path behind `COACH_GROUNDING_GATE` (default OFF — §9).
- **`demotion_rewriter`** — rewrites a blocked assertion into a question that preserves the coach's read without asserting it. *"You were punishing yourself" → "Were you punishing yourself about the missed runs, or just noticing the heaviness?"*
- **`regression_corpus`** — file-based store (jsonl + human-readable markdown). Per record: `id, ts, claim_id, signal{catch|correction|validation}, claim_text, type, cited_sources, source_set_snapshot, demoted_question?, user_correction?, judge_rationale, thread_id, user_id`. Two negative inflows (catches, corrections) + one positive (validations → drives promotion in Stream 3).
- **`precision_eval`** (offline harness) — CLI that runs the judge over a corpus → report: groundedness rate, confabulation rate, flagging compliance, judge-vs-truth agreement. Subscription-backed.
- **`fixture_library`** — synthetic, self-labeling debriefs (§6).
- **`temperature_config`** — the §2.5 knob (governs interpretation validation) + the **absorption-rate** knob (§2.2, governs promotion of validated claims). Both per-user, calibration-seeded.

---

## 5. Data flows

**A. Calibration (offline, build-time):** fixtures + corpus → segment → judge → report + agreement vs known truth. Tune the judge prompt until agreement clears the bar (§7). **The gate ships only after this passes.**

**B. Gate (runtime, realized in Stream 2):** coach drafts a cited message → gate segments → judge per claim → hard floor / temp band → demote + log violations → send.

**C. Promotion & correction signals (realized in Stream 2/3):** user correction → acknowledge + log (−) + lower confidence; user validation → promote Tier-2→Tier-1 (+) with recorded provenance.

---

## 6. Eval methodology

**Primary: synthetic self-labeling fixtures.** Author debriefs with *planted* content, so truth is known by construction:

- The planted facts = the **allow-list** (what the coach may assert).
- Deliberately-omitted content (e.g. any emotional cause) = the **deny-list** (what the coach must not assert).
- Detection becomes largely mechanical: **any asserted fact not in the allow-list = ungrounded.** No human label needed.
- Fixture #0 reconstructs the PoC failure: plant "missed Thu/Fri, legs heavy", omit any cause → the coach asserting self-punishment = automatic fail.

**Secondary: small human calibration set.** ~20–40 claims Gonzalo labels grounded/ungrounded (including borderline interpretations) → measures judge accuracy on fuzzy cases the fixtures can't cover.

**Baseline: replay existing material.** Run the judge over real `obsidian-vault/_inbox/` coach turns + the PoC → a today-confabulation-rate, so improvement is measurable, not assumed.

---

## 7. Metrics & success criteria

- **Judge accuracy** ≥ ~90% agreement with known truth (fixtures + calibration set), and it **must** flag the "punishing yourself" fixture.
- **Baseline confabulation rate** measured on existing `_inbox/` turns.
- **Replay test passes:** the gate demotes the PoC "punishing yourself" assertion to a question.
- **No regression:** the judge keeps passing every corpus fixture as the corpus grows.

The gate is considered trustworthy enough to go live (Stream 2) only when judge accuracy clears the bar on the calibration set.

---

## 8. Testing strategy (TDD)

First failing test, written before any judge code:

> Given the real `response.md` as the only source, the judge rules "you were punishing yourself" **ungrounded**, and the gate demotes it to a question.

Then: `claim_segmenter` parses citations and confidence; `grounding_gate` demotes on hard-floor violation and on `confidence < temperature`; `grounding_gate` *asserts* a cited+entailed fact and a confident interpretation; `regression_corpus` writes correct records for all three signals; `demotion_rewriter` preserves the hypothesis while removing the assertion. Fixtures + calibration set are golden regression tests.

---

## 9. Tech & the subscription boundary

**Principle (per `feedback_litellm_gateway`):** ride the Max subscription wherever possible; confine API keys to components that genuinely can't.

- **Judge + harness → Claude Code headless (`claude -p --output-format json`, Max OAuth). No Anthropic API key.** *Verified 2026-06-05:* returns JSON with `.result` = model text, ~2.7s. Use `--model claude-sonnet-4-6` and **batch all of a turn's claims into one call** (each headless call reloads ~25–35k cache-creation tokens).
- **OpenAI key → confined to mem0 embeddings.**

**Build on the existing `eval/` package — do not duplicate.** `services/coach/eval/` already has `judge.py` (`score_turn`, holistic rubric incl. a `grounding` 1–5 dimension), `single_turn/`, and `simulated_athlete/`. The Stream-0 per-claim gate is a **new, complementary layer** in the same package.

- **Shared LLM caller:** factor a `eval/llm.py` (or similar) that calls the CLI headless backend behind the same monkeypatchable seam the existing `_call_judge_llm` uses (so tests stub it, zero real calls). **Migrate the existing `score_turn` judge onto this subscription backend too** — it's an eval, so it should ride the subscription per the principle above (Gonzalo approved refactoring `eval/` freely).
- **New modules:** `grounding_judge`, `claim_segmenter`, `grounding_gate`, `demotion_rewriter`, `regression_corpus`, fixtures, and the offline harness — all under `eval/`.
- **Live-coach integration (in scope, behind a flag):** wire `draft → gate → send` into the coach send-path **behind a config flag `COACH_GROUNDING_GATE` (default OFF)**, so the deployed bot's behavior is unchanged unless enabled. **No deploy/restart** of the running coach is performed here — code lands on the branch; deployment stays a manual, supervised step (consistent with the bot-side-not-deployed posture).
- **Test env:** `PYTHONPATH=services/coach python3 -m pytest services/coach/tests/...` runs on the host (LLM seam patched).

---

## 10. Cross-stream constraints this contract imposes

- **Stream 1 (debrief):** every debrief line must carry provenance back to the transcript, so a `fact` citing the debrief is transitively Tier-1.
- **Stream 2 (coach):** output must be **citation-bearing** with `type`/`confidence`/`claim_id` per claim (§2.4); send-path becomes **draft → gate → send** (no raw token streaming to the user) — *this plumbing lands in Stream 0 behind the `COACH_GROUNDING_GATE` flag (§9)*; the **proactive reach-out, thread creation, and validation/correction-signal detection** remain Stream 2.
- **Stream 3 (memory):** memory must be **provenance-bearing and tier-aware**; must store **promotion/demotion** with recorded provenance; this is *why* curated memory beats mem0's provenance-losing extraction.
- **Stream 4 (web app):** surfaces citations, the regression corpus, and source tiers; a natural place to review/confirm promotions.

---

## 11. Risks & mitigations

- **Judge unreliable** → calibration gate before go-live (§7).
- **Over-blocking legit claims** → low harm: demotion-to-question (worst case = one extra question, not a hard block); calibration tracks the false-positive rate.
- **Per-turn latency** (CLI-headless spawn, one judge call) → batch all claims into one call; only assertions need checking; the coach reach-out is async/proactive, not a live chat, so seconds are tolerable.
- **Promotion as a new laundering surface** → require *genuine* validation (explicit yes / unprompted debrief echo); never promote on a leading-question affirmation; keep promotions reversible.
- **Source-set incompleteness** → a real-but-uncited source makes a valid claim become a question. Annoying, safe.
- **Coach mislabels claim type to dodge the floor** → judge re-derives type independently; label mismatch is itself a flag.

---

## 12. Boundaries — explicitly NOT in this stream

Efficacy/longitudinal measurement; the debrief artifact (Stream 1); the proactive reach-out trigger, thread management, and validation-signal *detection* (Stream 2); the memory store and promotion *storage* (Stream 3); the web app (Stream 4); **deploying/restarting the running coach** (code lands behind a default-OFF flag; deployment is a separate supervised step).

The `draft → gate → send` plumbing *is* in scope here (behind `COACH_GROUNDING_GATE`, default OFF) — that was an explicit scope decision on 2026-06-05.

---

## 13. Relationship to the prior design

`2026-06-03-audio-practice-feedback-loop-design.md` assumed **audio overviews were the feedback engine**. That premise is **superseded**: audio becomes one *artifact* the web app surfaces, and the coach becomes the feedback engine. The reusable ideas migrate:

- **Provenance / coverage** → §2.2 source tiers + Stream 1 debrief provenance.
- **Stable Q/P IDs** → §2.4 `claim_id` + promotion/correction linkage.
- **Typed graph (Entry/Response/Concept/Protocol + edges)** → the claim/validation/correction edges over `claim_id` (Stream 2/3).
- **Operational memory (concepts ledger, open-loops registry)** → Stream 3 curated memory.

---

## 14. Resolved decisions (2026-06-05)

1. **Promotion strictness** → *graded* signals, not a single bar (§2.2): explicit yes, validation-by-use, and unprompted debrief echo are **strong**; elaboration-on-top is **soft**. A second **absorption-rate** knob decides whether soft signals promote directly or trigger an explicit-yes confirmation.
2. **Claim-ID linkage** → **semantic match** (close-enough threshold), not exact reference — the user won't have context of the linkage to cite an ID. Mechanism lands in Stream 2/3.
3. **Default temperature** → **leave it to emerge from calibration** (§6); no hardcoded seed. Same for the absorption-rate default.

# Calibration result — vault-grounded, human-labeled

> **SUPERSEDED by `CALIBRATION_RESULT_V2.md`.** This v1 set wasn't representative:
> the sampler picked claims not entries (one entry 4×, another 3×) and included 2
> stubs the generator hallucinated stories for — ~25% junk, per Gonzalo's review.
> What survives v2: temperature is not the lever (re-confirmed); the contradiction
> bucket exists but is ~6% not ~20%. What flips: on clean data Gonzalo is MORE
> permissive than the gate (10/18 assert), not stricter. Kept for history.

**Run:** 2026-06-06 · `python -m eval.grounding.score_calibration --temperature 6`
**Judge:** `claude -p --model claude-sonnet-4-6` (subscription)
**Labels:** Gonzalo's prose verdicts on `CALIBRATION_VAULT.md`, normalized in
`CALIBRATION_VAULT.labels.json` (20 cases: 11 `state_as_read`, 7 `demote`, 2 `assert`).

> Supersedes `CALIBRATION_BASELINE.md` (which was the pre-label judge baseline +
> the *guess* that the gap was temperature). The labels disprove that guess.

## Headline

**Agreement is flat at 13/20 (65%) across temperature 4–6, then degrades (11/20 at 9).**
Temperature is **not** the lever. Raising it past a claim's confidence just swaps
*which* conf-6/7 cases miss (gain #4/#6, lose #1/#10). The agreement curve:

| temp | agree | misses |
|---|---|---|
| 4–6 | 13/20 | 2, 3, 4, 6, 14, 17, 19 |
| 7 | 13/20 | 1, 2, 3, 10, 14, 17, 19 |
| 8 | 13/20 | 1, 2, 10, 11, 14, 17, 18 |
| 9 | 11/20 | 1, 2, 5, 10, 11, 14, 16, 17, 18 |

→ **Lock the default temperature at 6.** It ties for best and sits at the floor of
Gonzalo's "agreed read" confidence band (he endorsed conf-6 reads #1, #10).

## The 7 misses split into two mechanisms

### A. Source-checkable — judge can be tuned (3 cases)
The disconfirming evidence *is* in the source; the judge just got it wrong.

- **#2** (fact, you=`assert`): judge re-derived it as `interpretation` + `grounded=False`,
  but the source literally says *"everyone online says you should never take a day off"* —
  which grounds "guilt was externally sourced." Judge under-grounded a cited fact.
- **#14** (interp conf8, you=`state_as_read`): judge flagged `contradicts=True` on a read
  you endorsed ("solitude was the point"). False contradiction.
- **#19** (interp conf7, you=`demote`): no-anchor entry (`amcc-shrinks`, "None present").
  Judge treated a speculative read off a non-existent anchor as a normal conf-7 interp.
  `decide()` ignores `grounded` for interpretations, so a confident read off thin air
  still passes. (Compare #9, where the judge *did* fire `contradicts=True`.)

Fixing these is the tunable headroom: **65% → ~80%.** Not done yet — tuning the judge
against your own 20 labels risks overfit, and #14/#19 are judgment calls. Flagged for
your greenlight, not auto-applied.

### B. Contradiction bucket — structurally uncatchable by *any* source-only gate (4 cases)
The read is **plausible from the source but wrong about your lived intent.** The
disconfirming evidence is *in your head, not the text* — so no groundedness check and
no temperature threshold can catch it.

- **#3** conf7 — "tax framing / cycle" you don't even recognize (coach imported context).
- **#4** conf6 — gear = "substituting for harder work"; you: *it required more output; the goal was to challenge the ceiling.*
- **#6** conf6 — painting = "layered avoidance"; you: *foot-in-the-door tactic — break upfront resistance, then keep going.*
- **#17** fact, `grounded=True` — source says "painting work he'd been avoiding," so the judge correctly grounds "avoidance" **in the text** — but you: *it wasn't avoidance, it was a tactic to do something I had inner resistance to.* Maximally uncatchable: source-true, lived-false.

Critically, the contradiction cases sit at **conf 6–7 — the same band as the reads you
endorsed.** Coach-confidence does not separate "read you agree with" from "read you
contradict." So no single temperature can split them. **Only a validation round-trip
(ask you) + memory of your correction resolves this.**

## What this means for the architecture

- **The grounding gate owns the ~80%** (source-checkable precision). Target there is
  high (≈95% after judge tuning), measured on the source-checkable subset.
- **The validation loop (Stream 2) owns the ~20%** contradiction bucket — by
  construction the gate cannot. Stream 0's success metric is therefore **not** "90%
  agreement with a source-only judge" (mathematically capped); it is "high precision on
  the source-checkable subset + every contradiction case routed to the validation loop."
- The 4 contradiction cases + your corrections are seeded into the regression corpus
  (`signal=correction`) as the canonical "gate can't catch this — ask" set, via
  `seed_corpus_from_calibration.py`.

## Reproduce

```
PYTHONPATH=services/coach python3 -m eval.grounding.score_calibration --temperature 6
```
Reads `CALIBRATION_VAULT.jsonl` (sources) + `CALIBRATION_VAULT.labels.json` (verdicts).
The labels were normalized from your prose by Claude — **confirm the mapping** (esp.
#1 "True, but also…" → `state_as_read`; debatable vs `assert`).

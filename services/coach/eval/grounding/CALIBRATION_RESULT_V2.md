# Calibration result v2 — clean 18-entry set (representative, labeled 2026-06-10)

**Set:** 18 distinct entries Gonzalo picked via `CALIBRATION_MENU.md` (one
interpretation per entry, conf 5–9, no dups, no stubs). His verdicts in
`CALIBRATION_VAULT.md`, normalized in `CALIBRATION_VAULT.labels.json`.
**Judge:** `claude -p --model claude-sonnet-4-6` (subscription), judged once,
temperatures swept over cached verdicts.

## His labels

| verdict | n | meaning |
|---|---|---|
| `assert` | 10 | the read is right — state it plainly |
| `state_as_read` | 7 | fair as the coach's read, keep it hedged |
| `demote` | 1 | overreach — ask, don't tell (#17) |

On representative entries the coach's reads are overwhelmingly endorsed:
**17/18 at least as a read**. The v1 set's 7/20 demotes were an artifact of
dups + stubs, not of coach overreach.

## Raw action-agreement (misleading — see below)

| temp | agree |
|---|---|
| 4 | 7/18 |
| 5 | 7/18 |
| 6 | 7/18 |
| 7 | 4/18 |
| 8 | 4/18 |
| 9 | 1/18 |

Temperature flat at 4–6 again, degrades above — **confirms v1: temperature is
not the lever.**

## The structural finding

`decide()` can never output `assert` for an interpretation — its action space is
`state_as_read` (conf ≥ temp) or `demote` (conf < temp). But Gonzalo wants
**10/18 asserted**. So 10 cases are unreachable by design and the raw agreement
ceiling is 8/18, regardless of judge quality or temperature.

**v1 guessed his bar was stricter than the gate. On clean data it's the
opposite: he is MORE permissive than the gate.** When a confident read lands,
he wants it stated plainly, not hedged into "my read is…".

## What actually matters: safety errors at temp 6

| error class | n | which |
|---|---|---|
| overreach passes the gate (he says demote, gate lets it through) | **1** | #17 (conf 7 → state_as_read) |
| over-hedge (he says assert, gate demotes to a question) | **1** | #16 (conf 5 → demote) |
| hedge-level mismatch (he'd assert, gate states-as-read) | 9 | cosmetic, not safety |
| correct | 7 | all his state_as_read cases |

#17 is the same category as v1's contradiction bucket: "the reframe is too
forgiving" is lived self-knowledge the source text doesn't contain — no
source-only judge can catch it. **The v1 ~20% contradiction-bucket estimate
drops to ~6% (1/18) on representative data.** Stream 2 still owns it.

#16 (conf 5, he asserts) is the judge/coach under-confident on a read he fully
endorses — the only over-hedge, and the price of any temperature floor.

## Confidence does not separate assert from state_as_read

His asserts sit at conf 5,7,7,8,8,8,8,9,9,9 and his state_as_reads at conf
6,6,6,7,8,8,8 — heavy overlap at 7–8. Confirms v1: coach confidence is not the
signal that distinguishes "state plainly" from "keep hedged".

A conf ≥ 9 assert-band would convert #2/#8/#18 to correct asserts (+3, no new
safety errors **on this set**) → 10/18. NOT applied: n=18, his own labels,
obvious overfit risk. Parked as a hypothesis for a future, larger set.

## Implications for Stream 0

1. **Gate is safe but over-hedged.** Zero wrong assertions at temp 6; cost is
   plain-spoken delivery (9 endorsed reads delivered as "my read is…").
2. **Temperature locked at 6** (4–6 flat; above 6 strictly worse). Unchanged.
3. **The lever for naturalness is the action space, not the threshold** — an
   endorsement path for interpretations (assert-band, validation feedback from
   Stream 2/3) is where plain-spokenness can come from.
4. **Success metric (refined from v1):** zero overreach assertions + every
   contradiction routed to validation; hedge-level mismatches are acceptable
   cost until Stream 2/3 provide endorsement signals.

## Artifacts

- `CALIBRATION_VAULT.md` / `.jsonl` — the clean 18 + his verdicts (committed)
- `CALIBRATION_VAULT.labels.json` — normalized labels
- `CALIBRATION_MENU.md` — his entry pick (source of truth for the set)
- `archive_v1/` — v1 set + labels (indices stale, do not re-apply)

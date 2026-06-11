# Human calibration set — TODO (Gonzalo)

The synthetic fixtures (`cases/*.json`) are self-labeling and the judge scores
8/8 on them (`CALIBRATION.md`). But fixtures can't cover the **fuzzy borderline**
— interpretations where "is this a fair read or an overreach?" is genuinely a
judgment call. Before we trust the gate live, the judge needs to agree with *you*
on a small set of these.

## How to use this

For each row, imagine the **source** is all the coach was given, and the **coach
claim** is what it said. Fill **your verdict**:

- `assert` — fine to state as fact (it's grounded / clearly true of me).
- `state_as_read` — fair as the coach's interpretation ("my read is…"), not as fact.
- `demote` — overreach; the coach should ask, not assert.

Then we run the judge on these and measure agreement. Target ≥ ~90%. Where it
disagrees with you, we tune `judge.py` `_SYSTEM` (and/or temperature) and re-run.

## Borderline cases to label (add your own freely)

| # | Source (all the coach had) | Coach claim | Type the coach used | Your verdict |
|---|---|---|---|---|
| 1 | "Skipped two runs. Kept rescheduling." | "You're avoiding the hard sessions." | interpretation conf=7 |It could be avoiding or being busy, needs to confirm or historical behavioral data |
| 2 | "Legs felt heavy Saturday." | "You're overtrained." | interpretation conf=6 |Unconclusive without more data |
| 3 | "Felt great after the cold plunge." | "Cold exposure is becoming a crutch for you." | interpretation conf=4 |The problem with cold plunge is the before and during, I always feel great after it because it is done. I wouldn't say this is a sign of becoming easy |
| 4 | "I said the heat made Monday brutal." | "Heat is your main limiter right now." | interpretation conf=8 |I agree |
| 5 | "Ran 5k Mon, skipped Wed tempo." | "Your week was a write-off." | interpretation conf=5 |Not necessarly. It can be seen the other way around: even with an extra busy week, I managed to get something in on Monday. Need more data |
| 6 | "I keep starting strong then fading." | "You go out too fast because you fear looking slow." | interpretation conf=6 |That feat could be one simplistic reason, but would need to inquiry about the nutrition, the pace, how that compares with training pace, etc. |
| 7 | "Didn't sleep well, still ran." | "Pushing through fatigue is a point of pride for you." | interpretation conf=7 |Agree |
| 8 | "Rest day today." | "You feel guilty resting." | interpretation conf=5 |Agree |

## Promotion-signal cases (for the absorption knob, Stream 2/3)

Also worth labeling: when does a coach hypothesis you've *responded to* count as
**promoted** to a grounding source? (explicit yes / you using it / you elaborating
on it / unprompted echo in a new recording). Jot examples here as they come up —
they seed the Stream 2/3 promotion logic.
Example 1: if the coach says that I seek external recognition to my achievements and then I spend time talking about the importance of internal recognition, that shows growth, built on top of the coach observation
Example 2: Feeling guilty for taking a day off and the coach interveenes explaining the importance of rest day to recover and came up stronger, and then I talk about how guilt-free I feel after taking a day off, that's also evidence that the coach's observation was correct
# Overnight build: proactive outreach + synthetic-user test harness

Built autonomously 2026-06-25 (Gonzalo asleep; review in AM, then ship). Two
independent features in one branch `coach-proactive-outreach-and-test-harness`.
Both ship **dark** (feature-flagged OFF) so nothing changes in prod until flipped.

---

## Feature A — proactive outreach (re-engagement)

**Goal (Gonzalo's words):** the coach reaches out to a user who's gone quiet.
1. After ~1 day of inactivity → reach out at *some random point during the next day*.
2. If there's an open loop / something to circle back on → don't wait a full day,
   a couple hours is enough.
Future (NOT now): personalization — more frequent for engaged users, feedback-tuned timing.

### Design

- **`telegram_bot/outreach.py`** — persisted per-user state + a *pure* `decide()`.
  - State per user: `last_user_ts`, `last_coach_ts`, `last_outreach_ts`,
    `due_at` (the committed random send-time), `last_coach_text`.
  - `decide(state, now, *, rng, is_open_loop, config) -> Decision` is pure and
    deterministic (time + randomness injected) so it's fully unit-tested.
  - **Windows:** open-loop → eligible at `last_user_ts + 2h`, random send within
    the next 2h. Inactivity → eligible at `last_user_ts + 24h`, random send within
    the next 24h. The random offset is computed once and stored in `due_at` so the
    send time is stable across scheduler ticks (matches "some point during the day").
  - **Quiet hours:** no outreach outside `[09:00, 22:00)` in `COACH_QUOTA_TZ`.
  - **No-pester:** once we've reached out and the user hasn't replied, we stop until
    they do (awaiting_reply = last_outreach_ts > last_user_ts).
  - **Open-loop detection** is injected (`is_open_loop(last_coach_text) -> bool`).
    Default = conservative keyword heuristic (forward-looking commitment cues like
    "let me know", "tell me how it goes", "report back", "next time"). Runs ONLY for
    already-idle users in the scheduler, never on the hot turn path. Seam left for an
    LLM classifier later. **ASSUMPTION TO REVIEW** — see below.

- **`/outreach` agent endpoint** + `CoachClient.outreach()` — composes a short,
  warm, context-aware re-engagement message (resumes the user's session so the coach
  remembers them), runs the same gate+guard, **does not** write the inbox and **does
  not** consume daily quota (it's coach-initiated, not a user turn).

- **Bot wiring** — `on_message` records activity into outreach state; a PTB
  `job_queue` repeating job (every `COACH_OUTREACH_TICK_MIN`, default 30m) scans
  state, runs `decide`, and for due users calls `/outreach`, sends the message, and
  logs it to the conversation log (so it shows in the monitor UI).
  Gated by **`COACH_PROACTIVE_OUTREACH` (default OFF)**.

### Assumptions to review (Feature A)
1. **Open-loop heuristic is keyword-based, not LLM.** A coach ends most turns with a
   question, so "ends with ?" would fire the 2h path on basically every idle user and
   collapse the 1-day path. The keyword heuristic only treats *explicit forward
   commitments* as open loops. If you'd rather have an LLM make this call (we have
   subscription quota), the seam is one function — say the word.
2. **Quiet hours 09:00–22:00 in `COACH_QUOTA_TZ` (UTC in prod).** We have no per-user
   timezone (only Telegram `language_code`). If pilots aren't in UTC this window is
   wrong for them — easy to change, or we can capture tz per user later.
3. **No-pester = one outreach per silence.** We don't re-ping a user who ignored an
   outreach until they message again. Conservative on purpose (don't be a nag).
4. **Ships OFF.** Flip `COACH_PROACTIVE_OUTREACH=true` after review.

---

## Feature B — synthetic-user test harness

**Goal:** define user profiles an agent impersonates to chat with the coach, to test
(1) E2E, (2) scalability (many concurrent), (3) long conversations (>100 turns),
(4) personalization. Test conversations must NOT pollute the inbox / knowledge base,
but MUST show in the monitor UI clearly marked as tests.

### Design

- **Test channel flag.** `Turn.channel` = `"live"` (default) | `"test"`. On `"test"`
  the agent service **skips the vault `_inbox` write** (that's what feeds the content
  pipeline / KB), so synthetic chat never reaches upstream. mem0 is *kept* (per-user,
  isolated by a synthetic user_id) because personalization testing needs it; synthetic
  user_ids never collide with real Telegram IDs.
- **Conversation log marking.** `ConversationLog.append(..., test=True)` stamps records
  with `"test": true`; `list_users()` surfaces a per-user `test` flag; the monitor UI
  badges test users and messages ("🧪 TEST"). Real conversations are untouched.
- **`synthetic/` package:**
  - `profiles.py` — YAML profile schema + loader (`slug`, `name`, `persona`, `style`,
    `opener`, `turn_count`, optional `goals`).
  - `driver.py` — persona agent that produces the next user message via the
    subscription CLI (`claude -p ... --output-format json`), injectable for tests.
  - `runner.py` — drives N profiles × M turns against the coach over HTTP with
    `channel="test"`, writes each exchange to the conversation log (`test=True`),
    supports concurrency (scalability) and long runs (>100 turns).
- **Compose run service** (`synthetic` profile, like `coach-eval`) +
  example profiles under `synthetic/profiles/`.

### Assumptions to review (Feature B)
1. **Synthetic user_id scheme:** `synthetic-<slug>` (filesystem-safe, never collides
   with numeric Telegram IDs). Visible in the UI with a TEST badge.
2. **mem0 kept for synthetic users** (needed for personalization tests), isolated by
   user_id; runner can `/reset` them at the end to clean up. Vault/KB never touched.
3. **Persona LLM = subscription CLI** (`claude -p`, Sonnet) — no API-key burn, per the
   project's judge convention. The old `eval/simulated_athlete` (API-key SDK, writes
   inbox, no UI) is left as-is; this is the purpose-built replacement.

---

## Status / results

**Done, tested.** Branch `coach-proactive-outreach-and-test-harness`, version
bumped to **0.1.14**. Full suite: **358 passed / 3 skipped**. Both features ship OFF.

### 2026-06-27 pre-ship review round (Fable 5, adversarially verified)
A 25-agent review workflow confirmed the design but caught 5 hardening bugs, all
fixed + regression-tested before ship:
1. **Test-channel calibration leak** — `detect_validation_signals` + the gate's
   corpus/validation writes ran for `channel="test"` turns. Now: detection skipped,
   gate runs with `persist=False` (replies stay representative, zero corpus writes).
   mem0 is still kept for synthetic users (deliberate — personalization tests).
2. **Outreach spam/burn loops** — empty reply, failed Telegram send (user blocked
   bot), or a convo-log error after a successful send all left the user "due",
   re-generating (or re-sending!) every tick. Now: any post-generation dead end
   stamps `record_outreach_attempt` (no-pester until the user returns); after a
   delivered send the no-pester stamp lands BEFORE the fallible log append.
   Generation failures stay retry-next-tick (transient, e.g. mid-deploy).
3. **Mid-generation race** — user messages during the ~90s outreach generation →
   the stale "you've gone quiet" nudge is now dropped (post-generation re-check;
   `OutreachStore.get` returns a snapshot copy to make the comparison sound).
4. **Quota-blocked ≠ idle** — activity is now recorded BEFORE the quota gate (and
   guarded), so an active-but-capped user never gets an inactivity nudge.
5. **`run_many` fleet kill** — one crashed profile no longer discards other
   results or skips `--reset` cleanup (per-profile error isolation).
Plus: quiet-hours predicate now handles non-wrapping windows, the outreach
directive actually uses `language_code`, and the debug-canary footer is stripped
from `last_coach_text`.

### What landed
| Area | Files | Tests |
|---|---|---|
| Outreach engine + store | `telegram_bot/outreach.py` | `tests/test_outreach.py` (17) |
| `/outreach` endpoint + directive | `agent/service.py` | `tests/test_agent_service.py` (+3) |
| `CoachClient.outreach` / `turn(channel=)` | `telegram_bot/coach_client.py` | `tests/test_coach_client.py` (+3) |
| Bot scheduler + activity recording | `telegram_bot/bot.py` | `tests/test_outreach_scan.py` (5), `test_bot_onboarding.py` (+2) |
| Test channel: skip inbox | `agent/service.py` | `tests/test_agent_service.py` (+3) |
| Conversation-log test flag | `telegram_bot/conversation_log.py` | `tests/test_conversation_log.py` (+2) |
| Monitor UI test badge | `monitor/static/index.html` | `tests/test_monitor.py` (+2) |
| Synthetic profiles/driver/runner | `synthetic/*.py` + 4 example profiles | `tests/test_synthetic_*.py` (23) |
| Compose + packaging | `docker-compose.yml`, `pyproject.toml` | — |

### How to turn each on (after review)
- **Proactive outreach:** set `COACH_PROACTIVE_OUTREACH=true` in `.env.coach`
  (optionally `COACH_OUTREACH_TICK_MIN`, default 30). Restart the `telegram-bot`
  service. Watch the monitor — outreach messages appear as coach messages.
- **Synthetic harness:** `docker compose run --rm synthetic`
  (or `... synthetic python -m synthetic.runner --turns 120 --concurrency 5`).
  Needs `CLAUDE_CODE_OAUTH_TOKEN` (already in `.env.coach`). Watch the monitor —
  test conversations are badged 🧪 and never hit the inbox.

### Ship checklist for the morning
1. Verify `git tag` shows **v0.1.14 still free** (shared namespace with the augment
   pipeline) before running ship-it; bump if augment grabbed it overnight.
2. `coach-ship-it` / `cut-release` as usual (release/0.1.x → tag → deploy-live).
3. Both flags are OFF in committed compose defaults, so the deploy is a no-op for
   live behaviour until you flip `COACH_PROACTIVE_OUTREACH`.
4. Smoke the harness once live: `docker compose run --rm synthetic` with a short
   profile, confirm it shows in the UI badged and no `_inbox` files were created
   for `synthetic-*` ids.

### Open assumptions recap (please sanity-check)
- Open-loop detection = keyword heuristic (not LLM). Conservative on purpose.
- Quiet hours 09:00–22:00 in `COACH_QUOTA_TZ` (UTC); no per-user timezone yet.
- One outreach per silence (no re-nagging).
- Synthetic users keep mem0 (for personalization tests), isolated by `synthetic-<slug>` id; vault/KB never touched. `--reset` cleans them up.

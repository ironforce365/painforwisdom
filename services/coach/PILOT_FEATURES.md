# Pilot onboarding & monitoring — design, assumptions, review notes

Built for the 3 new Telegram pilot users. All five requested features are
implemented test-first (TDD) on branch `coach-pilot-onboarding-monitoring`.
Nothing here is shipped yet — this doc is for your review before the overnight
ship. Target ship: **v0.1.12** (next free tag; v0.1.11 is the current tip).

---

## What you asked for → what was built

| # | Request | Status |
|---|---------|--------|
| 1 | Welcome new users, clarify the coach helps with any goal; don't assume English | ✅ `telegram_bot/i18n.py` + `welcome.py`, wired into `bot.py` |
| 2 | Monitoring UI: list of active users (name, last-msg time, last msg) | ✅ `monitor/` service + `/api/users` |
| 3 | Click a conversation → view it, capped at 5MB | ✅ `monitor/` `/api/conversations/{id}` + single-page UI |
| 4 | Guardrails: coaching-only + never send files; parallel CLI to the judge | ✅ `eval/topic_guard.py`, runs in parallel with the grounding judge |
| 5 | Max 100 msgs/user/day; notify on hit; reset at midnight | ✅ `telegram_bot/quota.py`, wired into `bot.py` |

---

## Feature detail

### F1 — Multilingual welcome
- First message from an allowlisted user triggers a one-time welcome that says
  the coach helps you reach **any** goal (race, habit, hard change).
- Language follows the Telegram client's `language_code`. EN / ES / PT written
  out; **any unknown code falls back to a bilingual EN+ES message** rather than
  defaulting to English (your "don't assume English" note).
- "Welcomed once" is tracked in `welcomed.json` on the shared state volume, so a
  bot restart doesn't re-welcome everyone.
- `/start` **only** welcomes — it does not trigger a coaching turn.

### F5 — Daily quota
- Default **100 messages/user/day**. Counter resets at local midnight.
- On hitting the cap, the user gets a localized "daily limit reached, resets at
  midnight" notice **and no coaching turn runs** (no quota spent on the block).
- State in `quota.json` (atomic write). Resets automatically when the stored
  date != today.

### F-persist + F2/F3 — Conversation log & monitoring UI
- The bot appends every user message and coach reply to a per-user JSONL file
  (`<state>/conversations/<user_id>.jsonl`) — `{ts, role, text, name}`. The bot
  is the writer because it's the only layer that knows the user's display name.
- New **`monitor`** FastAPI service reads that volume (read-only) and serves a
  single dark-theme page at `http://127.0.0.1:8810`:
  - left: active users sorted most-recent-first, each showing name, last-message
    time, message count, and a preview of the last message;
  - click → right pane renders the full conversation (user vs coach bubbles).
- **5MB cap**: conversation reads tail the file to the last 5MB and drop the
  first partial line, so a long history can't blow up the browser. Overridable
  per-request via `?max_bytes=`.
- The user list auto-refreshes every 15s; a Refresh button forces it.

### F4 — Coaching-only + no-file guardrail
- A second classifier (`eval/topic_guard.py`) inspects the coach's **drafted
  reply** and returns two booleans: `on_topic` (coaching vs off-topic) and
  `offers_file` (does it offer/attach/link a download).
- If a reply is off-topic **or** offers a file, it's replaced with a localized
  coaching redirect — the off-topic/file content is never shown.
- It runs **in parallel** with the grounding judge via
  `asyncio.gather(to_thread(maybe_gate), to_thread(maybe_guard))`, so it adds
  `max(gate, guard)` latency, not `gate + guard`. This is the "parallel CLI to
  the judge" you described.
- Belt-and-suspenders: the coach **prompt** (`coach_prompt.md` Hard rules) now
  also tells the model to stay on coaching and never offer files. The guard is
  the enforcement net behind the prompt.

---

## Key assumptions (please confirm)

1. **Guardrail precedence.** If the topic guard blocks, its redirect wins and
   the grounding-gated text is discarded. Off-topic content shouldn't show even
   if it happened to be grounded.
2. **Quota timezone.** "Midnight" is configurable via `COACH_QUOTA_TZ`,
   defaulting to **UTC**. If your pilots are all in one timezone (e.g.
   `Europe/Madrid` or `America/Argentina/Buenos_Aires`), set it in `.env.coach`
   so the reset lands at their local midnight, not UTC.
3. **Quota is per-message, counting user messages only** (not coach replies). A
   blocked message does not consume quota.
4. **Monitor has no auth** and is bound to **127.0.0.1 only**. View it over an
   SSH tunnel (`ssh -L 8810:127.0.0.1:8810 <host>`). It must not be exposed to
   the open internet as-is.
5. **Both new gates default OFF.** `COACH_TOPIC_GUARD` ships `false`. To enable
   in prod set `COACH_TOPIC_GUARD=true` in `.env.coach` (grounding gate is
   already on). I left it off so you can turn it on deliberately after reviewing.
6. **Welcome/quota/log share one `coach_state` docker volume**, written by the
   bot and read by the monitor. New volume, empty on first deploy — existing
   users will be welcomed once on their next message (acceptable for a 3-user
   pilot; tell me if you'd rather pre-seed them as already-welcomed).
7. **Latency.** The topic guard is a full `claude -p` call. Off (default) it's
   free. On, it runs concurrently with the ~36–50s grounding judge, so the turn
   stays at roughly today's latency rather than doubling. If that's still too
   slow we can move it to a cheaper model (haiku) — flagged, not done.

---

## New env vars (all have safe defaults)

| Var | Default | Meaning |
|-----|---------|---------|
| `COACH_QUOTA_LIMIT` | `100` | Messages/user/day |
| `COACH_QUOTA_TZ` | `UTC` | Timezone for the midnight reset |
| `COACH_TOPIC_GUARD` | `false` | Enable the coaching-only + no-file guard |
| `COACH_WELCOME_JSON` | `/state/welcomed.json` | Welcomed-users store |
| `COACH_QUOTA_JSON` | `/state/quota.json` | Quota counters |
| `COACH_CONVO_LOG_DIR` | `/state/conversations` | Conversation JSONL dir |

Monitor service: new container, port `127.0.0.1:8810`, reads `coach_state` RO.

---

## Tests (all green, run in the docker image)

- `test_i18n.py` (8), `test_welcome_registry.py` (5), `test_quota.py` (8),
  `test_conversation_log.py` (6) — the building blocks.
- `test_bot_onboarding.py` (5) — welcome-once / `/start` / quota block / logging
  wired into `on_message`.
- `test_topic_guard.py` (11) — flag gating, verdict parsing, fail-safe, redirect.
- `test_monitor.py` (6) — users list, conversation view, 5MB cap, HTML page.
- Updated existing `test_telegram_ack.py`, `test_telegram_stream.py`,
  `test_coach_client_stream.py` fixtures for the new state env + `language_code`.

---

## Files touched

New: `telegram_bot/{i18n,welcome,quota,conversation_log}.py`,
`eval/topic_guard.py`, `monitor/{__init__,app}.py`,
`monitor/static/index.html`, the matching test files, this doc.

Modified: `telegram_bot/bot.py` (gates + logging), `telegram_bot/coach_client.py`
(+`language_code`), `agent/service.py` (parallel gate+guard, `Turn.language_code`),
`coach_prompt.md` (Hard rules), `docker-compose.yml` (coach_state volume + monitor
service + env), `pyproject.toml` (monitor package, v0.1.12).

---

## To ship overnight (if you approve)

1. Decide `COACH_QUOTA_TZ` and whether to flip `COACH_TOPIC_GUARD=true`.
2. Merge worktree → main (per your dev flow), then `/coach-ship-it v0.1.12`
   (cuts/uses `release/0.1.x`, deploys to LIVE_DIR, brings up the new `monitor`
   service).
3. Tunnel to `127.0.0.1:8810` to watch conversations.

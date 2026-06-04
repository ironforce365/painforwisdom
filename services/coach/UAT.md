# UAT checklist — virtual coach

Run after first deploy and after any task that touches the agent loop.

## Smoke

- [ ] `docker compose ps` — all 5 services up (`mem0-postgres`, `mem0-neo4j`, `mem0-api`, `coach-agent`, `telegram-bot`)
- [ ] `curl localhost:8800/health` returns `{"status":"ok"}`
- [ ] `docker compose logs coach-agent | grep -i error` empty
- [ ] `docker compose logs telegram-bot | grep -i error` empty

## Allowlist

- [ ] Send Telegram message from an allowed ID — coach replies
- [ ] Send Telegram message from a non-allowed ID — no reply, log entry "rejecting user_id=..."

## Onboarding flow

- [ ] Have a friend DM the bot from a non-allowed Telegram account — bot replies "pending"
- [ ] Admin's Telegram receives a message with the requester's info + Approve/Deny buttons
- [ ] Tap Approve — admin sees "✅ Approved", requester gets "You're in"
- [ ] Requester sends a follow-up message — coach replies normally
- [ ] `access.json` on disk shows the new user_id in `allowed_user_ids`
- [ ] Restart bot — approved user can still chat (persistence)

## Voice

- [ ] Send a 5-sec voice message in English — transcript → coach reply
- [ ] Send a 5-sec voice message in Spanish — transcript → coach reply

## Crisis filter

- [ ] Type "I want to die" — coach returns the canned 988/116/findahelpline reply (NOT a normal reply)
- [ ] Type "últimamente quiero quitarme la vida" — same canned reply

## Multi-turn voice

- [ ] Three back-and-forth turns from one user — coach references prior turns (not amnesiac)
- [ ] Three back-and-forth turns from a second user — separate context (no leakage)

## Inbox

- [ ] After 3 turns, `ls vault/_inbox/<user_id>/*.md` shows 3 entries
- [ ] Run `python -m sidecar.promote_to_notion` — Notion shows 3 new tasks

## Vault rebuild

- [ ] `python -m vault_rag.rebuild_cron` exits 0 in <3 min
- [ ] `ls services/coach/vault_rag/storage/` shows `graph_store.json` + `docstore.json`

## Eval

- [ ] `python -m eval.single_turn.run` writes `/eval-runs/single-turn.jsonl` with 10 rows
- [ ] `python -m eval.simulated_athlete.nightly_eval` writes `/eval-runs/nightly.jsonl` with ~30 rows
- [ ] Mean `frontal` score ≥ 3.5 across all rows
- [ ] No `grounding` score < 3

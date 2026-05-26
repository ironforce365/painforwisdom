# Virtual coach (Stack C)

Telegram-fronted endurance coach over Gonzalo's Obsidian vault. See spec at
`docs/superpowers/specs/2026-05-24-virtual-coach-design.md` and plan at
`docs/superpowers/plans/2026-05-25-virtual-coach-stack-c.md`.

## Architecture

```
Telegram → telegram-bot (allowlist + Whisper) → HTTP → coach-agent → ClaudeSDKClient
                                                          │
                                                          ├─ MCP: vault_rag (LlamaIndex PropertyGraph)
                                                          ├─ MCP: user_memory (per-user scratchpad)
                                                          └─ MCP: mem0 (long-term facts)
                                                          │
                                                          └─ post-turn → /vault/_inbox/<user>/<ts>.md
                                                                          └─ sidecar → Notion review queue
```

## Setup

1. `cp .env.template .env.coach` and fill secrets.
2. `claude setup-token` on your laptop → copy `CLAUDE_CODE_OAUTH_TOKEN` into `.env.coach`.
3. **Do NOT** set `ANTHROPIC_API_KEY` in `.env.coach` — it silently shadows OAuth.
4. Add allowed Telegram numeric user IDs to `access.json`.
5. `docker compose --env-file .env.coach -f docker-compose.yml build`
6. `docker compose --env-file .env.coach -f docker-compose.yml up -d`
7. First-time vault index build:
   ```bash
   docker compose exec coach-agent python -m vault_rag.rebuild_cron
   ```
8. Send a Telegram message to verify.

## Cron sidecars

See `crontab.example`. Install with `crontab crontab.example` on the host or wire via systemd timers.

## Quota notes

- From 2026-06-15 the Agent SDK draws a separate monthly credit bucket on the Max plan.
- `quota_monitor.py` reads `total_cost_usd` from agent JSONL and alerts Telegram at 80%.
- Authoritative billing is at https://console.anthropic.com — the SDK number is a client-side estimate.

## Single-user OAuth ToS

The OAuth subscription token is authorized for one principal (Gonzalo). Pilot users send messages
that the coach processes ON BEHALF OF Gonzalo — they are addressees, not principals.

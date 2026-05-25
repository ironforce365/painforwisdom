# Virtual coach service

Stack C implementation. See plan: `docs/superpowers/plans/2026-05-25-virtual-coach-stack-c.md`.

## Quick start (post-implementation)

```bash
cp .env.template .env.coach   # fill in
docker compose --env-file .env.coach -f docker-compose.yml up -d
```

## Services

- `mem0-postgres`, `mem0-neo4j`, `mem0-api` — long-term memory
- `coach-agent` — claude-agent-sdk service (HTTP on :8800)
- `telegram-bot` — bot polling, allowlist, voice→Whisper→agent

## Cron sidecars

- `sidecar/classify_themes.py` — every 30 min
- `sidecar/promote_to_notion.py` — hourly
- `sidecar/quota_monitor.py` — every 15 min
- `vault_rag/rebuild_cron.py` — nightly 02:00
- `eval/simulated_athlete/nightly_eval.py` — nightly 03:00
```

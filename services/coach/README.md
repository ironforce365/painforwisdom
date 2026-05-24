# Coach Service (Phase 1)

Telegram-fronted virtual coach for 1–10 athletes. Khoj + Mem0 + Claude Opus 4.7.

## Operations

Start: `docker compose -f services/coach/docker-compose.yml --env-file services/coach/.env up -d`
Stop:  `docker compose -f services/coach/docker-compose.yml down`
Logs:  `docker compose -f services/coach/docker-compose.yml logs -f khoj`

## Sidecars (run via cron on host)

- Every 5 min: `python -m services.coach.sidecar.log_conversations`
- Weekly:      `python -m services.coach.sidecar.promote_to_notion`
- Weekly:      `python -m services.coach.sidecar.quota_monitor`

## Nightly eval

Cron: `python -m services.coach.eval.nightly_eval`

Reports: `eval-runs/YYYY-MM-DD/aggregate.md`

## Spec

`docs/superpowers/specs/2026-05-24-virtual-coach-design.md`

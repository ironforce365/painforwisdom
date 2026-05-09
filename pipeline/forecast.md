# Pipeline Cost Forecast

## Per-run totals
- Input tokens (incl. cache): **10,969**
  - Fresh: 1,809
  - Cache writes: 9,160
  - Cache reads: 0
- Output tokens (est.): **3,700**
- **API-path cost: $0.0953 / run**
- **Subscription-path cost: $0.00 / run** (Pro/Max)

## Subscription quota impact (Pro/Max 5x assumed)
- Sonnet messages per run: **4**
- 5h-window quota share: **~2.0%** per run
- Max safe replays per 5h window: **~50**
- ITPM burst share (back-to-back): ~21.9%

## Per-stage breakdown
| Stage | Model | System | Fresh | Cache W | Cache R | Out | API $ |
|---|---|---:|---:|---:|---:|---:|---:|
| extract | claude-sonnet-4-6 | 2,102 | 198 | 2,102 | 0 | 600 | $0.0175 |
| kb-curator | claude-sonnet-4-6 | 2,804 | 675 | 2,804 | 0 | 400 | $0.0185 |
| writer | claude-sonnet-4-6 | 2,046 | 461 | 2,046 | 0 | 1,500 | $0.0316 |
| research | claude-sonnet-4-6 | 2,208 | 475 | 2,208 | 0 | 1,200 | $0.0277 |

## Assumptions
- pro_max5_sonnet_msgs_per_5h: 200
- sonnet_itpm_per_minute: 50000
- validator: pure-Python (no LLM tokens)
- notion_stages: REST API (no LLM tokens)
- transcribe: local Whisper (no LLM tokens)

# PoC — LangGraph mini-pipeline

3-stage proof-of-concept for replacing Paperclip:

1. **transcribe** — shells out to `extract_transcription.sh` (existing Whisper wrapper).
2. **extract** — calls Anthropic via the official SDK using the existing `coaching-thought-extractor` agent prompt body. Anthropic prompt cache enabled on the system prompt.
3. **notify** — shells out to `telegram_io.sh send` with the Core Insight.

Telemetry is appended to `poc/runs.jsonl` (one row per stage + a `summary` row).

## Why this exists

Validate two things before committing to a full LangGraph migration:

1. The new stack hits the per-transcript wall-clock budget (PoC target: **< 5 min**; full-pipeline target: < 10 min). Today's Paperclip baseline on `bulk-daily/PXL_20260413_194231193.mp4` was ~30 min, with Stage 1 alone taking 15m28s.
2. Subscription billing via `claude setup-token` works end-to-end. PoC prefers `ANTHROPIC_AUTH_TOKEN` (Bearer auth, OAuth, $0 marginal cost on Pro/Max) and falls back to `ANTHROPIC_API_KEY` (credits) if absent.

## Setup

```bash
conda create -n painforwisdom-poc python=3.11 -y
conda activate painforwisdom-poc
pip install -r poc/requirements.txt
```

Authenticate (pick one):

```bash
# Option A — subscription billing (preferred)
claude setup-token
# paste the printed token into .env as:  ANTHROPIC_AUTH_TOKEN=...

# Option B — API credits (fallback)
# put ANTHROPIC_API_KEY=sk-ant-... in .env
```

`.env` must also contain `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` (already present from the production pipeline).

## Run

```bash
conda activate painforwisdom-poc
cd /home/gonzalo/workspace/painforwisdom/painforwisdom
python -m poc.run_poc --video bulk-daily/PXL_20260413_194231193.mp4
```

Override the model with `POC_MODEL=claude-opus-4-6 python -m poc.run_poc ...`.

## Success metric

| Metric                | Target                          | Source                             |
|-----------------------|---------------------------------|------------------------------------|
| End-to-end wall-clock | **< 5 min** (PoC) / < 10 min (full pipeline) | `poc/runs.jsonl` `summary.total_duration_s` |
| Transcribe stage      | < ~5 min (Whisper-bound)        | `runs.jsonl` `transcribe.duration_s` |
| Extract stage         | < 90 s on Sonnet 4.6            | `runs.jsonl` `extract.duration_s`    |
| Cost per run          | $0 (subscription) or < $0.10 (API) | `runs.jsonl` `extract.cost_usd`   |
| Telegram delivered    | message visible on phone        | manual confirm                     |

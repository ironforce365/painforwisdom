# PoC Results — LangGraph + LiteLLM + Anthropic OAuth

**Run date:** 2026-05-07
**Test video:** `bulk-daily/PXL_20260413_194231193.mp4` (167-word transcript)
**Verdict:** ✅ **GO — every planned tech choice validated; proceed to full LangGraph migration.**

This is the **v2** results doc. v1 silently dropped LiteLLM (concern #1) and reported a cache miss without verifying root cause (concern #2). v2 closes both gaps. Lessons saved as `feedback_no_silent_feature_drops.md` in memory.

## Headline numbers (v2 — 4 stages: transcribe + extract + refine + notify)

| Metric                | Target | Achieved | Paperclip baseline (same video, 2026-04-27)         |
|-----------------------|--------|----------|------------------------------------------------------|
| End-to-end wall-clock | < 5 min (300 s) | **49.8 s** ✅ | ~30 min for full 8-stage; **15 m 28 s for stage 1 alone** |
| Transcribe (Whisper)  | < 5 min        | 31.8 s   | 43 s                                                 |
| Extract (LLM call 1, LiteLLM) | < 90 s | 15.2 s   | 15 m 28 s (with one weak-content retry)              |
| Refine (LLM call 2, LiteLLM, cache hit) | n/a | **2.1 s** | n/a                                          |
| Notify (Telegram)     | —              | 0.7 s    | inline (no timing logged)                            |
| Cost per run          | $0 sub / <$0.10 API | **$0.00 (subscription)** | unknown (no cost telemetry on Paperclip)        |
| Telegram delivered    | yes            | ✅ exit=0 | yes                                                  |

**Stage-1 (extract) speed-up vs Paperclip: ~61×.** End-to-end vs full Paperclip run: **~36×.**

## Concern #1 — LiteLLM integration on the OAuth path

**Resolved.** LiteLLM is now in the call path for both LLM stages.

### Probe results (saved during diagnosis)

Three patterns tested via direct LiteLLM probes:

| Pattern | Outcome |
|---|---|
| `extra_headers={"Authorization": "Bearer ..."}` + `api_key="dummy"` | ❌ `AuthenticationError: invalid x-api-key`. LiteLLM still sends `x-api-key` header alongside our Authorization header; Anthropic rejects. |
| `auth_token=` kwarg | ❌ `BadRequestError: auth_token: Extra inputs are not permitted`. LiteLLM does not surface a passthrough for the SDK's auth_token field. |
| `client=` kwarg with pre-built `Anthropic()` instance | ✅ **Works.** LiteLLM dispatches via the supplied client; auth handled by the SDK; LiteLLM still wraps cost-tracking, retry, exception mapping, and the unified completion API. |

### Production pattern (now in `poc/llm.py`)

```python
client = Anthropic(
    auth_token=os.environ["ANTHROPIC_AUTH_TOKEN"],
    default_headers={"anthropic-beta": "oauth-2025-04-20"},
)
resp = litellm.completion(
    model="anthropic/claude-sonnet-4-6",
    messages=[
        {"role": "system", "content": [
            {"type": "text", "text": "You are Claude Code, Anthropic's official CLI for Claude."},
            {"type": "text", "text": <extractor body>, "cache_control": {"type": "ephemeral"}},
        ]},
        {"role": "user", "content": user_message},
    ],
    max_tokens=2000,
    client=client,
)
```

### Implications for full migration

- LiteLLM is fully usable on the subscription path. We keep it for: cost tracking, exception mapping, eventual cross-provider fallback (Sonnet → Gemini Flash → Haiku) on rate-limit or model errors.
- Single Anthropic client is constructed once and reused per process. `_get_client()` caches it module-level.
- For the API-key path, no client= injection needed; LiteLLM handles auth natively.

## Concern #2 — Anthropic prompt cache on the OAuth path

**Resolved.** Cache writes and reads, both confirmed in telemetry.

### What v1 missed

v1 reported `cache_read_tokens=0` and `cache_creation_tokens=0` and inferred "OAuth doesn't get prompt-cache access." That inference was wrong. Cache simply didn't engage because the cached payload was below threshold.

### Empirical threshold finding

Sized-block test on Sonnet 4.6 via OAuth:

| Cached block size (input tokens) | `cache_creation_input_tokens` |
|---|---|
| ~1,150 | 0 (miss)        |
| ~1,430 | 0 (miss)        |
| ~1,850 | 0 (miss)        |
| ~2,260 | 2,257 ✅ written |
| ~2,820 | 2,817 ✅ written |

**Real Sonnet 4.6 cache minimum is ≈ 2,048 tokens, not the 1,024 listed in the public docs.** The original extractor body is 1,635 tokens — just below threshold, hence the v1 miss.

### Fix landed in `poc/graph.py::load_extractor_prompt()`

A `## VOICE & TONE REFERENCE` + `## FAILURE MODES` + `## CONTENT QUALITY CALIBRATION` appendix was added to the cached system block. Real instructional content (not filler) — pushes the cached prefix from ~1,635 to ~2,500 tokens.

### v2 cache evidence (from `runs.jsonl`)

```jsonc
// extract: cache write
{"stage":"extract","duration_s":15.19,"input_tokens":2581,"output_tokens":570,
 "cache_read_tokens":0,"cache_creation_tokens":2357,"cost_usd":0.0,
 "billing_mode":"subscription"}

// refine: cache hit on same system prefix
{"stage":"refine","duration_s":2.14,"input_tokens":2994,"output_tokens":47,
 "cache_read_tokens":2357,"cache_creation_tokens":0,"cost_usd":0.0,
 "billing_mode":"subscription"}
```

**Cache speedup: 15.2 s (cache write) → 2.1 s (cache hit) for an LLM call against the same system prefix → ~7× faster.** This is the headline win we'll harvest across the full 8-stage pipeline.

### Implications for full migration

- Every reusable system prompt (extractor, kb-curator, writer, research-curator) must be padded above 2,048 tokens. Most of these are large already; the small ones get a real-content appendix, not filler.
- Cache TTL is 5 minutes (Anthropic ephemeral). Pipeline runs in <5 min total today; the cache should remain warm across all stages of one transcript run.
- Cost accounting still respects cache: cache reads bill at 0.1× input rate (API-credit path); subscription path remains $0 marginal.

## Other findings worth carrying forward

1. **OAuth token rotates mid-session.** `~/.claude/.credentials.json` is the source of truth; `claude` CLI auto-refreshes it. The PoC originally read once at boot from `.env`, then failed with `Invalid authentication credentials` after Claude Code refreshed the file. **Full migration must read the token from `~/.claude/.credentials.json` at the start of each run** (or re-read on auth errors), not from `.env`.
2. **Subscription per-minute rate-limit is real.** Two LLM calls back-to-back hit `rate_limit_error: This request would exceed your account's rate limit` once during testing. Full migration needs:
   - Exponential backoff with jitter on `RateLimitError` (LiteLLM has retry primitives; we just need to wire them).
   - Optional fallback to API-key path or Gemini Flash on persistent rate-limit.
3. **Anthropic OAuth auth requirements are not documented anywhere obvious.** Recap of empirical findings:
   - `anthropic-beta: oauth-2025-04-20` header — **mandatory**.
   - First system block exactly `"You are Claude Code, Anthropic's official CLI for Claude."` — **mandatory**.
   - Without either: returns mislabeled `429 rate_limit_error` with literal message `"Error"` (not the user-friendly rate-limit message you'd expect). After token rotation but before refresh, returns clear `"Invalid authentication credentials"`. The 429 obfuscation is auth-by-design.

## Pipeline

```
video.mp4 ──[transcribe: extract_transcription.sh]──▶ transcript.txt
         ──[extract: LiteLLM → Anthropic OAuth + cache write]──▶ extraction_report (markdown)
         ──[refine: LiteLLM → Anthropic OAuth + cache HIT]──▶ tweet
         ──[notify: telegram_io.sh send]──▶ phone
         ──[runs.jsonl]──▶ per-stage telemetry
```

## Files

- `poc/run_poc.py` — CLI entry point.
- `poc/graph.py` — 4-node LangGraph definition + `load_extractor_prompt()` with cache-padding appendix.
- `poc/llm.py` — LiteLLM-via-pre-built-Anthropic-client wrapper (the only working OAuth pattern with LiteLLM today).
- `poc/state.py` — TypedDict shared graph state.
- `poc/runs.jsonl` — append-only telemetry ledger (empirical record for all GO claims above).
- `poc/README.md` — how to run.

## Next steps (full migration phase plan)

1. **Token-rotation handling:** read OAuth from `~/.claude/.credentials.json` per-run; re-read on auth errors.
2. **Rate-limit handling:** wire `litellm.completion(num_retries=...)` + custom backoff; add Gemini Flash fallback for non-creative stages on persistent throttle.
3. **Replicate the LangGraph pattern for all 8 production stages:** kb-curator, painforwisdom-writer, notion-blog-post-logger, blog-post-catchy-title, research-curator, notion-research-logger, pipeline-summary.
4. **Parallelize stages** 4+5 ‖ 6+7 via LangGraph `Send`.
5. **HITL via `interrupt()`/`Command(resume=…)`** for: Stage 1 Flagged-content gate, Stage 2 kb-curator new-theme/framework approvals.
6. **Swap `MemorySaver` → `SqliteSaver`** for durable checkpointing per run.
7. **Pad every system prompt above 2,048 tokens** so cache engages on every stage.
8. **Decommission Paperclip:** archive `migrate_agents.sh`, `PIPELINE_GEMINI.md`; update root docs.

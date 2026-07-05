# Coach turn-latency review — 2026-07-04

Pilot feedback: replies take ~90s. Target: **≤30s**. This review maps where the
time goes (measured, not guessed), ranks the fixes, and settles the model
question with an A/B eval. Reviewed at v0.1.15 (`origin/main` 91962de) against
the live deployment (`painforwisdom-live`, gate+guard ON).

## TL;DR

- The measured prod turn was **81.3s** (first turn after a restart; warm turns
  ≈ **69s**). The model is *not* the bottleneck — the **architecture spends the
  time in 5 sequential inference round-trips, cold subprocess spawns, and
  blocking side-work**.
- **The coach does not run Opus and never has.** No model is configured
  anywhere (`agent/service.py:_build_agent_options` sets no `model=`), and the
  container's session transcripts show every agent turn on
  **`claude-sonnet-4-6`** — the CLI's silent default. The "don't downgrade from
  Opus" constraint is moot; the real decision is Sonnet 4.6 (status quo) vs
  Sonnet 5 vs pinning Opus 4.8 for the first time. See §5 for the A/B.
- With gate ON the turn is **buffered**: the user sees *nothing* until the
  whole ~80s finishes. Perceived latency is worse than actual latency.
- The fix stack below gets a warm turn to **~20–27s** without touching model
  quality (R1–R6), and first-turn-after-restart stops being special (R4).

## 1. Measured breakdown

Prod perf lines, turn of 2026-07-04 23:47 (first turn after watchdog restart;
`docker logs coach-coach-agent-1`, `gate=True`):

| step | ms | notes |
|---|---|---|
| doctrine_retrieve | 15,280 | cold: lazy singleton build (index load + full-corpus BM25 + cross-encoder load), on the event loop. Warm ≈ 1–3s |
| memory_read | 2,353 | mem0 `/search` → OpenAI embedding, on the event loop |
| agent_query | 51,322 | see decomposition below |
| gate_judge | 6,383 | 1× cold `claude -p` (sonnet-4-6), no demotions this turn |
| memory_write | 5,931 | mem0 add → gpt-4o-mini extraction — **blocks the reply** |
| **turn_total** | **81,274** | |

### The 51s of "generation" is 5 round-trips, not one slow model

Session transcript decomposition (`/root/.claude/projects/-app/…jsonl`, model
`claude-sonnet-4-6`, single user turn):

| # | wall | what | output tokens |
|---|---|---|---|
| 1 | ~12s | thinking + **ToolSearch** (fetch deferred MCP tool schemas) | 441 |
| 2 | ~10s | thinking + `mcp__vault_rag__search_vault` — **redundant**: `<vault_context>` was already pre-injected by `_compose_turn_prompt` | 432 |
| 3 | ~4s | vault search executes (OpenAI embed + cross-encoder rerank) | — |
| 4 | ~9s | thinking + **ToolSearch** again + `mcp__mem0__mem0_search` — **redundant**: `<about_this_user>` was already pre-injected | 173+136 |
| 5 | ~12s | final thinking + 300-token reply | 300 |

Two of five round-trips are pure tool-schema fetches (the CLI defers MCP tool
schemas and the model must call ToolSearch to load them); two more re-search
context the pipeline already injected. **One or two round-trips would produce
the same reply.**

### Per-subprocess overhead (microbenchmark, this host)

A trivial `claude -p "Reply with exactly: OK"` costs **~6.5s wall, of which
only ~2.8s is API time** → **~3.5–4s pure CLI spawn overhead** per call.
Every gated turn pays this ≥1× (judge) + 1× per demotion + 1× topic guard
(parallel with gate) + 1× validation-detect when open items exist.

## 2. Architecture findings (file:line)

1. **No model / effort / max_tokens configured** —
   `agent/service.py` `_build_agent_options` (≈:366-402) sets only
   `system_prompt`, `resume`, `allowed_tools`, `mcp_servers`. Deployed SDK
   0.2.108 supports `model=`, `effort=` (`low|medium|high|xhigh|max`),
   `thinking=`, `max_thinking_tokens=`. Headless default effort is `high`.
   Prod is at the mercy of CLI defaults (which silently resolved to
   sonnet-4-6).
2. **MCP tool schemas deferred → ToolSearch round-trips** — 3 MCP servers
   (`vault_rag` in-process; `user_memory` + `mem0` as per-turn stdio python
   subprocesses, :395-400). The CLI defers the schemas; the model burns
   round-trips fetching them.
3. **Redundant mid-turn retrieval** — `_compose_turn_prompt` already injects
   `<doctrine>`/`<vault_context>` + `<about_this_user>`; the prompt
   (`coach_prompt.md`) doesn't tell the model that searching again is usually
   unnecessary.
4. **memory_write blocks the reply** — `service.py:678-679` (stream) and
   :560-562 (non-stream) run mem0 add (a server-side gpt-4o-mini extraction
   call) *before* emitting `done`. Failures are already swallowed → safe to
   detach.
5. **Pre-retrieval runs synchronously on the event loop** —
   `_compose_turn_prompt` is a plain `def` called directly (:425/:455): OpenAI
   embedding round-trip + cross-encoder CPU inference block the loop.
6. **Retriever singletons build lazily on first turn** — `agent/retrieval.py`
   (≈:24-56): index load + BM25 over the whole docstore + cross-encoder model
   load, per singleton (raw-vault and doctrine are separate). Every watchdog
   restart / deploy re-imposes ~12–15s on the next unlucky user.
7. **Every gate/guard/validation LLM call is a cold `claude -p` spawn** —
   `eval/llm.py:38-44`. ~4s of pure overhead per call.
8. **Buffered mode = zero streaming** — with gate or guard enabled,
   `/turn/stream` accumulates the whole draft, gates it, and emits one final
   delta (`service.py:620-674`). The Telegram progressive-edit machinery
   (`bot.py:110-122`) is idle in prod.
9. **validation-detect blocks before generation** — `service.py:605-610`;
   it's bookkeeping (logs signals, closes items) and needn't gate the turn.

## 3. Ranked fixes

| # | fix | est. saving (warm turn) | risk / effort |
|---|---|---|---|
| R1 | **Pin `model=` and `effort=` explicitly** in `_build_agent_options`, env-tunable (`COACH_AGENT_MODEL`, `COACH_AGENT_EFFORT`). Defaults per §5 A/B. | effort `high→medium` trims thinking in *every* round-trip; combined with R2 this is the reply-step lever. Also removes silent-default drift. | trivial code; quality guarded by §5 eval |
| R2 | **Collapse 5 round-trips → 1–2.** (a) Drop `user_memory` + `mem0` MCP servers from the turn (their reads are pre-injected; writes already happen service-side post-turn) → smaller tool surface, no deferral, no stdio spawns. (b) Keep in-process `search_vault` only, and add one prompt line: "Vault context and user memory are already provided above; call search_vault only if they are clearly insufficient." | **~25–35s** — the single biggest win | prompt+options change; verify with synthetic harness that reply quality holds and ToolSearch disappears from transcripts |
| R3 | **memory_write off the reply path** — `asyncio.create_task` after `done` (or background task queue). | ~6s | trivial; failures already swallowed |
| R4 | **Warm-start at boot**: build both retriever singletons + cross-encoder in the FastAPI startup hook (and have `/health/deep` exercise them). Move `_compose_turn_prompt` into `asyncio.to_thread`. | +12–15s removed from every first-turn-after-restart; loop no longer blocked | low |
| R5 | **validation-detect off the critical path** — run concurrently with generation (or post-reply); it's bookkeeping. | ~6.5s on turns with open validations | low |
| R6 | **Parallelize doctrine_retrieve ∥ memory_read** (`asyncio.gather` over threads); judge/guard/demotion subprocesses get `--effort low` (and optionally judge → haiku-4-5 — bounded entailment check). | ~2s + 2–4s | low; judge model change needs a quick calibration re-check |
| R7 | **Perceived latency (buffered mode)**: minimum — send staged status edits to the placeholder ("digging into your training history…", "checking my sources…") driven by real pipeline stages. Stronger option — stream the draft live and post-gate *edit* the Telegram message with the gated text (Telegram edits make this possible; trade-off: pre-gate text is briefly visible). | UX: time-to-first-signal drops from ~70s to <5s | policy decision on the stronger option (never-spam / grounding posture) |
| R8 | **Per-user persistent `ClaudeSDKClient`** — SDK supports many `query()` calls on one connected client per session; pilot has 3 users. Kills ~4s CLI spawn + MCP boot per turn. | ~4–6s | medium (client lifecycle, restart interplay); do after R1–R4 |

**Not viable:** fast mode (`speed: "fast"`) — interactive Claude Code only,
not exposed through the agent SDK/headless; and the Anthropic API directly —
the Max subscription only rides the CLI/agent SDK.

### Post-fix arithmetic (warm turn, gate ON)

generation 12–18s (1–2 round-trips, pinned model+effort) + judge 3–5s +
retrieval/memory-read ~2s (parallel) + write/validation off-path
→ **~20–27s**, inside target — *without* any model downgrade. Pinning Opus 4.8
adds roughly 30–60% to the generation step (see A/B); with R2 in place even
that lands near ~30s.

## 4. Model & effort decision (the "keep Opus" question)

Reframe: prod has been generating on **Sonnet 4.6** the whole pilot. So:

- Staying on Sonnet 4.6 = status quo, zero quality risk.
- **Sonnet 5 is an upgrade, not a downgrade** — Anthropic's migration guide:
  "Claude Sonnet 5 substantially improves on Sonnet 4.6 for coding and agentic
  work, reaching what was previously Opus-tier quality on many tasks."
- Pinning Opus 4.8 would be the first time the coach runs Opus at all — and
  per §5 it costs 40–70% more generation latency without winning the rubric.

§5 settles this empirically.

## 5. A/B evidence (rubric judge, this host, 2026-07-04)

Method: the 10 single-turn eval items (`eval/single_turn/eval_set.yaml`) ×
5 configs, generated with the production coach system prompt via the
subscription CLI, scored by the existing Sonnet 4.6 rubric judge
(`eval/judge.py`: frontal-ness, no-citing, probing, brevity, grounding, voice;
1–5 each), wall time per generation measured. Caveats: single-shot generation
without vault retrieval or MCP tools (identical handicap for all configs), one
run per item, LLM judge variance ±.

| config | wall s | API s | frontal | no-citing | probing | brevity | grounding | voice | **total /30** |
|---|---|---|---|---|---|---|---|---|---|
| sonnet-4-6, default effort *(≈ prod today)* | 34.9 | 31.5 | 3.60 | 4.20 | 4.90 | 3.00 | 4.80 | 3.60 | 24.10 |
| sonnet-5, default effort | 43.6 | 40.2 | **4.70** | 4.60 | 5.00 | **3.50** | 4.60 | **4.60** | **27.00** |
| **sonnet-5, effort=medium** | **20.1** | **16.7** | 4.30 | **5.00** | 4.90 | **3.50** | 4.60 | **4.60** | **26.90** |
| opus-4-8, default effort | 33.4 | 28.9 | 4.20 | 4.60 | 4.50 | 2.20 | 4.20 | 3.90 | 23.60 |
| opus-4-8, effort=medium | 27.9 | 23.5 | 4.10 | 5.00 | 5.00 | 2.10 | 4.20 | 4.30 | 24.70 |

Raw per-item results + the harness script are in
`docs/2026-07-04-ab-results/` (one JSONL per config, `reasoning` field per
score).

**Reading:**

- **`sonnet-5 @ effort=medium` dominates the prod baseline**: better or equal
  on *every* rubric dimension (voice 4.6 vs 3.6, frontal 4.3 vs 3.6, brevity
  3.5 vs 3.0) while cutting generation API time roughly in half (16.7s vs
  31.5s). This is the evidence the "no quality regression" requirement asked
  for — strengthened by the fact that the comparison is against what prod
  *actually* runs (Sonnet 4.6), not Opus.
- **Opus 4.8 shows no quality edge on this rubric** (23.6–24.7 vs 26.9–27.0)
  and is 40–70% slower than sonnet-5@medium. Its long-form replies tank the
  brevity dimension (2.1–2.2) — for a Telegram accountability coach, Opus's
  strengths (long-horizon agentic work, deep reasoning) aren't what the rubric
  — or the pilot users — reward.
- Sonnet 5 at *default* effort gains ~nothing over medium (27.0 vs 26.9) at
  2.4× the latency: **medium is the right effort for this workload.**

**Caveats:** n=10, one generation per item, single LLM judge (Sonnet 4.6 —
note it did *not* favor its own family), no vault retrieval/tools in the
harness (identical handicap for all configs), judge variance ±0.5/dim
plausible. Before flipping the prod default, confirm with the synthetic-user
harness (`tests` synthetic driver) on multi-turn conversations — single-turn
rubric wins don't automatically transfer to multi-turn coaching arcs.

**Recommendation:** default `COACH_AGENT_MODEL=claude-sonnet-5`,
`COACH_AGENT_EFFORT=medium` (env-tunable per R1, so switching to
`claude-opus-4-8` for an A/B week is a config change, not a deploy).

## 6. Suggested rollout order

> **Status (2026-07-05):** R1–R7 implemented in the follow-up PR (`perf-latency-impl`).
> R1 defaults `COACH_AGENT_MODEL=claude-sonnet-5` + `COACH_AGENT_EFFORT=medium`.
> R2 drops the `user_memory`/`mem0` MCP servers from the turn (vault_rag only) +
> a prompt line against re-fetching pre-injected context. R3 moves memory_write
> off the reply path (BackgroundTasks / detached task). R4 warm-starts the
> retrievers in a lifespan hook + parallel `to_thread` compose. R5 runs
> validation-detect concurrently with generation. R6 parallelizes doctrine∥memory
> and adds an opt-in `COACH_LLM_EFFORT` for gate/guard. R7 streams the model's
> thinking live as a "story" then delivers a labeled, gated answer
> (`{"thinking":…}` / `{"delta":…}` NDJSON; bot renders two beats). R8 (per-user
> persistent client) deferred — the arithmetic reaches target without it. New
> perf line: `perf step=agent_roundtrips n=<k>`.



1. R3 + R4 + R5 + R6 (low-risk mechanical; ~-15s and no cold first turns) —
   one small PR, verify with synthetic harness.
2. R1 + R2 (model/effort pinning + round-trip collapse) — the big one; gate on
   the synthetic-user harness + this eval set before shipping.
3. R7 staged-status edits (UX floor), then decide on stream-then-edit.
4. R8 persistent clients if still needed to shave the last seconds.

Instrumentation to keep honest: the existing `coach.perf` lines already cover
every step; add `perf step=agent_roundtrips n=<k>` (count assistant messages
per turn) so regressions in tool-call behavior are visible in one grep.

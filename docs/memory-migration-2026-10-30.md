# Auto-memory migration to Jarvis KB (2026-10-30, wo-013578d9)

Spec: `docs/superpowers/specs/2026-10-30-harness-housekeeping.md` section 3. Source: `~/.claude/projects/-home-gonzalo-workspace-painforwisdom-painforwisdom/memory/` (32 topic files + `MEMORY.md`), archived to `memory.archived-2026-10-30/` after the KB writes below were confirmed.

Verdicts: migrate (new KB entry), merge (already in an existing entry, or folded into a new one), drop.

| File | Verdict | KB id / reason |
|---|---|---|
| coach_allowlist_inplace_write.md | merge | kn-447a3dad has the rule; directory-volume exception folded into kn-080d54ce. Pilot user ids dropped (personal, prod state). |
| coach_debug_canary.md | merge | kn-a9ae1b7f (COACH_DEBUG footer, default on). |
| coach_docker_disk.md | migrate | kn-080d54ce (requirements.txt sync, CPU torch declined 2026-06-16, cleanup, open items). |
| coach_eval_package.md | migrate | kn-080d54ce (judge seam, vault empty in worktrees); test command already in kn-3f8e6a34 / CLAUDE.md. |
| coach_followups_built.md | merge | kn-447a3dad (release model, LIVE_DIR); stale "prod = v0.1.11" dropped (tags reach v0.1.15); gotchas into kn-080d54ce. |
| coach_proactive_and_synthetic.md | migrate | kn-dd14188b. |
| coach_shared_tag_namespace.md | merge | kn-447a3dad, kn-f7ffb135 (tags shared with augment). |
| coach_turn_latency_breakdown.md | migrate | kn-91eb26ee (PR 77 merged 2026-10-30, PR 78 still open draft). |
| coach_watchdog_uptime.md | merge | kn-447a3dad (watchdog, DNS pin); asyncio.timeout and log black-hole gotchas into kn-080d54ce. |
| feedback_absolute_paths.md | drop | Duplicates `~/.claude/CLAUDE.md`. |
| feedback_audio_overview_format.md | migrate | kn-3e561e86. |
| feedback_cost_forecast_before_replay.md | migrate | kn-53702cc0 (with why). |
| feedback_dev_flow_worktree_to_main.md | merge | kn-447a3dad has the flow; the why folded into kn-4a81fb2a item 4. |
| feedback_drop_catchy_title.md | drop | Moot after spec 2.3; kn-25ffb5c1 already says never re-add. |
| feedback_litellm_gateway.md | migrate | kn-7fe8150e. |
| feedback_loop_reframe_5streams.md | migrate | kn-5278d08d (stream roadmap, repo-verified PRs 62/64/66/67). |
| feedback_no_derivative_sources.md | merge | kn-f9b7ae7f ("format conversions are output sinks"). |
| feedback_no_silent_feature_drops.md | migrate | kn-4a81fb2a item 1. |
| feedback_paragraph_level_theming.md | merge | kn-f9b7ae7f ("map themes per paragraph"). |
| feedback_persist_review_artifacts.md | migrate | kn-4a81fb2a item 3. |
| feedback_poc_before_migration.md | migrate | kn-4a81fb2a item 2. |
| feedback_vault_vs_notion_themes.md | merge | kn-f9b7ae7f. Its `_THEME_TO_AGENT` pointer is stale (not in `pipeline/nodes/notion_research.py`), not carried. |
| grounding_calibration_finding.md | migrate | kn-5278d08d. Flags a contradiction: "temp locked at 6" vs config `_DEFAULT = 5`, not wired in compose. |
| pipeline_perf_baseline.md | merge | kn-25ffb5c1 (baseline, target); canonical video + PoC target into kn-4a81fb2a item 2. |
| pr52_grounding_gate_branch.md | drop | History: PR 52 closed, gate landed via PR 62. |
| project_augment_restart_hardening_todo.md | migrate | kn-a9dd6331 TODO 2. |
| project_monitor_decouple_todo.md | migrate | kn-a9dd6331 TODO 3. |
| project_pipeline_release_folder_todo.md | migrate | kn-a9dd6331 TODO 1. |
| stream2_validation_loop.md | merge | kn-5278d08d, kn-a9ae1b7f (gate ON, rollback). |
| stream3_doctrine_memory.md | merge | kn-f9b7ae7f (doctrine vs memory); PR 66/67 merged, recorded in kn-5278d08d. "PR #66 open" in MEMORY.md was stale. |
| subscription_cli_judge.md | migrate | kn-53702cc0. |
| user_ultra_subscription.md | migrate | kn-53702cc0. |

Stale KB entries corrected (spec 3.4): kn-e17e2272 retracted, superseded by kn-a9dd6331 (PR 77 merged; CPU-only torch was declined, not a TODO). kn-f08e540c retracted, superseded by kn-a9ae1b7f (PR 77 merged).

Prod facts carried into kn-a9dd6331 are marked unverified: workers cannot read coach prod (kn-775ab565).

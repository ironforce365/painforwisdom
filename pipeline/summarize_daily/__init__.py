"""Daily summarizer subsystem (Phase 4).

Picks the most-pending reachable cluster from Notion Research Tasks, fetches
sources, renders 3 brief files (deep-dive / application / audio-prompts),
optionally uploads to NotebookLM via the Phase 6c publisher, and marks the
rows Summarized.

CLI:
    python -m pipeline.summarize_daily --dry-run
    python -m pipeline.summarize_daily --apply
    python -m pipeline.summarize_daily --apply --mcp-publish

Layout:
    __main__.py             # CLI orchestrator
    fetcher.py              # URL → clean text dispatcher
    clusterer.py            # Notion → row selection
    brief_writer.py         # rows → 3 brief files (delegates to v2 PoC)
    notion_state.py         # mark rows Summarized
    notebooklm_publisher.py # Phase 6c MCP publisher
"""

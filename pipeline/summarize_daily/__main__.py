"""Daily summarizer CLI entry point.

  python -m pipeline.summarize_daily --dry-run
  python -m pipeline.summarize_daily --apply
  python -m pipeline.summarize_daily --apply --mcp-publish

--dry-run prints the cluster that would be picked + cost forecast. No fetch.
--apply runs the full pipeline. --mcp-publish additionally pushes to NotebookLM.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

from dotenv import load_dotenv

load_dotenv(PROJECT_ROOT / ".env")

from pipeline.runtime import append_metric  # noqa: E402
from pipeline.summarize_daily.brief_writer import write_brief  # noqa: E402
from pipeline.summarize_daily.clusterer import (  # noqa: E402
    Cluster,
    MIN_ROWS_PER_BRIEF,
    fetch_pending_rows,
    pick_cluster,
)
from pipeline.summarize_daily.fetcher import FetchError  # noqa: E402
from pipeline.summarize_daily.notion_state import mark_summarized  # noqa: E402


DAILY_LOG = PROJECT_ROOT / "reports" / "daily-summarizer-runs.jsonl"


def _print_dry_run(cluster: Cluster) -> None:
    print(f"Picked theme: {cluster.theme}")
    print(f"Sub-angle:    {cluster.sub_angle}")
    print(f"Vault entry:  {cluster.vault_entry}")
    print(f"Rows ({len(cluster.rows)}):")
    for r in cluster.rows:
        url = r.get("alt_source_url") or r.get("source_url") or "(no url)"
        print(f"  - {r.get('title','')[:60]:60s}  {r.get('type','?'):8s}  {url}")
    print("\nEstimated LLM cost: ~$0.40 (per-row summaries + synthesis + application + prompts)")
    print("No fetch, no LLM calls, no Notion writes performed.")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    g = parser.add_mutually_exclusive_group(required=True)
    g.add_argument("--dry-run", action="store_true")
    g.add_argument("--apply", action="store_true")
    parser.add_argument("--mcp-publish", action="store_true", help="Upload to NotebookLM.")
    parser.add_argument(
        "--skip-themes",
        default="",
        help="Comma-separated theme names to skip this run.",
    )
    parser.add_argument(
        "--max-cost-usd",
        type=float,
        default=1.0,
        help="Halt if forecast exceeds this (default $1.00).",
    )
    args = parser.parse_args(argv)

    skip_themes = {t.strip() for t in (args.skip_themes or "").split(",") if t.strip()}

    print("Loading pending rows from Notion...")
    rows = fetch_pending_rows()
    print(f"Pending reachable rows: {len(rows)}")
    if not rows:
        print("Research queue empty — no brief today.")
        return 0

    cluster = pick_cluster(rows, skip_themes=skip_themes)
    if cluster is None:
        print("No eligible cluster after skip filter.")
        return 0

    if args.dry_run:
        _print_dry_run(cluster)
        return 0

    print(f"\n=== Building brief: {cluster.theme} / {cluster.sub_angle} ===")
    try:
        cluster_dir, skipped = write_brief(cluster)
    except FetchError as exc:
        # All sources failed — no brief is possible. Tell the user on Telegram
        # so it does not silently fail (the original bug).
        print(f"!! Brief aborted: {exc}")
        _send_fetch_failure_telegram(
            cluster,
            skipped=[{"url": r.get("alt_source_url") or r.get("source_url", ""),
                      "title": r.get("title", ""), "error": "all-rows-failed"}
                     for r in cluster.rows],
            aborted=True,
        )
        return 1
    print(f"Brief written: {cluster_dir}")
    if skipped:
        print(f"!! Skipped {len(skipped)} unreachable sources:")
        for s in skipped:
            print(f"   - {s['title'][:60]}  {s['url']}  ({s['error']})")
        # Filter cluster.rows down to survivors before Notion marking so we
        # don't mark a failed source as Summarized.
        surviving_urls = {
            s["url"] for s in [
                {"url": (r.get("alt_source_url") or r.get("source_url") or "")}
                for r in cluster.rows
            ]
        } - {s["url"] for s in skipped}
        cluster.rows = [
            r for r in cluster.rows
            if (r.get("alt_source_url") or r.get("source_url") or "") in surviving_urls
        ]
        if len(cluster.rows) < MIN_ROWS_PER_BRIEF:
            print(
                f"!! Only {len(cluster.rows)} source(s) survived (< MIN={MIN_ROWS_PER_BRIEF}); "
                "still publishing what we have but flagging the brief."
            )

    notebooklm_url = ""
    if args.mcp_publish:
        try:
            from pipeline.summarize_daily.notebooklm_publisher import publish

            result = publish(
                cluster_dir=cluster_dir,
                theme=cluster.theme,
                sub_angle=cluster.sub_angle,
                vault_entry_slug=cluster.vault_entry,
                sources=[
                    {
                        "title": r.get("title", ""),
                        "author_host": r.get("author_host", ""),
                        "type": r.get("type", ""),
                        "relevance": r.get("relevance", ""),
                    }
                    for r in cluster.rows
                ],
            )
            notebooklm_url = result.notebook_url
            print(f"NotebookLM: {result.notebook_url}  status={result.status}")
        except Exception as exc:  # noqa: BLE001
            print(f"!! MCP publish failed: {exc}")

    ok, errors = mark_summarized(cluster.rows, cluster_dir, notebooklm_url=notebooklm_url)
    print(f"\nNotion updates: {ok} ok, {len(errors)} failed.")
    for e in errors:
        print(f"  ! {e['page_id']}: {e['error']}")

    _send_telegram(cluster, cluster_dir, notebooklm_url, ok, len(errors), skipped=skipped)

    append_metric(
        DAILY_LOG,
        "daily-summarizer",
        theme=cluster.theme,
        sub_angle=cluster.sub_angle,
        rows=len(cluster.rows),
        notion_ok=ok,
        notion_fail=len(errors),
        notebooklm_url=notebooklm_url,
        cluster_dir=str(cluster_dir.relative_to(PROJECT_ROOT)),
    )
    return 0


def _summary_50w(cluster_dir: Path, cluster: Cluster) -> str:
    """Pull the first ≤50 words from application.md's opening paragraph (the
    cluster's punchline) and prepend theme/sub-angle. Deterministic, no LLM."""
    app_md = cluster_dir / "application.md"
    body = ""
    if app_md.exists():
        for para in app_md.read_text().split("\n\n"):
            stripped = para.strip()
            if stripped and not stripped.startswith("#") and not stripped.startswith(">"):
                body = stripped
                break
    words = body.split()
    if len(words) > 45:
        body = " ".join(words[:45]) + "…"
    return body or f"{cluster.theme} / {cluster.sub_angle} — {len(cluster.rows)} sources synthesized."


def _send_telegram(
    cluster: Cluster,
    cluster_dir: Path,
    notebooklm_url: str,
    notion_ok: int,
    notion_fail: int,
    skipped: list[dict] | None = None,
) -> None:
    try:
        from pipeline.telegram import send  # noqa: WPS433 (local import: optional dep)
    except Exception as exc:  # noqa: BLE001
        print(f"!! Telegram import failed: {exc}")
        return

    skipped = skipped or []
    summary = _summary_50w(cluster_dir, cluster)
    lines = [
        f"📚 Daily brief — {cluster.theme}",
        f"Sub-angle: {cluster.sub_angle}",
        f"Sources: {len(cluster.rows)} | Notion: {notion_ok} ok / {notion_fail} fail",
    ]
    if skipped:
        lines.append(f"⚠️ Skipped {len(skipped)} unreachable source(s) — see below.")
    lines.extend(["", summary, ""])
    if notebooklm_url:
        lines.append(f"NotebookLM ▶ {notebooklm_url}")
    else:
        lines.append(f"Cluster dir: {cluster_dir.relative_to(PROJECT_ROOT)}")
    if skipped:
        lines.append("")
        lines.append("Skipped sources:")
        for s in skipped:
            lines.append(f"• {s.get('title','')[:60]} — {s.get('error','')}")
            lines.append(f"  {s.get('url','')}")
        lines.append("")
        lines.append("To ban permanently: add the URL to config/fetch_denylist.txt.")
    text = "\n".join(lines)
    try:
        rc = send(text)
        print(f"Telegram send rc={rc}")
    except Exception as exc:  # noqa: BLE001
        print(f"!! Telegram send failed: {exc}")


def _send_fetch_failure_telegram(
    cluster: Cluster,
    skipped: list[dict],
    aborted: bool,
) -> None:
    """Notify when fetch errors block the brief (full or partial)."""
    try:
        from pipeline.telegram import send  # noqa: WPS433
    except Exception as exc:  # noqa: BLE001
        print(f"!! Telegram import failed: {exc}")
        return
    header = "❌ Daily brief ABORTED" if aborted else "⚠️ Daily brief — fetch failures"
    lines = [
        header,
        f"Theme: {cluster.theme}  /  sub-angle: {cluster.sub_angle}",
        f"Failed sources: {len(skipped)} / {len(cluster.rows)}",
        "",
    ]
    for s in skipped:
        lines.append(f"• {s.get('title','')[:60]} — {s.get('error','')}")
        lines.append(f"  {s.get('url','')}")
    lines.append("")
    lines.append("Ban a URL: add it to config/fetch_denylist.txt.")
    try:
        rc = send("\n".join(lines))
        print(f"Telegram send (failure) rc={rc}")
    except Exception as exc:  # noqa: BLE001
        print(f"!! Telegram send failed: {exc}")


if __name__ == "__main__":
    sys.exit(main())

"""Daily summarizer CLI entry point.

  python -m pipeline.summarize_daily --dry-run
  python -m pipeline.summarize_daily --apply
  python -m pipeline.summarize_daily --apply --mcp-publish
  python -m pipeline.summarize_daily --apply --mcp-publish --count 3

--dry-run prints the cluster that would be picked + cost forecast. No fetch.
--apply runs the full pipeline. --mcp-publish additionally pushes to NotebookLM.
--count N produces up to N briefs in one run, each with its own audio overview
and its own Telegram message. Themes already used in the run are skipped so
each brief covers a different topic. Default 3 (≈3h of listening for Gonzalo's
commute + run window).
"""
from __future__ import annotations

import argparse
import os
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


def _daily_chat_id() -> str | None:
    """Dedicated Telegram channel for daily summaries. Falls back to the
    pipeline default ($TELEGRAM_CHAT_ID) if unset."""
    return os.environ.get("TELEGRAM_DAILY_SUMMARY_CHAT_ID") or None


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


def _run_one_brief(
    cluster: Cluster,
    *,
    mcp_publish: bool,
    index: int = 1,
    total: int = 1,
) -> tuple[int, set[str]]:
    """Build + publish + notify for a single cluster.

    Returns (exit_code, consumed_page_ids). consumed_page_ids covers all rows
    the cluster touched (success + skipped), so the outer loop won't re-pick
    them in subsequent iterations of the same run.
    """
    consumed_page_ids = {r.get("page_id", "") for r in cluster.rows if r.get("page_id")}

    print(f"\n=== Building brief: {cluster.theme} / {cluster.sub_angle} ===")
    try:
        cluster_dir, skipped = write_brief(cluster)
    except FetchError as exc:
        print(f"!! Brief aborted: {exc}")
        _send_fetch_failure_telegram(
            cluster,
            skipped=[{"url": r.get("alt_source_url") or r.get("source_url", ""),
                      "title": r.get("title", ""), "error": "all-rows-failed"}
                     for r in cluster.rows],
            aborted=True,
        )
        return 1, consumed_page_ids
    print(f"Brief written: {cluster_dir}")
    if skipped:
        print(f"!! Skipped {len(skipped)} unreachable sources:")
        for s in skipped:
            print(f"   - {s['title'][:60]}  {s['url']}  ({s['error']})")
        surviving_urls = {
            (r.get("alt_source_url") or r.get("source_url") or "")
            for r in cluster.rows
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
    audio_url = ""
    if mcp_publish:
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
            audio_url = result.audio_url
            print(
                f"NotebookLM: {result.notebook_url}  status={result.status}  "
                f"audio_url={'yes' if audio_url else 'no'}"
            )
        except Exception as exc:  # noqa: BLE001
            print(f"!! MCP publish failed: {exc}")

    # Notion gets the notebook URL (long-lived); audio_url is a CDN URL that
    # may rotate, so it's not the right thing to persist back to the source row.
    ok, errors = mark_summarized(cluster.rows, cluster_dir, notebooklm_url=notebooklm_url)
    print(f"\nNotion updates: {ok} ok, {len(errors)} failed.")
    for e in errors:
        print(f"  ! {e['page_id']}: {e['error']}")

    _send_telegram(
        cluster,
        cluster_dir,
        notebooklm_url,
        ok,
        len(errors),
        skipped=skipped,
        audio_url=audio_url,
        index=index,
        total=total,
    )

    append_metric(
        DAILY_LOG,
        "daily-summarizer",
        theme=cluster.theme,
        sub_angle=cluster.sub_angle,
        rows=len(cluster.rows),
        notion_ok=ok,
        notion_fail=len(errors),
        notebooklm_url=notebooklm_url,
        audio_url=audio_url,
        cluster_dir=str(cluster_dir.relative_to(PROJECT_ROOT)),
    )
    return 0, consumed_page_ids


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
    parser.add_argument(
        "--count",
        type=int,
        default=3,
        help="How many briefs to produce in this run (default 3, ≈3h of audio).",
    )
    args = parser.parse_args(argv)

    skip_themes = {t.strip() for t in (args.skip_themes or "").split(",") if t.strip()}

    print("Loading pending rows from Notion...")
    rows = fetch_pending_rows()
    print(f"Pending reachable rows: {len(rows)}")
    if not rows:
        print("Research queue empty — no brief today.")
        return 0

    # --dry-run shows what the FIRST pick would be — no need to expand to N for
    # the cost forecast since each brief costs roughly the same.
    if args.dry_run:
        cluster = pick_cluster(rows, skip_themes=skip_themes)
        if cluster is None:
            print("No eligible cluster after skip filter.")
            return 0
        _print_dry_run(cluster)
        if args.count > 1:
            print(f"\n(--count={args.count} would produce up to {args.count} briefs at ~$0.40 each.)")
        return 0

    consumed: set[str] = set()
    used_themes: set[str] = set(skip_themes)
    produced = 0
    final_rc = 0

    for i in range(1, max(args.count, 1) + 1):
        cluster = pick_cluster(
            rows,
            skip_themes=used_themes,
            excluded_page_ids=consumed,
        )
        if cluster is None:
            print(
                f"\nNo more eligible clusters after {produced} brief(s) "
                f"(target {args.count}). Stopping early."
            )
            break
        print(f"\n--- Daily brief {i}/{args.count} ---")
        rc, used_ids = _run_one_brief(
            cluster,
            mcp_publish=args.mcp_publish,
            index=i,
            total=args.count,
        )
        consumed |= used_ids
        used_themes.add(cluster.theme)
        if rc != 0:
            final_rc = rc
        else:
            produced += 1

    print(f"\nDone — {produced}/{args.count} brief(s) produced this run.")
    return final_rc


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
    audio_url: str = "",
    index: int = 1,
    total: int = 1,
) -> None:
    try:
        from pipeline.telegram import send  # noqa: WPS433 (local import: optional dep)
    except Exception as exc:  # noqa: BLE001
        print(f"!! Telegram import failed: {exc}")
        return

    skipped = skipped or []
    summary = _summary_50w(cluster_dir, cluster)
    # Progress-aware header so the dedicated channel reads as a queue:
    # `Audio 1/3 ready`, `Audio 2/3 ready`, etc.
    header = (
        f"🎧 Audio {index}/{total} ready — {cluster.theme}"
        if audio_url
        else f"📚 Brief {index}/{total} — {cluster.theme} (audio rendering)"
    )
    lines = [
        header,
        f"Sub-angle: {cluster.sub_angle}",
        f"Sources: {len(cluster.rows)} | Notion: {notion_ok} ok / {notion_fail} fail",
    ]
    if skipped:
        lines.append(f"⚠️ Skipped {len(skipped)} unreachable source(s) — see below.")
    lines.extend(["", summary, ""])
    # Audio URL is the click-to-listen link Gonzalo actually wants on his phone.
    # Notebook URL is kept as a fallback for "open the project in NotebookLM"
    # when the audio is still rendering or the CDN URL has rotated.
    if audio_url:
        lines.append(f"🎧 Listen ▶ {audio_url}")
        if notebooklm_url:
            lines.append(f"NotebookLM project: {notebooklm_url}")
    elif notebooklm_url:
        lines.append(f"NotebookLM ▶ {notebooklm_url}")
        lines.append("(audio still rendering — open the mobile app to listen)")
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
        rc = send(text, chat_id=_daily_chat_id())
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
        rc = send("\n".join(lines), chat_id=_daily_chat_id())
        print(f"Telegram send (failure) rc={rc}")
    except Exception as exc:  # noqa: BLE001
        print(f"!! Telegram send failed: {exc}")


if __name__ == "__main__":
    sys.exit(main())

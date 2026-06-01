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

    print(f"\n=== Building brief {index}/{total}: {cluster.theme} / {cluster.sub_angle} ===")
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
    except Exception as exc:  # noqa: BLE001
        # Anything else from the LLM/synthesis stack (rate limits, long-context
        # gating, transient 5xx, etc.) used to crash the whole systemd unit and
        # silently kill the remaining briefs in the run. Now: log, alert,
        # mark this cluster's rows as consumed (so the next iteration picks a
        # different theme), and keep going.
        print(f"!! Brief {index}/{total} crashed: {type(exc).__name__}: {exc}")
        _send_brief_crash_telegram(
            cluster,
            index=index,
            total=total,
            error=f"{type(exc).__name__}: {exc}",
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
    visuals: dict[str, str] = {}
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
            visuals = {
                "slide_deck": result.slide_deck_path,
                "mind_map": result.mindmap_path,
                "infographic": result.infographic_path,
            }
            print(
                f"NotebookLM: {result.notebook_url}  status={result.status}  "
                f"audio_url={'yes' if audio_url else 'no'}  "
                f"slides={'yes' if result.slide_deck_path else 'no'}  "
                f"mindmap={'yes' if result.mindmap_path else 'no'}  "
                f"infographic={'yes' if result.infographic_path else 'no'}"
            )
        except Exception as exc:  # noqa: BLE001
            print(f"!! MCP publish failed: {exc}")

    # Vault dir for the derivative theory + application entries written by
    # poc_brief_v2.run_cluster. Resolved here (not via the publisher) so the
    # Telegram message can surface it even if NotebookLM publish failed.
    sub_slug_for_vault = cluster_dir.name.split("--", 1)[-1] if "--" in cluster_dir.name else cluster_dir.name
    today_for_vault = cluster_dir.name.split("--", 1)[0] if "--" in cluster_dir.name else ""
    vault_brief_dir = (
        PROJECT_ROOT / "obsidian-vault" / "gonzalo-book" / "deep-dive"
        / cluster.theme / f"{today_for_vault}--{sub_slug_for_vault}"
        if today_for_vault else None
    )

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
        visuals=visuals,
        vault_dir=vault_brief_dir if vault_brief_dir and vault_brief_dir.exists() else None,
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
        slide_deck_path=visuals.get("slide_deck", ""),
        mindmap_path=visuals.get("mind_map", ""),
        infographic_path=visuals.get("infographic", ""),
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
    # Partial success exits 0 so systemd `Restart=on-failure` does not replay
    # already-published briefs. Crash details are already on Telegram per brief.
    # Only a zero-produced run signals the queue is genuinely stuck.
    if produced > 0:
        return 0
    return final_rc or 1


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


def _esc(text: str) -> str:
    """Escape `& < >` for Telegram HTML parse mode. Required for any dynamic
    content interpolated into the message — otherwise a theme name with `&`
    in it would 400 the sendMessage call."""
    return (text or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


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
    visuals: dict[str, str] | None = None,
    vault_dir: Path | None = None,
) -> None:
    try:
        from pipeline.telegram import send  # noqa: WPS433 (local import: optional dep)
    except Exception as exc:  # noqa: BLE001
        print(f"!! Telegram import failed: {exc}")
        return

    skipped = skipped or []
    visuals = visuals or {}
    summary = _summary_50w(cluster_dir, cluster)
    # Progress-aware header so the dedicated channel reads as a queue:
    # `Audio 1/3 ready`, `Audio 2/3 ready`, etc.
    header = (
        f"🎧 Audio {index}/{total} ready — {_esc(cluster.theme)}"
        if notebooklm_url
        else f"📚 Brief {index}/{total} — {_esc(cluster.theme)} (publish failed)"
    )
    lines = [
        header,
        f"Sub-angle: {_esc(cluster.sub_angle)}",
        f"Sources: {len(cluster.rows)} | Notion: {notion_ok} ok / {notion_fail} fail",
    ]
    if skipped:
        lines.append(f"⚠️ Skipped {len(skipped)} unreachable source(s) — see below.")
    lines.extend(["", _esc(summary), ""])
    # The `audio_url` field (Google CDN URL) is dropped from the message —
    # it requires Google Sign-In and isn't shareable. The NotebookLM project
    # URL opens the audio panel in the mobile app, which is what Gonzalo
    # actually clicks on his commute. Rendered as an HTML href so the URL
    # doesn't take up half the line.
    if notebooklm_url:
        lines.append(f'🎧 <a href="{notebooklm_url}">Listen</a>')
    else:
        lines.append(f"Cluster dir: <code>{_esc(str(cluster_dir.relative_to(PROJECT_ROOT)))}</code>")
    # Companion visuals live in the same NotebookLM studio panel as the audio,
    # so a single tap on Listen surfaces all of them. The line below is a status
    # indicator — it tells Gonzalo which artifacts to expect when he opens the
    # notebook on his commute, without burning screen space on three redundant
    # links to the same URL.
    visual_indicators = []
    if visuals.get("slide_deck"):
        visual_indicators.append("📊 Slides")
    if visuals.get("mind_map"):
        visual_indicators.append("🧠 Mind map")
    if visuals.get("infographic"):
        visual_indicators.append("🖼️ Infographic")
    if visual_indicators:
        lines.append(" + ".join(visual_indicators) + " in the same notebook")
    if vault_dir is not None:
        try:
            vault_rel = vault_dir.relative_to(PROJECT_ROOT)
        except ValueError:
            vault_rel = vault_dir
        lines.append(f"📁 Vault: <code>{_esc(str(vault_rel))}</code>")
    if skipped:
        lines.append("")
        lines.append("Skipped sources:")
        for s in skipped:
            lines.append(f"• {_esc(s.get('title','')[:60])} — {_esc(s.get('error',''))}")
            url = s.get("url", "")
            if url:
                lines.append(f'  <a href="{url}">source</a>')
        lines.append("")
        lines.append("To ban permanently: add the URL to <code>config/fetch_denylist.txt</code>.")
    text = "\n".join(lines)
    try:
        rc = send(text, chat_id=_daily_chat_id(), parse_mode="HTML")
        print(f"Telegram send rc={rc}")
    except Exception as exc:  # noqa: BLE001
        print(f"!! Telegram send failed: {exc}")


def _send_brief_crash_telegram(
    cluster: Cluster,
    *,
    index: int,
    total: int,
    error: str,
) -> None:
    """Notify when a brief crashes mid-LLM (rate limit, long context, transient
    upstream error). Distinct from a fetch failure: the cluster's sources were
    reachable but the synthesis step couldn't finish."""
    try:
        from pipeline.telegram import send  # noqa: WPS433
    except Exception as exc:  # noqa: BLE001
        print(f"!! Telegram import failed: {exc}")
        return
    lines = [
        f"⚠️ Brief {index}/{total} crashed — {_esc(cluster.theme)}",
        f"Sub-angle: {_esc(cluster.sub_angle)}",
        "",
        f"<code>{_esc(error[:500])}</code>",
        "",
        f"{total - index} brief(s) still to come this run — continuing.",
    ]
    try:
        rc = send("\n".join(lines), chat_id=_daily_chat_id(), parse_mode="HTML")
        print(f"Telegram send (crash) rc={rc}")
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
        f"Theme: {_esc(cluster.theme)}  /  sub-angle: {_esc(cluster.sub_angle)}",
        f"Failed sources: {len(skipped)} / {len(cluster.rows)}",
        "",
    ]
    for s in skipped:
        lines.append(f"• {_esc(s.get('title','')[:60])} — {_esc(s.get('error',''))}")
        url = s.get("url", "")
        if url:
            lines.append(f'  <a href="{url}">source</a>')
    lines.append("")
    lines.append("Ban a URL: add it to <code>config/fetch_denylist.txt</code>.")
    try:
        rc = send("\n".join(lines), chat_id=_daily_chat_id(), parse_mode="HTML")
        print(f"Telegram send (failure) rc={rc}")
    except Exception as exc:  # noqa: BLE001
        print(f"!! Telegram send failed: {exc}")


if __name__ == "__main__":
    sys.exit(main())

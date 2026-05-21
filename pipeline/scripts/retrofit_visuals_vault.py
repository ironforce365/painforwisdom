"""Retrofit slides + infographic + mind map + vault theory/application entries
onto already-published daily-summarizer briefs.

Context (2026-05-21): The 2026-05-21 06:00 daily-brief run executed without the
visuals + vault feature (PR #38 / `eea120b`) because the local `main` branch
had diverged from `origin/main` and never pulled the merged PR. The cluster
dirs at `briefs/<theme>/<date>--<sub-slug>/` contain `deep-dive.md`,
`application.md`, `audio-prompts.md`, `notebooklm_url.txt`, and `audio_url.txt`
but are missing `slides.pdf`, `mindmap.json`, `infographic.png`, AND the
companion vault entries at `obsidian-vault/gonzalo-book/deep-dive/<theme>/
<date>--<sub-slug>/{theory,application}.md`.

This script re-opens each existing NotebookLM notebook by its ID (from
`notebooklm_url.txt`), filters the source list to those belonging to a given
cluster (titles prefixed with `<cluster_dir.name>:`), and re-runs the three
visual generators (`nlm slides create`, `nlm infographic create`,
`nlm mindmap create`) scoped to that source subset. Then it generates the two
derivative vault entries via the same LLM calls poc_brief_v2 uses, writes
them, and commits the vault submodule.

Idempotent: a cluster is skipped if `slides.pdf`, `mindmap.json`, AND
`infographic.png` already exist AND the vault dir has `theory.md` and
`application.md`. Use `--force` to override.

Usage:
    # Today's 3 briefs only (foreground, fast)
    python -m pipeline.scripts.retrofit_visuals_vault --today

    # A specific cluster
    python -m pipeline.scripts.retrofit_visuals_vault --cluster briefs/amcc-effect/2026-05-21--amcc-and-voluntary-override-of-comfort-seeking

    # Everything (run in background; ~2h wall clock for 26 clusters)
    python -m pipeline.scripts.retrofit_visuals_vault --all

    # Dry run — print plan, no writes, no nlm calls
    python -m pipeline.scripts.retrofit_visuals_vault --all --dry-run
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
import traceback
from pathlib import Path
from typing import Any, Dict, List, Optional

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(PROJECT_ROOT / ".env")

from pipeline.runtime import VAULT_PATH  # noqa: E402
from pipeline.summarize_daily.notebooklm_publisher import (  # noqa: E402
    PublishError,
    _create_infographic,
    _create_mindmap,
    _create_slides,
    _download_artifact,
    _poll_artifact,
    _run_nlm,
    render_infographic_focus,
    render_slides_focus,
)
from pipeline.scripts.poc_brief_v2 import (  # noqa: E402
    _application_vault,
    _commit_vault,
    _theory_vault,
    _write_vault_entries,
)
from pipeline.telegram import send as telegram_send  # noqa: E402


BRIEFS_ROOT = PROJECT_ROOT / "briefs"
NOTEBOOK_URL_RE = re.compile(r"notebooklm\.google\.com/notebook/([0-9a-f-]+)")


def _extract_notebook_id(url_text: str) -> Optional[str]:
    m = NOTEBOOK_URL_RE.search(url_text or "")
    return m.group(1) if m else None


def _list_cluster_sources(notebook_id: str, cluster_name: str) -> List[str]:
    """Return source IDs whose title starts with `<cluster_name>:` so visual
    generation is scoped to exactly the 3-4 sources belonging to this brief
    (and not the entire per-theme accumulated notebook).
    """
    rc, stdout, _stderr = _run_nlm(["source", "list", notebook_id])
    if rc != 0:
        raise PublishError(f"nlm source list failed for {notebook_id}: {_stderr[:300]}")
    try:
        sources = json.loads(stdout)
    except (json.JSONDecodeError, ValueError):
        raise PublishError(f"could not parse nlm source list output: {stdout[:300]}")
    if not isinstance(sources, list):
        raise PublishError(f"nlm source list returned non-list: {type(sources).__name__}")
    matching: List[str] = []
    prefix = f"{cluster_name}:"
    listener_prefix = "Gonzalo's vault entry"  # the 4th source uses this label
    for s in sources:
        if not isinstance(s, dict):
            continue
        title = s.get("title") or ""
        sid = s.get("id") or ""
        if not sid:
            continue
        if title.startswith(prefix):
            matching.append(sid)
        elif listener_prefix in title:
            # Vault-entry source: included only if this cluster is the active
            # owner of the per-theme notebook. For per-cluster scoping we
            # exclude it to keep visuals focused on the brief's own content;
            # the vault entry is already a derivative of the deep-dive.
            pass
    return matching


def _parse_cluster_meta(cluster_dir: Path) -> Optional[Dict[str, str]]:
    """`briefs/<theme>/<YYYY-MM-DD>--<sub-slug>/`  →  metadata dict.

    Returns None if the path doesn't fit the cluster shape (e.g. it's a
    per-theme directory containing only `.notebooklm-id`).
    """
    parts = cluster_dir.relative_to(BRIEFS_ROOT).parts
    if len(parts) != 2:
        return None
    theme, date_sub = parts
    if "--" not in date_sub or len(date_sub) < len("YYYY-MM-DD--x"):
        return None
    today_str, _, sub_slug = date_sub.partition("--")
    if not re.match(r"^\d{4}-\d{2}-\d{2}$", today_str):
        return None
    return {
        "theme": theme,
        "today": today_str,
        "sub_slug": sub_slug,
        "cluster_name": date_sub,
    }


def _read_cluster(cluster_dir: Path) -> Optional[Dict[str, Any]]:
    if not (cluster_dir / "deep-dive.md").exists():
        return None
    if not (cluster_dir / "application.md").exists():
        return None
    url_file = cluster_dir / "notebooklm_url.txt"
    if not url_file.exists():
        return None
    notebook_id = _extract_notebook_id(url_file.read_text())
    if not notebook_id:
        return None
    return {
        "cluster_dir": cluster_dir,
        "notebook_id": notebook_id,
        "deep_dive_md": (cluster_dir / "deep-dive.md").read_text(),
        "application_md": (cluster_dir / "application.md").read_text(),
    }


def _is_complete(cluster_dir: Path, meta: Dict[str, str]) -> bool:
    visuals_done = all(
        (cluster_dir / fn).exists()
        for fn in ("slides.pdf", "mindmap.json", "infographic.png")
    )
    vault_dir = VAULT_PATH / "gonzalo-book" / "deep-dive" / meta["theme"] / meta["cluster_name"]
    vault_done = (vault_dir / "theory.md").exists() and (vault_dir / "application.md").exists()
    return visuals_done and vault_done


def _retrofit_one(cluster_dir: Path, *, force: bool, dry_run: bool) -> Dict[str, Any]:
    """Returns a result dict with `status`, `cluster`, `notebook_id`, and any
    error context. Never raises — callers tolerate partial failures so one bad
    cluster doesn't kill the batch."""
    meta = _parse_cluster_meta(cluster_dir)
    if meta is None:
        return {"status": "skip-not-a-cluster", "cluster": str(cluster_dir)}

    payload = _read_cluster(cluster_dir)
    if payload is None:
        return {"status": "skip-missing-files", "cluster": str(cluster_dir)}

    if not force and _is_complete(cluster_dir, meta):
        return {"status": "skip-already-complete", "cluster": str(cluster_dir)}

    result: Dict[str, Any] = {
        "status": "in_progress",
        "cluster": str(cluster_dir.relative_to(PROJECT_ROOT)),
        "theme": meta["theme"],
        "today": meta["today"],
        "sub_slug": meta["sub_slug"],
        "notebook_id": payload["notebook_id"],
        "visuals": {},
        "vault": {},
    }

    if dry_run:
        result["status"] = "dry-run"
        return result

    # --- Visuals -------------------------------------------------------
    try:
        source_ids = _list_cluster_sources(payload["notebook_id"], meta["cluster_name"])
        if not source_ids:
            result["status"] = "no-matching-sources"
            result["error"] = (
                f"no sources matching prefix '{meta['cluster_name']}:' "
                f"in notebook {payload['notebook_id']}"
            )
            return result
        result["source_count"] = len(source_ids)
    except PublishError as exc:
        result["status"] = "source-list-failed"
        result["error"] = str(exc)
        return result

    notebook_id = payload["notebook_id"]
    slides_id = mindmap_id = infographic_id = ""

    if force or not (cluster_dir / "slides.pdf").exists():
        try:
            slides_id = _create_slides(
                notebook_id, source_ids, render_slides_focus(meta["theme"], meta["sub_slug"])
            )
            (cluster_dir / "slide_deck_artifact_id.txt").write_text(slides_id + "\n")
            result["visuals"]["slides_artifact_id"] = slides_id
        except PublishError as exc:
            result["visuals"]["slides_error"] = str(exc)
    else:
        result["visuals"]["slides_status"] = "already-exists"

    if force or not (cluster_dir / "infographic.png").exists():
        try:
            infographic_id = _create_infographic(
                notebook_id, source_ids, render_infographic_focus(meta["theme"], meta["sub_slug"])
            )
            (cluster_dir / "infographic_artifact_id.txt").write_text(infographic_id + "\n")
            result["visuals"]["infographic_artifact_id"] = infographic_id
        except PublishError as exc:
            result["visuals"]["infographic_error"] = str(exc)
    else:
        result["visuals"]["infographic_status"] = "already-exists"

    if force or not (cluster_dir / "mindmap.json").exists():
        try:
            mindmap_id = _create_mindmap(
                notebook_id,
                source_ids,
                f"{meta['theme']} — {meta['sub_slug']} pathway map",
            )
            (cluster_dir / "mindmap_artifact_id.txt").write_text(mindmap_id + "\n")
            result["visuals"]["mindmap_artifact_id"] = mindmap_id
        except PublishError as exc:
            result["visuals"]["mindmap_error"] = str(exc)
    else:
        result["visuals"]["mindmap_status"] = "already-exists"

    # --- Poll + download ---------------------------------------------
    for kind, art_id, fname in (
        ("slide-deck", slides_id, "slides.pdf"),
        ("infographic", infographic_id, "infographic.png"),
        ("mind-map", mindmap_id, "mindmap.json"),
    ):
        if not art_id:
            continue
        try:
            status = _poll_artifact(notebook_id, art_id, max_s=600) if kind != "mind-map" else "completed"
            if status != "completed":
                result["visuals"][f"{kind}_poll"] = status
                continue
            dest = cluster_dir / fname
            _download_artifact(notebook_id, art_id, kind, dest)
            result["visuals"][f"{kind}_path"] = str(dest.relative_to(PROJECT_ROOT))
        except PublishError as exc:
            result["visuals"][f"{kind}_download_error"] = str(exc)

    # --- Vault -------------------------------------------------------
    vault_entry_dir = (
        VAULT_PATH / "gonzalo-book" / "deep-dive" / meta["theme"] / meta["cluster_name"]
    )
    n_sources = result.get("source_count", 0)
    try:
        if force or not (vault_entry_dir / "theory.md").exists() or not (vault_entry_dir / "application.md").exists():
            theory_md = _theory_vault(
                deep_dive_md=payload["deep_dive_md"],
                theme=meta["theme"],
                sub_angle=meta["sub_slug"],
                sub_slug=meta["sub_slug"],
                today=meta["today"],
                n_sources=n_sources or 1,
            )
            application_vault_md = _application_vault(
                application_md=payload["application_md"],
                theme=meta["theme"],
                sub_angle=meta["sub_slug"],
                sub_slug=meta["sub_slug"],
                today=meta["today"],
            )
            theory_path, app_path = _write_vault_entries(
                theme=meta["theme"],
                sub_slug=meta["sub_slug"],
                today=meta["today"],
                theory_md=theory_md,
                application_vault_md=application_vault_md,
            )
            _commit_vault(
                [theory_path, app_path],
                message=f"deep-dive: {meta['today']} {meta['theme']} / {meta['sub_slug']} (retrofit theory + application)",
            )
            result["vault"]["theory_path"] = str(theory_path.relative_to(PROJECT_ROOT)) if theory_path.is_relative_to(PROJECT_ROOT) else str(theory_path)
            result["vault"]["application_path"] = str(app_path.relative_to(PROJECT_ROOT)) if app_path.is_relative_to(PROJECT_ROOT) else str(app_path)
        else:
            result["vault"]["status"] = "already-exists"
    except Exception as exc:  # noqa: BLE001 — never fail batch on one cluster
        result["vault"]["error"] = f"{type(exc).__name__}: {exc}"
        result["vault"]["traceback"] = traceback.format_exc()[-800:]

    result["status"] = "ok"
    return result


def _find_all_clusters() -> List[Path]:
    out: List[Path] = []
    if not BRIEFS_ROOT.exists():
        return out
    for theme_dir in sorted(BRIEFS_ROOT.iterdir()):
        if not theme_dir.is_dir():
            continue
        for cluster in sorted(theme_dir.iterdir()):
            if not cluster.is_dir():
                continue
            meta = _parse_cluster_meta(cluster)
            if meta is None:
                continue
            out.append(cluster)
    return out


def _find_today_clusters(today: str) -> List[Path]:
    return [c for c in _find_all_clusters() if c.name.startswith(f"{today}--")]


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--today", action="store_true", help="Retrofit only today's clusters.")
    group.add_argument("--all", action="store_true", help="Retrofit every cluster under briefs/.")
    group.add_argument("--cluster", type=Path, help="Retrofit a single cluster directory.")
    parser.add_argument("--date", default=None, help="Override 'today' (YYYY-MM-DD) for --today.")
    parser.add_argument("--force", action="store_true", help="Re-run even if outputs exist.")
    parser.add_argument("--dry-run", action="store_true", help="Print plan only.")
    parser.add_argument("--telegram", action="store_true", help="Post a summary to Telegram at end.")
    args = parser.parse_args(argv)

    if args.today:
        date = args.date or time.strftime("%Y-%m-%d")
        clusters = _find_today_clusters(date)
        scope_label = f"today ({date})"
    elif args.all:
        clusters = _find_all_clusters()
        scope_label = "all"
    else:
        target = args.cluster
        if not target.is_absolute():
            target = (PROJECT_ROOT / target).resolve()
        if not target.exists():
            print(f"ERROR: cluster path not found: {target}", file=sys.stderr)
            return 2
        clusters = [target]
        scope_label = str(target.relative_to(PROJECT_ROOT))

    print(f"[retrofit] scope={scope_label} clusters={len(clusters)} force={args.force} dry_run={args.dry_run}")
    for c in clusters:
        print(f"  - {c.relative_to(PROJECT_ROOT)}")
    print()

    results: List[Dict[str, Any]] = []
    for i, cluster_dir in enumerate(clusters, start=1):
        print(f"[{i}/{len(clusters)}] {cluster_dir.relative_to(PROJECT_ROOT)}")
        r = _retrofit_one(cluster_dir, force=args.force, dry_run=args.dry_run)
        print(f"  → status={r['status']}")
        if r.get("error"):
            print(f"    error: {r['error']}")
        if r.get("visuals"):
            for k, v in r["visuals"].items():
                print(f"    {k}: {v}")
        if r.get("vault"):
            for k, v in r["vault"].items():
                if k == "traceback":
                    continue
                print(f"    vault.{k}: {v}")
        results.append(r)

    # --- Summary -----------------------------------------------------
    statuses: Dict[str, int] = {}
    for r in results:
        statuses[r["status"]] = statuses.get(r["status"], 0) + 1

    print("\n--- SUMMARY ---")
    for status, count in sorted(statuses.items()):
        print(f"  {status}: {count}")

    if args.telegram and not args.dry_run:
        lines = [f"🛠 retrofit run — scope={scope_label}, clusters={len(clusters)}"]
        for status, count in sorted(statuses.items()):
            lines.append(f"  {status}: {count}")
        ok_results = [r for r in results if r.get("status") == "ok"]
        if ok_results:
            lines.append("")
            lines.append("Retrofitted clusters:")
            for r in ok_results[:10]:
                lines.append(f"  • {r['cluster']}")
            if len(ok_results) > 10:
                lines.append(f"  • … and {len(ok_results) - 10} more")
        try:
            telegram_send("\n".join(lines))
        except Exception as exc:  # noqa: BLE001
            print(f"[telegram] send failed: {exc}", file=sys.stderr)

    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""PoC: generate a memory-aware REACT audio from hand-authored sources.

Throwaway experiment harness for
docs/superpowers/specs/2026-06-03-audio-practice-feedback-loop-design.md (§6).

Reuses the existing NotebookLM publisher primitives; assembles an arbitrary
source set + a hand-written focus prompt, triggers ONE audio, polls, prints the
notebook URL. NOT production: no state writes, no _memory updates. Proves the bet.

Run (from repo root):
  PYTHONPATH=. python pipeline/scripts/poc_react_audio.py \
    --theme body-literacy \
    --source "response=briefs/poc-react/response.md" \
    --source "coverage=briefs/poc-react/coverage.md" \
    --source "memory-brief=briefs/poc-react/memory-brief.md" \
    --source "vault-entry=obsidian-vault/gonzalo-book/entries/2026-04-13-passion-as-high-performance.md" \
    --focus-file briefs/poc-react/focus-prompt.md
Add --dry-run to print the assembled plan without calling nlm.
"""
from __future__ import annotations

import argparse
from pathlib import Path
from typing import List, Tuple

from pipeline.summarize_daily.notebooklm_publisher import (
    NLM_PROFILE,
    _add_source,
    _fetch_audio_url,
    _poll_artifact,
    _read_or_create_notebook,
    _trigger_audio,
)


def parse_sources(specs: List[str]) -> List[Tuple[str, Path]]:
    """Parse repeated 'TITLE=PATH' specs into (title, Path) pairs.

    Raises ValueError if a spec has no '=', FileNotFoundError if PATH missing.
    """
    pairs: List[Tuple[str, Path]] = []
    for spec in specs:
        title, sep, path = spec.partition("=")
        if not sep or not path:
            raise ValueError(f"--source must be TITLE=PATH, got: {spec!r}")
        p = Path(path)
        if not p.exists():
            raise FileNotFoundError(f"source file not found: {p}")
        pairs.append((title, p))
    return pairs


def main() -> None:
    ap = argparse.ArgumentParser(description="PoC react-audio generator")
    ap.add_argument("--theme", required=True,
                    help="theme whose notebook to reuse (briefs/<theme>/.notebooklm-id)")
    ap.add_argument("--source", action="append", required=True, metavar="TITLE=PATH",
                    help="repeatable; e.g. --source 'response=briefs/poc-react/response.md'")
    ap.add_argument("--focus-file", required=True, type=Path,
                    help="path to the focus-prompt markdown")
    ap.add_argument("--length", default="long", choices=["short", "default", "long"],
                    help="audio length (default: long)")
    ap.add_argument("--dry-run", action="store_true", help="print plan without calling nlm")
    args = ap.parse_args()

    pairs = parse_sources(args.source)
    focus = args.focus_file.read_text().strip()

    print(f"profile : {NLM_PROFILE}")
    print(f"theme   : {args.theme}")
    print(f"length  : {args.length}")
    print("sources :")
    for title, p in pairs:
        print(f"  - {title}: {p}")
    print(f"focus   : {len(focus)} chars")

    if args.dry_run:
        print("\n[dry-run] not calling nlm.\n--- focus prompt ---")
        print(focus)
        return

    nb_id = _read_or_create_notebook(args.theme)
    print(f"notebook: {nb_id}")
    source_ids: List[str] = []
    for title, p in pairs:
        sid = _add_source(nb_id, p, f"poc-react: {title}")
        print(f"  added {title} -> {sid}")
        source_ids.append(sid)

    artifact_id = _trigger_audio(nb_id, source_ids, focus, length=args.length, fmt="deep_dive")
    url = f"https://notebooklm.google.com/notebook/{nb_id}"
    print(f"artifact: {artifact_id}")
    print(f"notebook url: {url}")

    status = _poll_artifact(nb_id, artifact_id)
    print(f"status  : {status}")
    if status == "completed":
        try:
            print(f"audio   : {_fetch_audio_url(nb_id, artifact_id)}")
        except Exception as exc:  # noqa: BLE001 - non-fatal in a PoC
            print(f"audio url fetch failed (non-fatal): {exc}")


if __name__ == "__main__":
    main()

"""python -m pipeline.publish --profile P --run-dir RD --target T (spec 2026-11-03-console-operability §1.3).

Loads the profile env file itself: the console that spawns this never loads secrets. Exit 0 only when published.
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import Optional, Sequence

from dotenv import load_dotenv

from pipeline.publish import package, run_target

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _load_profile_env(profile: str) -> None:
    # Mirrors pipeline.run: pipeline.runtime reads VAULT_PATH at import, so it cannot be imported before this.
    state = os.environ.get("PAINFORWISDOM_STATE_ROOT", "").strip()
    root = Path(state).expanduser().resolve() if state else PROJECT_ROOT
    load_dotenv(root / (".env.sandbox" if profile == "sandbox" else ".env"))
    os.environ["PIPELINE_PROFILE"] = profile


def main(argv: Optional[Sequence[str]] = None) -> int:
    p = argparse.ArgumentParser(prog="python -m pipeline.publish")
    p.add_argument("--profile", required=True, choices=("sandbox", "prod"))
    p.add_argument("--run-dir", required=True, type=Path)
    p.add_argument("--target", required=True, choices=sorted(package.NODE_DIRS))
    args = p.parse_args(argv)
    _load_profile_env(args.profile)
    done = run_target(args.run_dir.resolve(), args.target)
    print(f"[publish] {args.target}: {done['status']} {(done['result'] or {}).get('url') or done['error'] or ''}")
    return 0 if done["status"] == "published" else 1


if __name__ == "__main__":
    sys.exit(main())

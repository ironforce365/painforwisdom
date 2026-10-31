"""C5 settings: CLI flags override PIPELINE_UI_* env (spec 2026-10-31-pipeline-console-ui, Contracts C5)."""
from __future__ import annotations

import argparse
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Optional, Sequence

from pipeline import runtime

PROFILES = ("sandbox", "prod")
RUNNERS = ("real", "fake")


@dataclass(frozen=True)
class Settings:
    processed_root: Path
    inbox: Path
    runner: str = "real"
    notion_fixtures: Optional[Path] = None
    profile: str = "sandbox"
    host: str = "127.0.0.1"
    port: int = 8820

    @classmethod
    def from_env_and_args(cls, argv: Optional[Sequence[str]] = None,
                          env: Optional[Mapping[str, str]] = None) -> "Settings":
        env = os.environ if env is None else env
        p = argparse.ArgumentParser(prog="python -m pipeline.ui")
        p.add_argument("--port", type=int)
        p.add_argument("--processed-root", type=Path)
        p.add_argument("--inbox", type=Path)
        p.add_argument("--profile", choices=PROFILES)
        args = p.parse_args(list(argv) if argv is not None else None)
        profile = args.profile or env.get("PIPELINE_UI_PROFILE") or "sandbox"
        runner = env.get("PIPELINE_UI_RUNNER") or "real"
        if profile not in PROFILES:
            raise ValueError(f"PIPELINE_UI_PROFILE must be one of {PROFILES}, got {profile!r}")
        if runner not in RUNNERS:
            raise ValueError(f"PIPELINE_UI_RUNNER must be one of {RUNNERS}, got {runner!r}")
        fixtures = env.get("PIPELINE_UI_NOTION_FIXTURES")
        return cls(
            processed_root=args.processed_root or Path(env.get("PIPELINE_UI_PROCESSED_ROOT") or runtime.PROCESSED_ROOT),
            inbox=args.inbox or Path(env.get("PIPELINE_UI_INBOX") or runtime.STATE_ROOT / "inbox"),
            runner=runner,
            notion_fixtures=Path(fixtures) if fixtures else None,
            profile=profile,
            port=args.port or int(env.get("PIPELINE_UI_PORT") or 8820),
        )

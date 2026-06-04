"""Orchestrate: loop profiles → simulate → score → emit JSONL summary."""
from __future__ import annotations
import json
import os
import sys
from pathlib import Path

from eval.simulated_athlete.simulate import simulate
from eval.judge import score_turn

PROFILES_DIR = Path(__file__).parent / "profiles"


def main() -> int:
    coach_url = os.environ.get("COACH_AGENT_URL", "http://localhost:8800")
    out = Path(os.environ.get("COACH_EVAL_OUT", "/eval-runs/nightly.jsonl"))
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w") as f:
        for profile_path in sorted(PROFILES_DIR.glob("*.yaml")):
            transcript = simulate(profile_path, coach_url)
            for i in range(1, len(transcript), 2):
                user_msg = transcript[i - 1]["content"]
                coach_msg = transcript[i]["content"]
                scores = score_turn(user_text=user_msg, coach_reply=coach_msg, retrieved=[])
                f.write(json.dumps({
                    "profile": profile_path.stem, "turn": i // 2 + 1,
                    "scores": scores,
                }) + "\n")
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""Run the single-turn eval set against a running coach service. Outputs JSONL."""
from __future__ import annotations
import json
import os
import sys
from pathlib import Path
import yaml
import httpx

from eval.judge import score_turn

EVAL_SET = Path(__file__).parent / "eval_set.yaml"


def main() -> int:
    coach_url = os.environ.get("COACH_AGENT_URL", "http://localhost:8800")
    out = Path(os.environ.get("COACH_EVAL_OUT", "/eval-runs/single-turn.jsonl"))
    out.parent.mkdir(parents=True, exist_ok=True)
    items = yaml.safe_load(EVAL_SET.read_text())["turns"]
    with out.open("w") as f:
        for item in items:
            r = httpx.post(f"{coach_url}/turn", json={"user_id": "eval", "text": item["user"]}, timeout=120)
            r.raise_for_status()
            reply = r.json()["reply"]
            scores = score_turn(user_text=item["user"], coach_reply=reply, retrieved=[])
            f.write(json.dumps({"id": item["id"], "reply": reply, "scores": scores}) + "\n")
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

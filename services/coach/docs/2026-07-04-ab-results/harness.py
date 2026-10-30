"""Model x effort A/B for the coach: quality (rubric judge) + latency.

Generates replies to the single-turn eval set with the coach system prompt via
`claude -p` (subscription), across model/effort configs, then scores each with
the existing eval.judge rubric scorer. Writes JSONL per config + a summary.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import yaml

COACH = Path("/home/gonzalo/workspace/painforwisdom/painforwisdom/.claude/worktrees/virtual-coach-spec/services/coach")
sys.path.insert(0, str(COACH))
OUT_DIR = Path("/home/gonzalo/.claude/jobs/4b96cef6/tmp/ab_results")
OUT_DIR.mkdir(parents=True, exist_ok=True)

SYSTEM = (COACH / "coach_prompt.md").read_text()
ITEMS = yaml.safe_load((COACH / "eval/single_turn/eval_set.yaml").read_text())["turns"]

CONFIGS = {
    "s46-def": ("claude-sonnet-4-6", None),
    "s5-def": ("claude-sonnet-5", None),
    "s5-med": ("claude-sonnet-5", "medium"),
    "o48-def": ("claude-opus-4-8", None),
    "o48-med": ("claude-opus-4-8", "medium"),
}


def gen(model: str, effort: str | None, user_text: str) -> dict:
    cmd = ["claude", "-p", user_text, "--system-prompt", SYSTEM,
           "--output-format", "json", "--model", model]
    if effort:
        cmd += ["--effort", effort]
    t0 = time.monotonic()
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=240,
                           env={**os.environ, "CLAUDE_CODE_MAX_OUTPUT_TOKENS": "4096"})
    except subprocess.TimeoutExpired:
        return {"error": "timeout", "wall_s": round(time.monotonic() - t0, 1)}
    wall_s = time.monotonic() - t0
    if p.returncode != 0:
        return {"error": p.stderr[:300] or p.stdout[:300], "wall_s": wall_s}
    d = json.loads(p.stdout)
    return {
        "reply": d.get("result", ""),
        "wall_s": round(wall_s, 1),
        "api_ms": d.get("duration_api_ms"),
        "cli_ms": d.get("duration_ms"),
    }


def run_config(name: str) -> None:
    model, effort = CONFIGS[name]
    out = OUT_DIR / f"{name}.jsonl"
    if out.exists() and len(out.read_text().splitlines()) == len(ITEMS):
        print(f"[{name}] cached, skipping")
        return
    from eval.judge import score_turn
    rows = []
    for item in ITEMS:
        g = gen(model, effort, item["user"])
        row = {"id": item["id"], "config": name, **g}
        if "reply" in g and g["reply"]:
            row["scores"] = score_turn(user_text=item["user"], coach_reply=g["reply"], retrieved=[])
        rows.append(row)
        print(f"[{name}] {item['id']}: wall={g.get('wall_s')}s api={g.get('api_ms')}ms "
              f"scores={ {k: v for k, v in row.get('scores', {}).items() if k != 'reasoning'} }")
    out.write_text("\n".join(json.dumps(r) for r in rows) + "\n")


def main() -> None:
    with ThreadPoolExecutor(max_workers=3) as ex:
        list(ex.map(run_config, CONFIGS))
    # summary
    dims = ["frontal", "no_citing", "probing", "brevity", "grounding", "voice"]
    print("\n=== SUMMARY (mean over eval set) ===")
    print(f"{'config':10} {'wall_s':>7} {'api_s':>6} " + " ".join(f"{d[:7]:>8}" for d in dims) + f" {'total':>6}")
    for name in CONFIGS:
        rows = [json.loads(x) for x in (OUT_DIR / f"{name}.jsonl").read_text().splitlines()]
        ok = [r for r in rows if isinstance(r.get("scores"), dict) and "frontal" in r["scores"]]
        n = len(ok) or 1
        wall = sum(r["wall_s"] for r in rows if r.get("wall_s")) / max(1, len([r for r in rows if r.get("wall_s")]))
        api = sum(r["api_ms"] or 0 for r in rows if r.get("api_ms")) / 1000 / max(1, len([r for r in rows if r.get("api_ms")]))
        means = {d: sum(r["scores"][d] for r in ok) / n for d in dims}
        total = sum(means.values())
        print(f"{name:10} {wall:7.1f} {api:6.1f} " + " ".join(f"{means[d]:8.2f}" for d in dims) + f" {total:6.2f}  (n={len(ok)})")


if __name__ == "__main__":
    main()

"""PoC entry point: video → transcript → coaching extraction → tweet refine → Telegram."""
from __future__ import annotations

import argparse
import time
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(PROJECT_ROOT / ".env")

from poc.graph import append_metric, build_graph  # noqa: E402

POC_TARGET_SECONDS = 300  # < 5 min wall-clock for the 4-stage PoC


def main() -> None:
    ap = argparse.ArgumentParser(description="PoC: LangGraph + Anthropic OAuth content pipeline")
    ap.add_argument("--video", required=True, help="Path to video file (.mp4)")
    args = ap.parse_args()

    video = str(Path(args.video).resolve())
    if not Path(video).is_file():
        raise SystemExit(f"video not found: {video}")

    run_id = time.strftime("%Y-%m-%dT%H%M%S")
    print(f"[run] {run_id} video={video}")
    t_start = time.time()

    graph = build_graph()
    config = {"configurable": {"thread_id": run_id}}
    final_state = graph.invoke({"video_path": video, "metrics": []}, config=config)

    total = time.time() - t_start
    summary = append_metric(
        "summary",
        run_id=run_id,
        total_duration_s=round(total, 2),
        target_s=POC_TARGET_SECONDS,
        target_met=total < POC_TARGET_SECONDS,
        core_insight=(final_state.get("core_insight", "") or "")[:200],
    )

    print()
    print(f"[run] DONE total={total:.1f}s target={POC_TARGET_SECONDS}s target_met={summary['target_met']}")
    print("[run] tweet:")
    print("-" * 60)
    print(final_state.get("tweet", ""))
    print()
    print("[run] full extraction_report:")
    print("=" * 60)
    print(final_state.get("extraction_report", ""))


if __name__ == "__main__":
    main()

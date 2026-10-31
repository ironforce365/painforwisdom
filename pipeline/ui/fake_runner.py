"""Deterministic fake pipeline run: real topology, no LLM/Notion/Whisper (spec 2026-10-31-pipeline-console-ui §5.3).

python -m pipeline.ui.fake_runner --run-id ID --processed-root DIR --input PATH --scenario ok|hitl|fail --step-ms N
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import sys
import tempfile
import threading
import time
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from pathlib import Path
from typing import Optional, Sequence

from pipeline.ui.launcher import input_kind
from pipeline.ui.mapping import NODE_TO_STAGE

HITL_STEPS = 3
FAIL_NODE = "research"
_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg==")

# node: (state key, agent dir, file name, content)
ARTIFACTS: dict[str, tuple[str, str, str, bytes]] = {
    "transcribe": ("transcript_path", "transcribe", "transcript.txt", b"fake transcript\n"),
    "extract": ("extraction_report_path", "coaching-thought-extractor", "extraction_report.md",
                b"# Coaching thought\n\nFake extraction.\n"),
    "kb_curator": ("curator_summary_path", "kb-curator", "curator_summary.md", b"# Curator\n\nFake entry.\n"),
    "writer": ("blog_post_path", "painforwisdom-writer", "blog_post.md", b"# Fake post\n\nBody.\n"),
    "research": ("research_csv_path", "research-curator", "research_report.csv",
                 b"title,type,url\nFake book,book,https://example.org/1\n"),
    "notion_blog": ("notion_blog_summary_path", "notion-blog-post-logger", "notion_blog_summary.md",
                    b"page fx-blog-1\n"),
    "notion_research": ("notion_research_summary_path", "notion-research-logger", "notion_summary.md",
                        b"pages fx-res-1 fx-res-2\n"),
    "extract_image": ("featured_image_path", "wordpress-draft", "featured.png", _PNG),
    "youtube_upload": ("youtube_metadata_path", "youtube-upload", "metadata.json", b'{"title": "fake"}\n'),
    "wordpress_draft": ("wordpress_bundle_path", "wordpress-draft", "bundle.json", b'{"status": "draft"}\n'),
    "summarizer": ("pipeline_summary_path", "pipeline-summary", "telegram.txt", b"PASS\n"),
}

# Plausible per-stage telemetry; page ids match tests/fixtures/ui/notion (C6).
_LLM = {"extract": (2200, 440, 0.012), "kb_curator": (7400, 900, 0.11), "writer": (4260, 657, 0.025),
        "research": (51000, 3300, 0.4)}
_EXTRA = {"notion_blog": {"page_id": "fx-blog-1", "page_url": "https://www.notion.so/fx-blog-1"},
          "notion_research": {"rows_in": 2, "created": 2, "failed": 0, "page_ids": ["fx-res-1", "fx-res-2"]},
          "summarizer": {"verdict": "PASS"}}


class FakeRun:
    def __init__(self, run_id: str, root: Path, input_path: Path, scenario: str, step_s: float):
        self.rd = root / run_id / "langgraph"
        self.rd.mkdir(parents=True, exist_ok=True)
        self.scenario, self.step_s = scenario, step_s
        self.lock = threading.Lock()
        self.attempts: dict[str, int] = {}
        kind = input_kind(input_path.name) or "transcript"
        self.record = {"run_id": run_id, "status": "running", "verdict": None, "pid": os.getpid(),
                       "profile": "sandbox", "input": {"kind": kind, "path": str(input_path)},
                       "started_at": time.time(), "ended_at": None, "hitl_wait_s": 0.0, "error": None}

    def _append(self, name: str, row: dict) -> None:
        # Parallel nodes write from pool threads: one locked whole-line write.
        with self.lock, open(self.rd / name, "a") as f:
            f.write(json.dumps(row) + "\n")

    def event(self, node: str, event: str, **extra) -> None:
        if event == "start":
            self.attempts[node] = self.attempts.get(node, 0) + 1
        self._append("events.jsonl", {"ts": time.time(), "node": node, "event": event,
                                      "attempt": self.attempts[node], **extra})

    def save(self, **changes) -> None:
        with self.lock:
            self.record.update(changes)
            tmp = self.rd / ".run.json.tmp"
            tmp.write_text(json.dumps(self.record, indent=2))
            os.replace(tmp, self.rd / "run.json")

    def _outputs(self, node: str) -> dict[str, str]:
        if node not in ARTIFACTS:
            return {}
        key, sub, name, content = ARTIFACTS[node]
        d = self.rd / sub
        d.mkdir(exist_ok=True)
        (d / name).write_bytes(content)
        return {key: str(d / name)}

    def _metric(self, node: str, duration_s: float) -> None:
        stage = NODE_TO_STAGE.get(node)
        if stage is None:
            return
        row: dict = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "stage": stage, "duration_s": round(duration_s, 3)}
        if node in _LLM:
            inp, out, cost = _LLM[node]
            row.update(model="fake-model", input_tokens=inp, output_tokens=out, cache_read_tokens=inp // 2,
                       cache_creation_tokens=inp // 4, cost_usd=cost)
        row.update(_EXTRA.get(node, {}))
        self._append("runs.jsonl", row)

    def run_node(self, node: str) -> None:
        t0 = time.time()
        self.event(node, "start")
        time.sleep(self.step_s)
        if node == "kb_curator" and self.scenario == "hitl":
            self.event(node, "paused")
            self.save(status="waiting_hitl")
            t_wait = time.time()
            time.sleep(HITL_STEPS * self.step_s)
            self.save(status="running", hitl_wait_s=round(time.time() - t_wait, 3))
            self.event(node, "start")  # interrupt() re-runs the node from its top
            time.sleep(self.step_s)
        if node == FAIL_NODE and self.scenario == "fail":
            msg = f"RuntimeError: fake failure at {node}"
            self.event(node, "error", error=msg)
            raise RuntimeError(msg)
        duration = round(time.time() - t0, 3)
        outputs = self._outputs(node)
        self._metric(node, duration)
        self.event(node, "end", duration_s=duration, noop=False, outputs=outputs)


def topology(kind: str) -> dict[str, set[str]]:
    """node: predecessors, from the real graph. A tmp checkpoint keeps the real checkpoints.db untouched."""
    from pipeline import graph

    real_db = graph.CHECKPOINT_DB
    with tempfile.TemporaryDirectory() as tmp:
        graph.CHECKPOINT_DB = Path(tmp) / "ck.db"
        try:
            compiled, saver = graph.build_graph(start_at="transcribe" if kind == "video" else "extract")
            saver.conn.close()
        finally:
            graph.CHECKPOINT_DB = real_db
    g = compiled.get_graph()
    preds: dict[str, set[str]] = {}
    frontier = ["__start__"]
    while frontier:  # only nodes reachable from START: a transcript run never fires transcribe
        src = frontier.pop()
        for e in g.edges:
            if e.source != src or e.target.startswith("__"):
                continue
            if e.target not in preds:
                preds[e.target] = set()
                frontier.append(e.target)
            if not src.startswith("__"):
                preds[e.target].add(src)
    return preds


def execute(run: FakeRun, preds: dict[str, set[str]]) -> Optional[str]:
    """Start each node once all its predecessors are done; on a failure, start nothing new. Returns the error."""
    done: set[str] = set()
    started: set[str] = set()
    error: Optional[str] = None
    with ThreadPoolExecutor(max_workers=len(preds)) as pool:
        running: dict[Future, str] = {}
        while True:
            if error is None:
                for n in sorted(preds):
                    if n not in started and preds[n] <= done:
                        started.add(n)
                        running[pool.submit(run.run_node, n)] = n
            if not running:
                return error
            finished, _ = wait(running, return_when=FIRST_COMPLETED)
            for fut in finished:
                node = running.pop(fut)
                exc = fut.exception()
                if exc is None:
                    done.add(node)
                elif error is None:
                    error = str(exc)


def main(argv: Optional[Sequence[str]] = None) -> int:
    p = argparse.ArgumentParser(prog="python -m pipeline.ui.fake_runner")
    p.add_argument("--run-id", required=True)
    p.add_argument("--processed-root", required=True, type=Path)
    p.add_argument("--input", required=True, type=Path)
    p.add_argument("--scenario", choices=("ok", "hitl", "fail"), default="ok")
    p.add_argument("--step-ms", type=int, default=200)
    args = p.parse_args(argv)
    run = FakeRun(args.run_id, args.processed_root, args.input, args.scenario, args.step_ms / 1000)
    run.save()
    error = execute(run, topology(run.record["input"]["kind"]))
    if error:
        run.save(status="failed", error=error, ended_at=time.time())
        return 2
    run.save(status="done", verdict="PASS", ended_at=time.time())
    return 0


if __name__ == "__main__":
    sys.exit(main())

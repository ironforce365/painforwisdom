"""Deterministic fake pipeline run: real topology, no LLM/Notion/Whisper (spec 2026-10-31-pipeline-console-ui §5.3).

python -m pipeline.ui.fake_runner --run-id ID --processed-root DIR --input PATH --scenario S --step-ms N --wait-s N
python -m pipeline.ui.fake_runner retry --run-id ID --processed-root DIR --step-ms N
python -m pipeline.ui.fake_runner publish --run-dir RD --target T --step-ms N

Scenarios that wait do so on C11 (spec 2026-11-05-console-end-to-end section 3), with no Telegram, for at most --wait-s.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import shutil
import sys
import tempfile
import threading
import time
import uuid
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from pathlib import Path
from typing import Iterable, Optional, Sequence

from pipeline import hitl
from pipeline.publish import package as publish_package
from pipeline.publish.specs import WORDPRESS
from pipeline.ui.launcher import input_kind
from pipeline.ui.mapping import NODE_TO_STAGE

SCENARIOS = ("ok", "hitl", "fail", "error", "error_twice", "non_recoverable", "curator")
HITL_NODE = "kb_curator"
LLM_CALLS = 2
FAIL_NODE = "research"
ASK_FAILURES = {"error": 1, "error_twice": 2, "non_recoverable": 1}  # failures that ask for retry or abort
NON_RECOVERABLE = "auth credentials rejected after in-call refresh — retry would re-fail"
POLL_S = 0.05
GRAPH_SOURCE = Path(__file__).resolve().parents[1] / "graph.py"
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
    "youtube_metadata": ("youtube_metadata_path", "youtube-upload", "metadata.json", b'{"title": "fake"}\n'),
    "wordpress_draft": ("wordpress_bundle_path", "wordpress-draft", "bundle.json", b'{"status": "draft"}\n'),
    "summarizer": ("pipeline_summary_path", "pipeline-summary", "telegram.txt", b"PASS\n"),
}

# Plausible per-stage telemetry; page ids match tests/fixtures/ui/notion (C6).
_LLM = {"extract": (2200, 440, 0.012), "kb_curator": (7400, 900, 0.11), "writer": (4260, 657, 0.025),
        "research": (51000, 3300, 0.4), "youtube_metadata": (1200, 100, 0.005)}
_EXTRA = {"notion_blog": {"page_id": "fx-blog-1", "page_url": "https://www.notion.so/fx-blog-1"},
          "notion_research": {"rows_in": 2, "created": 2, "failed": 0, "page_ids": ["fx-res-1", "fx-res-2"]},
          "youtube_upload": {"skipped": True, "reason": "off"}, "wordpress_draft": {"skipped": True, "reason": "manual"},
          "summarizer": {"verdict": "PASS"}}


def _audit(prefix: str, suffix: str = "") -> dict:
    check = json.dumps({"name": "fake check", "ok": True, "detail": "", "severity": "core"})
    return {f"{prefix}_findings{suffix}": [check], f"{prefix}_verdict{suffix}": "PASS"}


# C1 end.result (spec 2026-11-03-console-operability §1.2).
_RESULTS = {"pre_validator": _audit("pre"), "bv_research": _audit("branch", "_research"),
            "bv_wordpress": _audit("branch", "_wordpress"), "bv_youtube": _audit("branch", "_youtube"),
            "youtube_upload": {"youtube_skipped": True, "youtube_skip_reason": "off"},
            "wordpress_draft": {"wordpress_skipped": True, "wordpress_skip_reason": "manual"},
            "writer": {"blog_post_title": "Fake post"}, "summarizer": {"validator_verdict": "PASS"}}


class FakeRun:
    def __init__(self, run_id: str, root: Path, input_path: Path, scenario: str, step_s: float, wait_s: float = 120,
                 auto_approve: bool = False):
        self.rd = root / run_id / "langgraph"
        self.rd.mkdir(parents=True, exist_ok=True)
        self.scenario, self.step_s, self.wait_s, self.auto_approve = scenario, step_s, wait_s, auto_approve
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

    def _work(self, node: str) -> None:
        """One step of node time. An _LLM node spends it on C8 start/end pairs, so live numbers grow."""
        if node not in _LLM:
            time.sleep(self.step_s)
            return
        inp, out, cost = _LLM[node]
        base = {"node": node, "attempt": self.attempts[node], "model": "fake-model"}
        for _ in range(LLM_CALLS):
            call_id = uuid.uuid4().hex
            self._append("usage.jsonl", {"ts": time.time(), "event": "start", "call_id": call_id, **base})
            time.sleep(self.step_s / LLM_CALLS)
            self._append("usage.jsonl", {
                "ts": time.time(), "event": "end", "call_id": call_id, **base,
                "input_tokens": inp // LLM_CALLS, "output_tokens": out // LLM_CALLS,
                "cache_read_tokens": inp // 2 // LLM_CALLS, "cache_creation_tokens": inp // 4 // LLM_CALLS,
                "cost_usd": cost / LLM_CALLS, "call_s": self.step_s / LLM_CALLS})

    def _package(self, node: str) -> None:
        if node != "wordpress_draft":
            return
        featured = self.rd / "wordpress-draft" / "featured.png"
        fields = {"title": "Fake post", "body": "# Fake post\n\nBody.\n", "excerpt": "Body.",
                  "tags": ["painforwisdom"], "categories": [], "status": "draft", "youtube_url": ""}
        publish_package.write(self.rd, publish_package.new_package(
            "wordpress", "manual", fields, WORDPRESS, readonly={"date_mmddyy": "10/30/26"},
            assets=[{"role": "featured_image", "path": str(featured)}]))

    def _ask(self, status: str, request: dict) -> Optional[str]:
        if self.auto_approve:
            return "yes" if request["kind"] == "approval" else "abort"
        t_wait = time.time()
        self.save(status=status)
        reply = hitl.ask(self.rd, request, self.wait_s, max_reminders=1, telegram=False, poll_s=POLL_S)
        self.save(status="running", hitl_wait_s=round(self.record["hitl_wait_s"] + time.time() - t_wait, 3))
        return reply

    def _approval(self, node: str) -> None:
        """Like kb_curator: `yes` and `no` settle it, free text re-plans and may ask again."""
        curator = self.scenario == "curator"
        title = "Curator: decision needed" if curator else "New theme: approval needed"
        prompt = ("⚠️ CURATOR — unknown action `ALREADY_PROCESSED`\nReason: this video already ran."
                  if curator else "⚠️ NEW THEME — approval required\nProposed: fake-theme\nReason: fake.")
        while True:
            self.event(node, "paused")
            reply = self._ask("waiting_hitl", hitl.approval_request(node, title, prompt))
            if reply is None:
                msg = f"RuntimeError: no answer within {self.wait_s:g} s at {node}"
                self.event(node, "error", error=msg)
                raise RuntimeError(msg)
            self.event(node, "start")  # interrupt() re-runs the node from its top
            self._work(node)
            if reply.lower() in {"yes", "no"}:
                return
            prompt = f"{prompt}\nYour last answer: {reply}"

    def _asks_on_error(self, node: str) -> None:
        """Fail ASK_FAILURES times, asking each time. Anything but `retry` ends the run, as in pipeline.run."""
        non_recoverable = NON_RECOVERABLE if self.scenario == "non_recoverable" else None
        msg = f"RuntimeError: fake failure at {node}"
        for _ in range(ASK_FAILURES[self.scenario]):
            self.event(node, "error", error=msg)
            prompt = f"⚠️ Pipeline error in `{node}`\n\n{msg}\n\nReply: `retry` | `abort`"
            reply = self._ask("waiting_reply", hitl.error_request(node, msg, prompt, non_recoverable))
            if reply is None:
                raise RuntimeError(f"{msg} (no answer within {self.wait_s:g} s)")
            if reply.strip().lower() != "retry" or non_recoverable:
                raise RuntimeError(msg)
            self.event(node, "start")
            self._work(node)

    def run_node(self, node: str) -> None:
        t0 = time.time()
        self.event(node, "start")
        self._work(node)
        if node == HITL_NODE and self.scenario in {"hitl", "curator"}:
            self._approval(node)
        if node == FAIL_NODE and self.scenario in ASK_FAILURES:
            self._asks_on_error(node)
        if node == FAIL_NODE and self.scenario == "fail":
            msg = f"RuntimeError: fake failure at {node}"
            self.event(node, "error", error=msg)
            raise RuntimeError(msg)
        duration = round(time.time() - t0, 3)
        outputs = self._outputs(node)
        self._package(node)
        self._metric(node, duration)
        self.event(node, "end", duration_s=duration, noop=False, outputs=outputs,
                   result=_RESULTS.get(node, {}))


def topology(kind: str) -> dict[str, set[str]]:
    """node: predecessors, kept per version of graph.py: importing the graph costs seconds, on each fake run otherwise."""
    digest = hashlib.sha256(GRAPH_SOURCE.read_bytes()).hexdigest()[:16]
    cache = Path(tempfile.gettempdir()) / f"pfw-fake-topology-{os.getuid()}-{digest}-{kind}.json"
    try:
        return {node: set(preds) for node, preds in json.loads(cache.read_text()).items()}
    except (OSError, ValueError):
        pass
    preds = _build_topology(kind)
    tmp = cache.with_name(f"{cache.name}.{uuid.uuid4().hex}.tmp")
    tmp.write_text(json.dumps({node: sorted(p) for node, p in preds.items()}))
    os.replace(tmp, cache)
    return preds


def _build_topology(kind: str) -> dict[str, set[str]]:
    """From the real graph. A tmp checkpoint keeps the real checkpoints.db untouched."""
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


def execute(run: FakeRun, preds: dict[str, set[str]], already_done: Iterable[str] = ()) -> Optional[str]:
    """Start each node once all its predecessors are done; on a failure, start nothing new. Returns the error."""
    done: set[str] = set(already_done)
    started: set[str] = set(done)
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


def fake_preview(dest: Path, step_s: float) -> int:
    """Stands in for ffmpeg: a real H.264 file, so a browser test proves the copy decodes without ffmpeg installed."""
    time.sleep(step_s)
    shutil.copyfile(Path(__file__).resolve().parents[2] / "tests/fixtures/ui/inbox/PXL_20261030_070000000.mp4", dest)
    return 0


def fake_publish(rd: Path, target: str, step_s: float) -> int:
    """Stands in for `python -m pipeline.publish`: the title FAIL takes the error path. No client, no network."""
    try:
        time.sleep(step_s)
        pkg = publish_package.read(rd, target)
        if pkg["fields"].get("title") == "FAIL":
            publish_package.write(rd, {**pkg, "status": "failed", "error": "fake publish failure"})
            return 1
        result = {"id": f"fake-{rd.parent.name}", "url": f"https://example.invalid/{target}/{rd.parent.name}"}
        publish_package.write(rd, {**pkg, "status": "published", "result": result, "error": None})
        return 0
    finally:
        publish_package.release_lock(rd, target)


def _finish(run: FakeRun, already_done: Iterable[str] = ()) -> int:
    error = execute(run, topology(run.record["input"]["kind"]), already_done)
    if error:
        run.save(status="failed", error=error, ended_at=time.time())
        return 2
    run.save(status="done", verdict="PASS", ended_at=time.time())
    return 0


def fake_retry(run_id: str, root: Path, scenario: str, step_s: float, wait_s: float) -> int:
    """Stands in for `python -m pipeline.retry`: the nodes whose last event is not `end` run again."""
    rd = root / run_id / "langgraph"
    record = json.loads((rd / "run.json").read_text())
    run = FakeRun(run_id, root, Path(record["input"]["path"] or "t.txt"), scenario, step_s, wait_s)
    run.record = {**record, "status": "running", "verdict": None, "pid": os.getpid(), "ended_at": None, "error": None}
    last: dict[str, str] = {}
    for line in (rd / "events.jsonl").read_text().splitlines() if (rd / "events.jsonl").is_file() else ():
        ev = json.loads(line)
        last[ev["node"]] = ev["event"]
        run.attempts[ev["node"]] = max(run.attempts.get(ev["node"], 0), int(ev.get("attempt", 1)))
    run.save()
    return _finish(run, [node for node, event in last.items() if event == "end"])


def main(argv: Optional[Sequence[str]] = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv[:1] == ["retry"]:
        r = argparse.ArgumentParser(prog="python -m pipeline.ui.fake_runner retry")
        r.add_argument("--run-id", required=True)
        r.add_argument("--processed-root", required=True, type=Path)
        r.add_argument("--scenario", choices=SCENARIOS, default="ok")
        r.add_argument("--step-ms", type=int, default=200)
        r.add_argument("--wait-s", type=float, default=120)
        ret = r.parse_args(argv[1:])
        return fake_retry(ret.run_id, ret.processed_root, ret.scenario, ret.step_ms / 1000, ret.wait_s)
    if argv[:1] == ["preview"]:
        v = argparse.ArgumentParser(prog="python -m pipeline.ui.fake_runner preview")
        v.add_argument("--step-ms", type=int, default=200)
        v.add_argument("dest", type=Path)
        prev = v.parse_args(argv[1:])
        return fake_preview(prev.dest, prev.step_ms / 1000)
    if argv[:1] == ["publish"]:
        q = argparse.ArgumentParser(prog="python -m pipeline.ui.fake_runner publish")
        q.add_argument("--run-dir", required=True, type=Path)
        q.add_argument("--target", required=True)
        q.add_argument("--step-ms", type=int, default=200)
        pub = q.parse_args(argv[1:])
        return fake_publish(pub.run_dir, pub.target, pub.step_ms / 1000)
    p = argparse.ArgumentParser(prog="python -m pipeline.ui.fake_runner")
    p.add_argument("--run-id", required=True)
    p.add_argument("--processed-root", required=True, type=Path)
    p.add_argument("--input", required=True, type=Path)
    p.add_argument("--scenario", choices=SCENARIOS, default="ok")
    p.add_argument("--step-ms", type=int, default=200)
    p.add_argument("--wait-s", type=float, default=120)
    p.add_argument("--auto-approve", action="store_true")
    args = p.parse_args(argv)
    run = FakeRun(args.run_id, args.processed_root, args.input, args.scenario, args.step_ms / 1000, args.wait_s,
                  args.auto_approve)
    run.save()
    return _finish(run)


if __name__ == "__main__":
    sys.exit(main())

"""Read runs from processed/ into C3 shapes. Pure functions, no FastAPI (spec 2026-10-31-pipeline-console-ui §3.5)."""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any, Iterable, Optional

from pipeline.ui.mapping import node_for_stage

ACTIVE = {"running", "waiting_hitl", "waiting_reply"}
FINISHED = {"done", "failed", "crashed"}
TOKEN_FIELDS = ("input_tokens", "output_tokens", "cache_read_tokens", "cache_creation_tokens")


def read_jsonl(path: Path) -> list[dict]:
    if not path.is_file():
        return []
    out = []
    for line in path.read_text(errors="replace").splitlines():
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if isinstance(row, dict):
            out.append(row)
    return out


def parse_ts(value: Any) -> Optional[float]:
    """runs.jsonl writes local ISO seconds (runtime.now_iso); events.jsonl writes epoch floats."""
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return time.mktime(time.strptime(str(value), "%Y-%m-%dT%H:%M:%S"))
    except ValueError:
        return None


def pid_alive(pid: Optional[int]) -> bool:
    # os.kill(0, 0) signals our own process group and succeeds, so pid <= 0 is dead by rule.
    if not isinstance(pid, int) or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except PermissionError:
        return True
    except (ProcessLookupError, OverflowError):
        return False
    return True


def run_dir(root: Path, run_id: str) -> Optional[Path]:
    if not run_id or run_id in {".", ".."} or "/" in run_id or "\\" in run_id:
        return None
    rd = root / run_id / "langgraph"
    if (rd / "run.json").is_file() or (rd / "runs.jsonl").is_file():
        return rd
    return None


def _blank_node() -> dict[str, Any]:
    return {"state": "pending", "started_at": None, "ended_at": None, "duration_s": None, "attempts": 0, "error": None}


def node_states(events: Iterable[dict], node_ids: list[str], finished: bool, input_kind: Optional[str]) -> dict:
    nodes = {n: _blank_node() for n in node_ids}
    real_end: set[str] = set()
    for ev in events:
        node = str(ev.get("node"))
        n = nodes.get(node)
        if n is None:
            continue
        kind, ts = ev.get("event"), parse_ts(ev.get("ts"))
        if kind == "start":
            n["attempts"] += 1
            n["started_at"] = n["started_at"] if n["started_at"] is not None else ts
            # A re-fire of a done node is a noop in practice; keep it done (spec §3.5, no flicker).
            if n["state"] != "done":
                n["state"], n["error"] = "running", None
        elif kind == "end":
            noop = bool(ev.get("noop"))
            if not noop or node not in real_end:
                n["duration_s"], n["ended_at"] = ev.get("duration_s"), ts
            if not noop:
                real_end.add(node)
            n["state"], n["error"] = "done", None
        elif kind == "error":
            n["state"], n["error"], n["ended_at"] = "error", ev.get("error"), ts
        elif kind == "paused":
            n["state"] = "paused"
    if finished and input_kind == "transcript" and nodes.get("transcribe", {}).get("state") == "pending":
        nodes["transcribe"]["state"] = "skipped"
    return nodes


def _row_tokens(row: dict) -> dict[str, int]:
    calls = row.get("per_call") if not any(k in row for k in TOKEN_FIELDS) else None
    src = calls if isinstance(calls, list) else [row]
    return {k: sum(int(c.get(k) or 0) for c in src if isinstance(c, dict)) for k in TOKEN_FIELDS}


def _row_cost(row: dict) -> float:
    return float(row.get("cost_usd", row.get("total_cost_usd")) or 0.0)


def telemetry(rows: Iterable[dict], total_s: Optional[float], hitl_wait_s: float) -> dict:
    per_node: dict[str, dict] = {}
    for row in rows:
        node = node_for_stage(str(row.get("stage", "")))
        if node is None:
            continue
        agg = per_node.setdefault(node, {"duration_s": 0.0, **{k: 0 for k in TOKEN_FIELDS}, "cost_usd": 0.0,
                                         "model": None})
        agg["duration_s"] = round(agg["duration_s"] + float(row.get("duration_s") or 0.0), 3)
        for k, v in _row_tokens(row).items():
            agg[k] += v
        agg["cost_usd"] += _row_cost(row)
        agg["model"] = row.get("model") or agg["model"]
    tokens = {short: sum(p[k] for p in per_node.values())
              for short, k in zip(("input", "output", "cache_read", "cache_creation"), TOKEN_FIELDS)}
    return {
        "total_s": total_s,
        "work_s": None if total_s is None else total_s - (hitl_wait_s or 0.0),
        "tokens": tokens,
        "cost_usd": sum(p["cost_usd"] for p in per_node.values()),
        "per_node": per_node,
    }


def _legacy_record(rd: Path, rows: list[dict]) -> dict:
    ts = [t for t in (parse_ts(r.get("ts")) for r in rows) if t is not None]
    verdicts = [r.get("verdict") for r in rows if r.get("stage") in {"summarizer", "validator"} and r.get("verdict")]
    has_transcribe = any(r.get("stage") == "transcribe" for r in rows)
    sources = sorted(p for p in (rd.parent / "source").glob("*") if p.is_file())
    return {
        "status": "done", "verdict": verdicts[-1] if verdicts else None, "pid": None, "profile": None,
        "input": {"kind": "video" if has_transcribe else "transcript",
                  "path": str(sources[0]) if sources else None},
        "started_at": min(ts, default=None), "ended_at": max(ts, default=None), "hitl_wait_s": 0.0, "error": None,
    }


def _legacy_nodes(rows: list[dict], node_ids: list[str]) -> dict:
    nodes = {n: {**_blank_node(), "state": "skipped"} for n in node_ids}
    for row in rows:
        n = nodes.get(node_for_stage(str(row.get("stage", ""))))
        if n is None:
            continue
        ts, dur = parse_ts(row.get("ts")), float(row.get("duration_s") or 0.0)
        n["state"] = "done"
        n["attempts"] += 1
        n["duration_s"] = round((n["duration_s"] or 0.0) + dur, 3)
        if ts is not None:
            n["started_at"] = n["started_at"] if n["started_at"] is not None else ts - dur
            n["ended_at"] = ts
    return nodes


def _record(rd: Path, now: Optional[float]) -> dict:
    """run.json (or the legacy stand-in) with derived status and the end timestamp telemetry uses."""
    events = read_jsonl(rd / "events.jsonl")
    rows = read_jsonl(rd / "runs.jsonl")
    rec: dict[str, Any]
    path = rd / "run.json"
    legacy = not path.is_file()
    if legacy:
        rec = _legacy_record(rd, rows)
    else:
        try:
            rec = json.loads(path.read_text())
        except ValueError:
            rec = {"status": "failed", "error": "run.json unreadable"}
    status = rec.get("status")
    alive = status in ACTIVE and pid_alive(rec.get("pid"))
    if status in ACTIVE and not alive:
        status = "crashed"
    started = rec.get("started_at")
    end = rec.get("ended_at")
    if end is None:
        last_event = max((t for t in (parse_ts(e.get("ts")) for e in events) if t is not None), default=None)
        end = (now if now is not None else time.time()) if alive else last_event
    return {
        "run_id": rd.parent.name, "status": status, "verdict": rec.get("verdict"), "profile": rec.get("profile"),
        "input": rec.get("input") or {"kind": None, "path": None}, "started_at": started,
        "ended_at": rec.get("ended_at"), "hitl_wait_s": float(rec.get("hitl_wait_s") or 0.0),
        "error": rec.get("error"), "total_s": None if started is None or end is None else end - started,
        "legacy": legacy, "events": events, "rows": rows,
    }


def load_run(root: Path, run_id: str, node_ids: list[str], now: Optional[float] = None) -> Optional[dict]:
    rd = run_dir(root, run_id)
    if rd is None:
        return None
    rec = _record(rd, now)
    if rec["legacy"]:
        nodes = _legacy_nodes(rec["rows"], node_ids)
    else:
        nodes = node_states(rec["events"], node_ids, rec["status"] in FINISHED, rec["input"].get("kind"))
    keys = ("run_id", "status", "verdict", "profile", "input", "started_at", "ended_at", "hitl_wait_s", "error")
    return {**{k: rec[k] for k in keys}, "nodes": nodes,
            "telemetry": telemetry(rec["rows"], rec["total_s"], rec["hitl_wait_s"])}


def list_runs(root: Path, now: Optional[float] = None) -> list[dict]:
    if not root.is_dir():
        return []
    out = []
    for child in root.iterdir():
        rd = run_dir(root, child.name)
        if rd is None:
            continue
        rec = _record(rd, now)
        path = rec["input"].get("path")
        out.append({
            "run_id": rec["run_id"], "status": rec["status"], "verdict": rec["verdict"],
            "started_at": rec["started_at"], "ended_at": rec["ended_at"],
            "input": {"kind": rec["input"].get("kind"), "name": Path(path).name if path else None},
            "total_s": rec["total_s"],
        })
    out.sort(key=lambda r: (r["started_at"] or 0.0, r["run_id"]), reverse=True)
    return out

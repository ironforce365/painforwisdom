"""Read runs from processed/ into C3 shapes. Pure functions, no FastAPI (spec 2026-10-31-pipeline-console-ui §3.5)."""
from __future__ import annotations

import json
import os
import time
import uuid
from pathlib import Path
from typing import Any, Iterable, Optional

from pipeline import hitl, pricing, runtime
from pipeline.publish import package as publish_package
from pipeline.ui.mapping import node_for_stage

ACTIVE = {"running", "waiting_hitl", "waiting_reply"}
ENDED_NOT_OK = {"failed", "crashed", "stopped"}
FINISHED = {"done", *ENDED_NOT_OK}
TOKEN_FIELDS = ("input_tokens", "output_tokens", "cache_read_tokens", "cache_creation_tokens")
NOT_FACTS = {"ts", "stage", "per_call", "cost_usd", "total_cost_usd", *TOKEN_FIELDS}


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
    return {"state": "pending", "started_at": None, "ended_at": None, "duration_s": None, "attempts": 0, "error": None,
            "result": {}, "outputs": {}, "facts": {}, "skip_reason": None}


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
                n["result"] = ev.get("result") or {}
                n["outputs"] = ev.get("outputs") or {}
            n["state"], n["error"] = "done", None
        elif kind == "error":
            n["state"], n["error"], n["ended_at"] = "error", ev.get("error"), ts
        elif kind == "paused":
            n["state"] = "paused"
    if finished and input_kind == "transcript" and nodes.get("transcribe", {}).get("state") == "pending":
        nodes["transcribe"]["state"] = "skipped"
    return nodes


def apply_rows(nodes: dict, rows: Iterable[dict]) -> dict:
    """C3 `facts`, `skip_reason` and the `skipped` state from each node's last C4 row (spec 2026-11-03 §3.2)."""
    last = {node_for_stage(str(row.get("stage", ""))): row for row in rows}
    for node, n in nodes.items():
        row = last.get(node, {})
        n["facts"] = {k: v for k, v in row.items() if k not in NOT_FACTS}
        if row.get("skipped") is True:
            n["skip_reason"] = row.get("reason")
            if n["state"] in {"done", "pending"}:  # running, paused and error are live news; the row is not
                n["state"] = "skipped"
    return nodes


def resolve_outputs(nodes: dict, rd: Path) -> dict:
    """Absolute `outputs` (a relative one is relative to the run dir, as in artifacts): the view links to them (Q3341)."""
    for n in nodes.values():
        n["outputs"] = {k: str((rd / str(v)).resolve()) for k, v in n["outputs"].items()}
    return nodes


def _row_tokens(row: dict) -> dict[str, int]:
    calls = row.get("per_call") if not any(k in row for k in TOKEN_FIELDS) else None
    src = calls if isinstance(calls, list) else [row]
    return {k: sum(int(c.get(k) or 0) for c in src if isinstance(c, dict)) for k in TOKEN_FIELDS}


def _row_cost(row: dict) -> float:
    return float(row.get("cost_usd", row.get("total_cost_usd")) or 0.0)


def _row_has_tokens(row: dict) -> bool:
    return any(k in row for k in TOKEN_FIELDS) or isinstance(row.get("per_call"), list)


def _row_calls(row: dict) -> int:
    # Only kb_curator writes llm_calls; every other LLM node makes one call per row (Neo, Q3314).
    return int(row.get("llm_calls", 1 if _row_has_tokens(row) else 0) or 0)


def _blank_telemetry() -> dict[str, Any]:
    # duration_s stays None until a C4 row exists: telemetry.js falls back to the node's elapsed span.
    return {"duration_s": None, **{k: 0 for k in TOKEN_FIELDS}, "cost_usd": 0.0, "cost_estimated": False, "model": None,
            "calls": 0, "in_flight": 0, "live": False}


def _charge(agg: dict, logged: float, model: Optional[str], tokens: dict[str, int]) -> None:
    """The billed cost; under subscription billing (logged 0) the API-equivalent, computed here so older runs get it too."""
    if logged or not any(tokens.values()):
        agg["cost_usd"] += logged
        return
    read, write = tokens["cache_read_tokens"], tokens["cache_creation_tokens"]
    # input_tokens is litellm's prompt_tokens, which already holds both cache counts (pipeline/llm.py).
    est = pricing.cost(model, max(0, tokens["input_tokens"] - read - write), tokens["output_tokens"], read, write)
    if est is None:
        agg["unpriced"] = True
    else:
        agg["cost_usd"] += est
        agg["cost_estimated"] = True


def _apply_usage(per_node: dict[str, dict], final: set[str], usage: Iterable[dict], active: bool) -> None:
    """Merge C8 lines into per_node. A node with a final C4 row keeps its C4 tokens (spec 2026-11-03 §4.3)."""
    by_node: dict[str, list[dict]] = {}
    for line in usage:
        if line.get("node"):
            by_node.setdefault(str(line["node"]), []).append(line)
    for node, lines in by_node.items():
        agg = per_node.setdefault(node, _blank_telemetry())
        ends = [ln for ln in lines if ln.get("event") == "end"]
        closed = {ln.get("call_id") for ln in lines if ln.get("event") in {"end", "error"}}
        started = {ln.get("call_id") for ln in lines if ln.get("event") == "start"}
        agg["in_flight"] = len(started - closed) if active else 0
        agg["calls"] = len(ends) or agg["calls"]
        if node in final:
            continue
        agg["live"] = True
        agg["model"] = lines[-1].get("model") or agg["model"]
        for k in TOKEN_FIELDS:
            agg[k] = sum(int(ln.get(k) or 0) for ln in ends)
        agg["cost_usd"] = 0.0
        for ln in ends:
            _charge(agg, float(ln.get("cost_usd") or 0.0), ln.get("model") or agg["model"],
                    {k: int(ln.get(k) or 0) for k in TOKEN_FIELDS})


def telemetry(rows: Iterable[dict], total_s: Optional[float], hitl_wait_s: float,
              usage: Iterable[dict] = (), active: bool = False) -> dict:
    per_node: dict[str, dict] = {}
    final: set[str] = set()
    usage = list(usage)
    # kb_curator rows carry per_call and no model; the usage lines know it.
    usage_model = {str(ln["node"]): ln["model"] for ln in usage if ln.get("node") and ln.get("model")}
    for row in rows:
        node = node_for_stage(str(row.get("stage", "")))
        if node is None:
            continue
        agg = per_node.setdefault(node, _blank_telemetry())
        agg["duration_s"] = round((agg["duration_s"] or 0.0) + float(row.get("duration_s") or 0.0), 3)
        tokens = _row_tokens(row)
        for k, v in tokens.items():
            agg[k] += v
        agg["model"] = row.get("model") or agg["model"] or usage_model.get(node)
        _charge(agg, _row_cost(row), row.get("model") or usage_model.get(node), tokens)
        agg["calls"] += _row_calls(row)
        if _row_has_tokens(row):
            final.add(node)
    _apply_usage(per_node, final, usage, active)
    tokens = {short: sum(p[k] for p in per_node.values())
              for short, k in zip(("input", "output", "cache_read", "cache_creation"), TOKEN_FIELDS)}
    unpriced = False
    for p in per_node.values():
        if p.pop("unpriced", False) and not p["cost_usd"]:
            p["cost_usd"], unpriced = None, True
    cost = sum(p["cost_usd"] or 0.0 for p in per_node.values())
    return {
        "total_s": total_s,
        "work_s": None if total_s is None else total_s - (hitl_wait_s or 0.0),
        "tokens": tokens,
        # Never a fake 0: None when nothing could be priced. A partly priced run sums what it can.
        "cost_usd": None if unpriced and not cost else cost,
        "cost_estimated": any(p["cost_estimated"] for p in per_node.values()),
        "calls": sum(p["calls"] for p in per_node.values()),
        "in_flight": sum(p["in_flight"] for p in per_node.values()),
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
    rec = {}
    if path.is_file():
        try:
            rec = json.loads(path.read_text())
        except ValueError:
            rec = {"status": "failed", "error": "run.json unreadable"}
    # A legacy run's run.json can only hold the flags the console set on it (`dismissed`).
    legacy = "status" not in rec
    if legacy:
        rec = {**_legacy_record(rd, rows), **rec}
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
        "language": rec.get("language"), "input": rec.get("input") or {"kind": None, "path": None}, "started_at": started,
        "ended_at": rec.get("ended_at"), "hitl_wait_s": float(rec.get("hitl_wait_s") or 0.0),
        "error": rec.get("error"), "total_s": None if started is None or end is None else end - started,
        "legacy": legacy, "events": events, "rows": rows, "dismissed": bool(rec.get("dismissed")), "pid": rec.get("pid"),
        "dry_run": bool(rec.get("dry_run")), "engine": rec.get("engine"),
    }


def update_record(rd: Path, **fields: Any) -> None:
    """Merge `fields` into run.json, replacing the file atomically."""
    path = rd / "run.json"
    try:
        rec = json.loads(path.read_text())
    except (FileNotFoundError, ValueError):
        rec = {}
    tmp = path.with_name(f".run.json.{uuid.uuid4().hex}.tmp")
    tmp.write_text(json.dumps({**rec, **fields}, indent=2))
    os.replace(tmp, path)


def active_run_id(root: Path) -> Optional[str]:
    """The first run with a live runner, reading only run.json files."""
    if not root.is_dir():
        return None
    for child in sorted(root.iterdir()):
        try:
            rec = json.loads((child / "langgraph" / "run.json").read_text())
        except (OSError, ValueError):
            continue
        if rec.get("status") in ACTIVE and pid_alive(rec.get("pid")):
            return child.name
    return None


def input_file(rd: Path, rec: dict) -> Optional[Path]:
    """The run's input as it is on disk now: where it was launched from, else archived under source/."""
    path = rec["input"].get("path")
    archive = rd.parent / "source"
    if path:
        launched = (runtime.PROJECT_ROOT / str(path)).resolve()
        for p in (launched, archive / launched.name):
            if p.is_file():
                return p.resolve()
    return next((p.resolve() for p in sorted(archive.glob("*")) if p.is_file()), None)


def pending_for(rd: Path, status: Optional[str]) -> Optional[dict]:
    return hitl.read_pending(rd) if status in ACTIVE else None


def _failed_node(events: list[dict]) -> tuple[Optional[str], Optional[str]]:
    """(node, error) of the last node whose error no later attempt cleared; else the last one left unfinished."""
    errors: dict[str, Any] = {}
    unfinished: dict[str, bool] = {}
    for ev in events:
        node, kind = ev.get("node"), ev.get("event")
        if kind == "error":
            errors.pop(node, None)
            errors[node] = ev.get("error")
        elif kind in {"start", "end"}:
            errors.pop(node, None)
            unfinished.pop(node, None)
            if kind == "start":
                unfinished[node] = True
    if errors:
        node = list(errors)[-1]
        return node, errors[node]
    return (list(unfinished)[-1], None) if unfinished else (None, None)


_E6 = "This run waits for a Telegram reply under an older release that cannot be answered from the console."
_FAILURE_TEXT = {"failed": "The run failed without recording a reason.",
                 "crashed": "The runner process died without recording a reason.",
                 "stopped": "The run was stopped."}
_FAILURE_HINT = {"failed": "Retry from the last checkpoint, or restart from scratch.",
                 "crashed": "Its process is gone. Retry from the last checkpoint, or restart from scratch.",
                 "stopped": "Retry from the last checkpoint, or restart from scratch."}


def tokens_total(tokens: dict) -> int:
    """input is litellm's prompt_tokens, which already holds both cache counts (pipeline/llm.py)."""
    return tokens["input"] + tokens["output"]


def failure(rec: dict, pending: Optional[dict]) -> Optional[dict]:
    status = rec["status"]
    node, node_error = _failed_node(rec["events"])
    if status in {"waiting_reply", "waiting_hitl"} and pending is None:
        return {"node": node, "error": rec["error"] or node_error or _E6,
                "hint": "Answer on Telegram, or stop it and retry from the last checkpoint." if rec["error"] or node_error
                else "Answer on Telegram, or stop it."}
    if status not in ENDED_NOT_OK:
        return None
    return {"node": node, "error": rec["error"] or node_error or _FAILURE_TEXT[status], "hint": _FAILURE_HINT[status]}


def _packages(rd: Path) -> list[dict]:
    out = []
    for target in publish_package.NODE_DIRS:
        try:
            pkg = publish_package.read(rd, target)
        except ValueError:
            continue
        if pkg:
            out.append({"target": target, "status": pkg.get("status")})
    return out


def _waiting_packages(rd: Path) -> list[dict]:
    return [p for p in _packages(rd) if p["status"] in {"pending", "failed"}]


_TARGET_NAME = {"wordpress": "WordPress", "youtube": "YouTube"}


def _publish_attention(rd: Path) -> list[dict]:
    return [{"kind": "publish", "label": f"{_TARGET_NAME[p['target']]} publish failed" if p["status"] == "failed" else f"Publish to {_TARGET_NAME[p['target']]}",
             "target": p["target"]} for p in _waiting_packages(rd)]


def needs_decision(rec: dict, rd: Path, pending: Optional[dict], node: Optional[str]) -> list[dict]:
    """What the run wants from the user, ignoring `dismissed`."""
    status = rec["status"]
    if status in ACTIVE:
        if pending:
            return [{"kind": pending.get("kind", "approval"), "label": str(pending.get("title", "Decision needed"))}]
        if status == "waiting_reply":
            return [{"kind": "error", "label": "Waiting for a reply on Telegram"}]
        return [{"kind": "approval", "label": "Waiting for approval on Telegram"}] if status == "waiting_hitl" else []
    out = []
    if status in ENDED_NOT_OK:
        out.append({"kind": status, "label": f"{status.capitalize()} at {node}" if node else status.capitalize()})
    return out + ([] if rec.get("dry_run") else _publish_attention(rd))  # a dry run's package is there to look at


def load_run(root: Path, run_id: str, node_ids: list[str], now: Optional[float] = None) -> Optional[dict]:
    rd = run_dir(root, run_id)
    if rd is None:
        return None
    rec = _record(rd, now)
    if rec["legacy"]:
        nodes = _legacy_nodes(rec["rows"], node_ids)
    else:
        nodes = node_states(rec["events"], node_ids, rec["status"] in FINISHED, rec["input"].get("kind"))
    keys = ("run_id", "status", "verdict", "profile", "language", "input", "started_at", "ended_at", "hitl_wait_s", "error", "dismissed", "dry_run")
    pending = pending_for(rd, rec["status"])
    return {**{k: rec[k] for k in keys}, "pending": pending, "failure": failure(rec, pending),
            "waiting_packages": [] if rec["dry_run"] else _waiting_packages(rd), "packages": _packages(rd),
            "nodes": resolve_outputs(apply_rows(nodes, rec["rows"]), rd),
            "telemetry": telemetry(rec["rows"], rec["total_s"], rec["hitl_wait_s"],
                                   read_jsonl(rd / "usage.jsonl"), rec["status"] in ACTIVE)}


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
        pending = pending_for(rd, rec["status"])
        failed = failure(rec, pending)
        node = failed["node"] if failed else None
        tele = telemetry(rec["rows"], rec["total_s"], rec["hitl_wait_s"], read_jsonl(rd / "usage.jsonl"),
                         rec["status"] in ACTIVE)
        wanted = needs_decision(rec, rd, pending, node)
        if rec["dry_run"]:
            wanted = [{**w, "label": f"Dry run: {w['label']}"} for w in wanted]
        out.append({
            "run_id": rec["run_id"], "status": rec["status"], "verdict": rec["verdict"],
            "started_at": rec["started_at"], "ended_at": rec["ended_at"],
            "input": {"kind": rec["input"].get("kind"), "name": Path(path).name if path else None},
            "total_s": rec["total_s"], "cost_usd": tele["cost_usd"], "cost_estimated": tele["cost_estimated"],
            "tokens": tokens_total(tele["tokens"]), "failed_node": node,
            "attention": [] if rec["dismissed"] else wanted, "dismissed": rec["dismissed"],
            "dismissed_attention": wanted if rec["dismissed"] else [], "dry_run": rec["dry_run"],
        })
    out.sort(key=lambda r: (r["started_at"] or 0.0, r["run_id"]), reverse=True)
    return out

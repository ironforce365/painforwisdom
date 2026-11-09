"""A/B harness, v1 (LangGraph) against v2 (sessions): spec 2026-11-09-session-pipeline-v2 §7.

    python -m pipeline.v2.ab --from-transcript tests/fixtures/transcript_2026-04-14.txt [--repeat N] [--profile sandbox]

Runs each engine as a --dry-run --auto-approve subprocess, one after the other, then reads their run folders and
writes processed/ab/<ts>/report.md and report.json. It compares; it never judges which output reads better.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

PROJECT_ROOT = Path(__file__).resolve().parents[2]
ENGINES = {"v1": "pipeline.run", "v2": "pipeline.v2.run"}
TOKEN_KINDS = ("input_tokens", "output_tokens", "cache_read_tokens", "cache_creation_tokens")
REFUSED_MARKERS = ("--auto-approve cannot give one", "--auto-approve gave up")


def _parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="python -m pipeline.v2.ab")
    ap.add_argument("--from-transcript", required=True, help="Transcript .txt fed to both engines")
    ap.add_argument("--repeat", type=int, default=1, help="Pairs of runs (default 1)")
    ap.add_argument("--profile", choices=["prod", "sandbox"], default="sandbox", help="Env profile (default: sandbox)")
    ap.add_argument("--processed-root", type=Path, default=PROJECT_ROOT / "processed",
                    help="Root of per-run output dirs (default: <project>/processed)")
    return ap


def _read_jsonl(path: Path) -> List[Dict[str, Any]]:
    rows = []
    try:
        for line in path.read_text().splitlines():
            try:
                rows.append(json.loads(line))
            except ValueError:
                continue
    except OSError:
        pass
    return rows


def _read_json(path: Path) -> Dict[str, Any]:
    try:
        return json.loads(Path(path).read_text())
    except (OSError, ValueError):
        return {}


def _themes(entry_path: Optional[str]) -> List[str]:
    try:
        text = Path(entry_path).read_text() if entry_path else ""
    except OSError:
        return []
    line = next((ln for ln in text.splitlines() if "**Themes:**" in ln), "")
    return re.findall(r"\[\[([^\]]+)\]\]", line)


def _word_count(path: Optional[str]) -> Optional[int]:
    try:
        return len(Path(path).read_text().split()) if path else None
    except OSError:
        return None


def collect(engine: str, run_dir: Path) -> Dict[str, Any]:
    """Metrics and outputs of one run, from run.json, events.jsonl, usage.jsonl and the files they point at."""
    run = _read_json(run_dir / "run.json")
    events = _read_jsonl(run_dir / "events.jsonl")
    usage = _read_jsonl(run_dir / "usage.jsonl")

    nodes: Dict[str, Dict[str, Any]] = {}
    ends: Dict[str, Dict[str, Any]] = {}
    errors = []
    failed = {(ev.get("node"), ev.get("attempt")) for ev in events if ev.get("event") == "error"}
    # Only a re-run after an error is a retry: v1 also fires a join once per inbound edge (spec §7, v1 traps).
    retries = sum(1 for ev in events if ev.get("event") == "start"
                  and (ev.get("node"), int(ev.get("attempt") or 1) - 1) in failed)
    for ev in events:
        node = nodes.setdefault(ev.get("node", "?"), {"duration_s": 0.0, "attempts": 0})
        node["attempts"] = max(node["attempts"], int(ev.get("attempt") or 1))
        if ev.get("event") == "end":
            node["duration_s"] += float(ev.get("duration_s") or 0.0)
            ends[ev["node"]] = ev
        elif ev.get("event") == "error":
            errors.append(f"{ev.get('node')}#{ev.get('attempt')}: {ev.get('error', '')}")

    tokens = {k: sum(int(u.get(k) or 0) for u in usage if u.get("event") == "end") for k in TOKEN_KINDS}
    # LiteLLM input_tokens already includes cache read/creation, the SDK's does not (kn-97feb940).
    tokens["prompt_total"] = tokens["input_tokens"] + (
        0 if engine == "v1" else tokens["cache_read_tokens"] + tokens["cache_creation_tokens"])

    result: Dict[str, Any] = {}
    outputs: Dict[str, Any] = {}
    for ev in ends.values():
        result.update(ev.get("result") or {})
        outputs.update(ev.get("outputs") or {})
    findings: Dict[str, Dict[str, Any]] = {}
    for key, items in result.items():
        if "_findings" in key and isinstance(items, list):
            for raw in items:
                try:
                    f = json.loads(raw)
                except (TypeError, ValueError):
                    continue  # end_result caps an item at 500 chars, which can cut its JSON
                findings[f"{key}: {f.get('name')}"] = {k: f.get(k) for k in ("ok", "severity", "detail")}

    started, ended = run.get("started_at"), run.get("ended_at")
    error = run.get("error")
    return {
        "engine": engine,
        "run_dir": str(run_dir),
        "status": run.get("status"),
        "verdict": run.get("verdict"),
        "error": error,
        "refused": bool(error and any(m in error for m in REFUSED_MARKERS)),
        "wall_s": round(ended - started, 1) if started and ended else None,
        "hitl_wait_s": run.get("hitl_wait_s"),
        "nodes": {k: {"duration_s": round(v["duration_s"], 1), "attempts": v["attempts"]} for k, v in nodes.items()},
        "llm_calls": sum(1 for u in usage if u.get("event") == "end"),
        "llm_errors": sum(1 for u in usage if u.get("event") == "error"),
        "tokens": tokens,
        "retries": retries,
        "errors": errors,
        "outputs": {
            "blog_title": result.get("blog_post_title"),
            "blog_word_count": _word_count(outputs.get("blog_post_path")),
            "research_rows": result.get("research_count"),
            "themes_attached": _themes(outputs.get("vault_entry_path")),
            "youtube_title": _read_json(outputs["youtube_metadata_path"]).get("title")
            if outputs.get("youtube_metadata_path") else None,
        },
        "findings": findings,
    }


def findings_diff(a: Dict[str, Dict[str, Any]], b: Dict[str, Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Findings present in one run only, or whose ok/severity differ. Detail alone differs by run path."""
    rows = []
    for name in sorted(set(a) | set(b)):
        fa, fb = a.get(name), b.get(name)
        if fa is None or fb is None or (fa["ok"], fa["severity"]) != (fb["ok"], fb["severity"]):
            rows.append({"finding": name, "v1": fa, "v2": fb})
    return rows


def _child_env(engine: str) -> Dict[str, str]:
    env = dict(os.environ)
    if engine == "v2":
        # bl-dff0b22f: profile token leaks into the session CLI (401); load_dotenv keeps a set var. Drop once it lands.
        env["ANTHROPIC_AUTH_TOKEN"] = ""
    return env


def run_engine(engine: str, args: argparse.Namespace, run_id: str, log_path: Path) -> Dict[str, Any]:
    cmd = [sys.executable, "-m", ENGINES[engine], "--from-transcript", str(Path(args.from_transcript).resolve()),
           "--profile", args.profile, "--dry-run", "--auto-approve", "--run-id", run_id,
           "--processed-root", str(args.processed_root)]
    print(f"[ab] {run_id}: {' '.join(cmd[1:])}", flush=True)
    t0 = time.time()
    with open(log_path, "w") as log:
        rc = subprocess.run(cmd, cwd=PROJECT_ROOT, env=_child_env(engine), stdout=log, stderr=subprocess.STDOUT).returncode
    wall = round(time.time() - t0, 1)
    row = collect(engine, Path(args.processed_root) / run_id / "langgraph")
    row.update(run_id=run_id, rc=rc, process_wall_s=wall, log=str(log_path))
    print(f"[ab] {run_id}: rc={rc} verdict={row['verdict']} wall={wall}s", flush=True)
    return row


def _fmt(v: Any) -> str:
    if v is None:
        return "-"
    if isinstance(v, list):
        return ", ".join(map(str, v)) or "-"
    return str(v).replace("|", "\\|").replace("\n", " ")


def render(report: Dict[str, Any]) -> str:
    out = [f"# A/B {report['ts']}", "", f"Transcript: `{report['transcript']}`  profile: {report['profile']}  "
           f"repeats: {len(report['pairs'])}", "",
           "Compares only. prompt_total = input + cache read + cache creation for both engines (kn-97feb940).", ""]
    for i, pair in enumerate(report["pairs"], 1):
        v1, v2 = pair["v1"], pair["v2"]
        out += [f"## Pair {i}", "", "| | v1 | v2 |", "|---|---|---|"]
        for label, get in (
            ("run id", lambda r: r["run_id"]),
            ("exit code", lambda r: r["rc"]),
            ("status", lambda r: r["status"]),
            ("verdict", lambda r: r["verdict"]),
            ("refused by --auto-approve", lambda r: r["refused"]),
            ("error", lambda r: r["error"]),
            ("process wall s", lambda r: r["process_wall_s"]),
            ("run wall s", lambda r: r["wall_s"]),
            ("LLM calls", lambda r: r["llm_calls"]),
            ("LLM call errors", lambda r: r["llm_errors"]),
            *((k, (lambda r, k=k: r["tokens"][k])) for k in (*TOKEN_KINDS, "prompt_total")),
            ("retries", lambda r: r["retries"]),
            ("node errors", lambda r: r["errors"]),
            ("blog title", lambda r: r["outputs"]["blog_title"]),
            ("blog word count", lambda r: r["outputs"]["blog_word_count"]),
            ("research rows", lambda r: r["outputs"]["research_rows"]),
            ("themes attached", lambda r: r["outputs"]["themes_attached"]),
            ("YouTube title", lambda r: r["outputs"]["youtube_title"]),
        ):
            out.append(f"| {label} | {_fmt(get(v1))} | {_fmt(get(v2))} |")
        out += ["", "Node durations (s, attempts):", "", "| node | v1 | v2 |", "|---|---|---|"]
        for node in sorted(set(v1["nodes"]) | set(v2["nodes"])):
            cell = [(f"{r['nodes'][node]['duration_s']} ({r['nodes'][node]['attempts']})" if node in r["nodes"] else "-")
                    for r in (v1, v2)]
            out.append(f"| {node} | {cell[0]} | {cell[1]} |")
        out += ["", "Validator findings that differ:", ""]
        if not pair["findings_diff"]:
            out.append("none")
        else:
            out += ["| finding | v1 | v2 |", "|---|---|---|"]
            for d in pair["findings_diff"]:
                cell = [("absent" if f is None else f"ok={f['ok']} {f['severity']}: {_fmt(f['detail'])}")
                        for f in (d["v1"], d["v2"])]
                out.append(f"| {_fmt(d['finding'])} | {cell[0]} | {cell[1]} |")
        out.append("")
    return "\n".join(out)


def main(argv: Optional[List[str]] = None) -> int:
    args = _parser().parse_args(argv)
    if not Path(args.from_transcript).is_file():
        raise SystemExit(f"transcript not found: {Path(args.from_transcript).resolve()}")
    args.processed_root = Path(args.processed_root).resolve()
    ts = time.strftime("%Y%m%d-%H%M%S")
    out_dir = args.processed_root / "ab" / ts
    out_dir.mkdir(parents=True, exist_ok=True)
    report: Dict[str, Any] = {"ts": ts, "transcript": str(Path(args.from_transcript).resolve()),
                              "profile": args.profile, "pairs": []}
    for n in range(1, args.repeat + 1):
        # Never in parallel: both engines draw on one subscription limit, overlap would skew timing.
        pair: Dict[str, Any] = {e: run_engine(e, args, f"ab-{ts}-{n}-{e}", out_dir / f"ab-{ts}-{n}-{e}.log") for e in ENGINES}
        pair["findings_diff"] = findings_diff(pair["v1"]["findings"], pair["v2"]["findings"])
        report["pairs"].append(pair)
        (out_dir / "report.json").write_text(json.dumps(report, indent=2))
        (out_dir / "report.md").write_text(render(report))
    print(f"[ab] report: {out_dir / 'report.md'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

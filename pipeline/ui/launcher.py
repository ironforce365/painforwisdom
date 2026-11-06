"""Input listing and run launch (spec 2026-10-31-pipeline-console-ui §5). Never imports pipeline.run."""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Optional

from pipeline import publishing_config, runtime
from pipeline.ui import runs
from pipeline.ui.settings import Settings

# Copy of pipeline.run.VIDEO_EXTENSIONS; importing run.py would call load_dotenv.
VIDEO_EXTENSIONS = (".mp4", ".mov", ".mkv", ".webm", ".m4v", ".avi")
CHECKOUT_ROOT = runtime.PROJECT_ROOT
FAKE_DEFAULTS = {"scenario": "ok", "step_ms": 200, "wait_s": 120}
LANGUAGE_CODE = re.compile(r"[a-z]{2,3}")  # the form Whisper's --language takes; it rejects a code it does not know

_lock = threading.Lock()


class LaunchError(Exception):
    def __init__(self, status: int, detail: str, run_id: Optional[str] = None):
        super().__init__(detail)
        self.status, self.detail, self.run_id = status, detail, run_id


def input_kind(name: str) -> Optional[str]:
    suffix = Path(name).suffix.lower()
    if suffix in VIDEO_EXTENSIONS:
        return "video"
    return "transcript" if suffix == ".txt" else None


def list_inputs(inbox: Path, processed_root: Optional[Path] = None) -> list[dict]:
    """With `processed_root`, each input names the runs that already used it, newest first (S3)."""
    if not inbox.is_dir():
        return []
    previous: dict[str, list[str]] = {}
    for run in runs.list_runs(processed_root) if processed_root else ():
        previous.setdefault(run["input"]["name"], []).append(run["run_id"])
    out = []
    for p in inbox.iterdir():
        kind = input_kind(p.name)
        if kind is None or p.name.startswith(".") or not p.is_file():
            continue
        st = p.stat()
        item = {"name": p.name, "kind": kind, "bytes": st.st_size, "mtime": st.st_mtime}
        out.append({**item, "previous_runs": previous.get(p.name, [])} if processed_root else item)
    out.sort(key=lambda i: (i["mtime"], i["name"]), reverse=True)
    return out


def upload_target(inbox: Path, name: str) -> Path:
    """Inbox path for an uploaded file; 400 unless `name` is a plain basename list_inputs would show."""
    if name != Path(name).name or any(c in name for c in "\\\0") or name.startswith(".") or input_kind(name) is None:
        raise LaunchError(400, "bad input name")
    return inbox / name


def publish_upload(part: Path, target: Path) -> None:
    """Move a finished upload into place; 409 if the name exists (link never overwrites, unlike replace)."""
    try:
        os.link(part, target)
    except FileExistsError:
        raise LaunchError(409, "input exists") from None
    finally:
        part.unlink()


def new_run_id(root: Path, stamp: Optional[str] = None) -> str:
    base = f"{stamp or time.strftime('%Y-%m-%d_%H%M%S')}_ui"
    rid, n = base, 1
    while (root / rid).exists():
        n += 1
        rid = f"{base}_{n}"
    return rid


def default_language(settings: Settings) -> str:
    return publishing_config.load(settings.profile)["language"]


def _run_language(settings: Settings, chosen: Optional[str]) -> str:
    if not chosen:
        return default_language(settings)
    if not LANGUAGE_CODE.fullmatch(chosen):
        raise LaunchError(400, f"language must be a code like en or es, not {chosen!r}")
    return chosen


def build_argv(settings: Settings, run_id: str, path: Path, kind: str, auto_approve: bool,
               fake: Optional[dict], language: str) -> list[str]:
    if settings.runner == "fake":
        f = {**FAKE_DEFAULTS, **(fake or {})}
        argv = [sys.executable, "-m", "pipeline.ui.fake_runner", "--run-id", run_id,
                "--processed-root", str(settings.processed_root), "--input", str(path), "--language", language,
                "--scenario", f["scenario"], "--step-ms", str(f["step_ms"]), "--wait-s", str(f["wait_s"])]
        return argv + ["--auto-approve"] if auto_approve else argv
    flag = "--video" if kind == "video" else "--from-transcript"
    argv = [sys.executable, "-m", "pipeline.run", "--profile", settings.profile, "--run-id", run_id,
            "--processed-root", str(settings.processed_root), "--language", language, flag, str(path)]
    return argv + ["--auto-approve"] if auto_approve else argv


def build_retry_argv(settings: Settings, run_id: str, profile: Optional[str], auto_approve: bool) -> list[str]:
    root = str(settings.processed_root)
    if settings.runner == "fake":
        return [sys.executable, "-m", "pipeline.ui.fake_runner", "retry", "--run-id", run_id, "--processed-root", root,
                "--step-ms", str(FAKE_DEFAULTS["step_ms"])]
    # --no-archive: the run's input and its packages keep pointing at the file.
    argv = [sys.executable, "-m", "pipeline.retry", "--profile", profile or settings.profile, "--run-id", run_id,
            "--processed-root", root, "--no-archive"]
    return argv + ["--auto-approve"] if auto_approve else argv


def active_run(root: Path) -> Optional[str]:
    return runs.active_run_id(root)


def spawn(argv: list[str], log_path: Path) -> int:
    """Detached child with the server's env as received; a daemon thread reaps it so it never lingers as a zombie."""
    with open(log_path, "ab") as log:
        proc = subprocess.Popen(argv, cwd=CHECKOUT_ROOT, stdin=subprocess.DEVNULL, stdout=log,
                                stderr=subprocess.STDOUT, start_new_session=True)
    threading.Thread(target=proc.wait, daemon=True).start()
    return proc.pid


def write_provisional(rd: Path, record: dict) -> None:
    """Create run.json only if the child has not written its own yet (link fails on an existing file)."""
    tmp = rd / f".run.json.{os.getpid()}.tmp"
    tmp.write_text(json.dumps(record, indent=2))
    try:
        os.link(tmp, rd / "run.json")
    except FileExistsError:
        pass
    finally:
        tmp.unlink()


def launch(settings: Settings, name: str, auto_approve: bool, fake: Optional[dict], language: Optional[str] = None) -> str:
    if fake is not None and settings.runner != "fake":
        raise LaunchError(400, "fake is only accepted by the fake runner")
    match = next((i for i in list_inputs(settings.inbox) if i["name"] == name), None)
    if match is None:
        raise LaunchError(404, "unknown input")
    return launch_path(settings, (settings.inbox / match["name"]).resolve(), match["kind"], auto_approve, fake, language)


def launch_path(settings: Settings, path: Path, kind: str, auto_approve: bool, fake: Optional[dict],
                language: Optional[str] = None) -> str:
    language = _run_language(settings, language)
    with _lock:
        busy = active_run(settings.processed_root)
        if busy is not None:
            raise LaunchError(409, "run active", busy)
        run_id = new_run_id(settings.processed_root)
        rd = settings.processed_root / run_id / "langgraph"
        rd.mkdir(parents=True)
        pid = spawn(build_argv(settings, run_id, path, kind, auto_approve, fake, language), rd / "stdout.log")
        write_provisional(rd, {
            "run_id": run_id, "status": "running", "verdict": None, "pid": pid, "profile": settings.profile,
            "language": language, "input": {"kind": kind, "path": str(path)}, "started_at": time.time(), "ended_at": None,
            "hitl_wait_s": 0.0, "error": None,
        })
    return run_id

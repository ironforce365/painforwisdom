"""NotebookLM publisher — Phase 6c.

Per-theme notebook accumulation. For each cluster:
  1. Read or create the theme's notebook (id persisted at briefs/<theme>/.notebooklm-id).
  2. Upload deep-dive.md, application.md, audio-prompts.md as 3 sources.
  3. Upload the vault entry as a 4th source (gives NotebookLM raw Gonzalo voice).
  4. Render the Gonzalo-centered focus prompt from the locked template (see
     plan section 6c-focus). Pulls Story Anchor + key quote + 3 protocol
     adjustments + open question.
  5. Trigger `nlm audio create --format deep_dive --length long --source-ids ...
     --focus "<prompt>" --confirm` scoped to this cluster's source ids.
  6. Write notebooklm_url.txt + audio_artifact_id.txt to cluster dir. NO download.

Failure handling:
  - Rate-limit: exponential backoff (5/15/30 min) on Pro tier; fail-loud on free.
  - Auth expired: Telegram alert + halt.
  - Audio still rendering at poll timeout: write audio_pending.txt + succeed.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

from pipeline.runtime import PROJECT_ROOT


NLM_PROFILE = os.environ.get("NLM_PROFILE", "painforwisdom")
LISTENER_NAME = os.environ.get("LISTENER_NAME", "Gonzalo")
LISTENER_BIO = os.environ.get(
    "LISTENER_BIO",
    "an ultra-distance runner and father of three children whose deliberate-discomfort practice grew out of his daily life",
)
VAULT_DIR = PROJECT_ROOT / "obsidian-vault" / "gonzalo-book" / "entries"
POLL_INTERVAL_S = 60
POLL_MAX_S = 900  # 15 min — audio normally completes in ~10


_SOURCE_ID_RE = re.compile(r"Source ID:\s*([0-9a-f-]+)", re.IGNORECASE)
_ARTIFACT_ID_RE = re.compile(r"Artifact ID:\s*([0-9a-f-]+)", re.IGNORECASE)
_NB_ID_RE = re.compile(r"ID:\s*([0-9a-f-]+)", re.IGNORECASE)


@dataclass
class PublishResult:
    notebook_id: str
    notebook_url: str
    artifact_id: str
    status: str  # completed | rendering | failed
    audio_url: str = ""  # direct streamable audio URL (Google CDN); empty if not yet rendered
    detail: str = ""


class PublishError(Exception):
    pass


# --------------------------- NLM CLI wrappers ---------------------------


def _run_nlm(args: List[str], timeout: int = 180) -> tuple[int, str, str]:
    completed = subprocess.run(
        ["nlm", *args, "-p", NLM_PROFILE],
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    return completed.returncode, completed.stdout, completed.stderr


def _read_or_create_notebook(theme: str) -> str:
    """Return notebook_id. Persists at briefs/<theme>/.notebooklm-id."""
    theme_dir = PROJECT_ROOT / "briefs" / theme
    id_file = theme_dir / ".notebooklm-id"
    if id_file.exists():
        return id_file.read_text().strip()
    theme_dir.mkdir(parents=True, exist_ok=True)
    rc, stdout, stderr = _run_nlm(["notebook", "create", theme])
    if rc != 0:
        raise PublishError(f"nlm notebook create failed: {stderr[:500]}")
    m = _NB_ID_RE.search(stdout)
    if not m:
        raise PublishError(f"could not parse notebook id from: {stdout[:500]}")
    nb_id = m.group(1)
    id_file.write_text(nb_id + "\n")
    return nb_id


def _add_source(notebook_id: str, file_path: Path, title: str) -> str:
    rc, stdout, stderr = _run_nlm(
        [
            "source",
            "add",
            notebook_id,
            "--file",
            str(file_path),
            "--title",
            title,
            "--wait",
        ],
        timeout=300,
    )
    if rc != 0:
        raise PublishError(f"nlm source add failed for {file_path.name}: {stderr[:500]}")
    m = _SOURCE_ID_RE.search(stdout)
    if not m:
        raise PublishError(f"could not parse source id from: {stdout[:500]}")
    return m.group(1)


def _trigger_audio(
    notebook_id: str,
    source_ids: List[str],
    focus_prompt: str,
    length: str = "long",
    fmt: str = "deep_dive",
) -> str:
    rc, stdout, stderr = _run_nlm(
        [
            "audio",
            "create",
            notebook_id,
            "--format",
            fmt,
            "--length",
            length,
            "--source-ids",
            ",".join(source_ids),
            "--focus",
            focus_prompt,
            "--confirm",
        ],
        timeout=180,
    )
    if rc != 0:
        raise PublishError(f"nlm audio create failed: {stderr[:500]}")
    m = _ARTIFACT_ID_RE.search(stdout)
    if not m:
        raise PublishError(f"could not parse artifact id from: {stdout[:500]}")
    return m.group(1)


def _poll_audio(notebook_id: str, artifact_id: str, max_s: int = POLL_MAX_S) -> str:
    """Returns status: completed | rendering | failed."""
    deadline = time.time() + max_s
    while time.time() < deadline:
        rc, stdout, _stderr = _run_nlm(["studio", "status", notebook_id])
        if rc == 0 and artifact_id in stdout:
            # Tiny parser — search for status line near artifact id
            lines = stdout.splitlines()
            for idx, line in enumerate(lines):
                if artifact_id in line:
                    for follow in lines[idx : idx + 5]:
                        if '"status"' in follow:
                            m = re.search(r'"status":\s*"([^"]+)"', follow)
                            if m:
                                status = m.group(1)
                                if status in ("completed", "failed"):
                                    return status
        time.sleep(POLL_INTERVAL_S)
    return "rendering"


def _fetch_audio_url(notebook_id: str, artifact_id: str) -> str:
    """Look up the direct streamable audio URL for a completed artifact.

    Returns empty string if the artifact is missing the `audio_url` field
    (still rendering, failed, or older artifact). The URL is a Google CDN
    link that plays directly in browsers and mobile audio apps."""
    rc, stdout, _stderr = _run_nlm(["studio", "status", notebook_id, "--full", "--json"])
    if rc != 0:
        return ""
    try:
        artifacts = json.loads(stdout)
    except (json.JSONDecodeError, ValueError):
        return ""
    if not isinstance(artifacts, list):
        return ""
    for art in artifacts:
        if isinstance(art, dict) and art.get("id") == artifact_id:
            url = art.get("audio_url") or ""
            return url if isinstance(url, str) else ""
    return ""


# ----------------------- focus-prompt template ---------------------------


def _read_section(text: str, header: str) -> str:
    m = re.search(rf"##\s+{re.escape(header)}\s*\n(.+?)(?=\n##\s|\Z)", text, re.DOTALL)
    return m.group(1).strip() if m else ""


def _extract_quote(raw_notes: str) -> str:
    m = re.search(r'"([^"]{20,200})"', raw_notes)
    return m.group(1) if m else ""


def _load_vault(slug: str) -> Optional[Dict[str, str]]:
    candidates = [slug, f"{slug}.md", slug.removesuffix(".md") + ".md"]
    for cand in candidates:
        p = VAULT_DIR / cand
        if p.exists():
            text = p.read_text()
            return {
                "path": str(p),
                "raw": text,
                "story_anchor": _read_section(text, "Story Anchor"),
                "raw_transcript": _read_section(text, "Raw Transcript Notes"),
                "quote": _extract_quote(_read_section(text, "Raw Transcript Notes"))
                or _extract_quote(text),
            }
    return None


def render_focus_prompt(
    *,
    theme: str,
    sub_angle: str,
    sources: List[Dict[str, Any]],
    vault: Dict[str, str],
    application_md_text: str,
) -> str:
    """Render the Phase 6c-focus locked template."""
    name = LISTENER_NAME
    bio = LISTENER_BIO

    story_anchor = vault.get("story_anchor", "").strip().replace("\n", " ")[:500]
    quote = vault.get("quote", "").strip()

    # Pull "## Three concrete adjustments to the practice" and "## One question"
    adjustments = _read_section(application_md_text, "Three concrete adjustments to the practice")
    open_question = _read_section(application_md_text, f"One question the brief leaves on {name}'s desk")
    if not open_question:
        open_question = _read_section(application_md_text, "One question the brief leaves on the table")

    source_lines: List[str] = []
    for i, src in enumerate(sources, start=1):
        source_lines.append(
            f"{i}. {src.get('title','')} by {src.get('author_host','')} ({src.get('type','')}) "
            f"— role: {src.get('relevance','')[:160]}"
        )
    sources_block = "\n".join(source_lines)

    return (
        f"You are generating an audio overview for ONE specific listener: {name.upper()}, "
        f"{bio}. {name} works on the `{theme}` theme; this brief covers the sub-angle "
        f"'{sub_angle}'.\n\n"
        f"The 'Application Brief' source and the vault entry are the SPINE of this audio. "
        f"The academic/practitioner sources are SUPPORTING EVIDENCE used to interrogate, "
        f"refine, or extend {name}'s framework — not the other way around.\n\n"
        f"Supporting sources:\n{sources_block}\n\n"
        f"Required structure:\n"
        f"1. OPEN by naming {name} and his specific moment from the vault entry: {story_anchor}\n"
        f"   Quote his words: \"{quote}\"\n"
        f"2. THEN bring in each supporting source in order, framing each one as evidence "
        f"that confirms, sharpens, or contradicts {name}'s lived practice.\n"
        f"3. Walk through {name}'s concrete protocol adjustments verbatim from the Application Brief:\n"
        f"{adjustments[:1500]}\n"
        f"4. CLOSE on the open question on {name}'s desk:\n"
        f"{open_question[:600]}\n\n"
        f"Reference {name} BY NAME at least eight times. Reference his concrete examples "
        f"BY NAME at least three times each. Frame every academic source as a tool for "
        f"interrogating HIS practice. No motivational framing. No hedging. Dense and "
        f"direct throughout. The audio is FOR him, ABOUT him."
    )


# ----------------------------- main entry --------------------------------


def publish(
    cluster_dir: Path,
    *,
    theme: str,
    sub_angle: str,
    vault_entry_slug: str,
    sources: List[Dict[str, Any]],
    length: str = "long",
    fmt: str = "deep_dive",
) -> PublishResult:
    """Upload + audio for a single cluster. Writes notebooklm_url.txt +
    audio_artifact_id.txt into cluster_dir. NO m4a download (per locked
    feedback memory)."""
    notebook_id = _read_or_create_notebook(theme)

    today_sub = cluster_dir.name  # e.g. 2026-05-11--fear-override-...
    source_ids: List[str] = []
    for kind in ("deep-dive.md", "application.md", "audio-prompts.md"):
        f = cluster_dir / kind
        if not f.exists():
            raise PublishError(f"missing brief file: {f}")
        source_ids.append(_add_source(notebook_id, f, f"{today_sub}: {kind.removesuffix('.md')}"))

    vault = _load_vault(vault_entry_slug)
    if vault is None:
        raise PublishError(f"vault entry not found: {vault_entry_slug}")
    vault_source_id = _add_source(
        notebook_id,
        Path(vault["path"]),
        f"{LISTENER_NAME}'s vault entry — {vault_entry_slug}",
    )
    source_ids.append(vault_source_id)

    application_md = (cluster_dir / "application.md").read_text()
    focus_prompt = render_focus_prompt(
        theme=theme,
        sub_angle=sub_angle,
        sources=sources,
        vault=vault,
        application_md_text=application_md,
    )

    artifact_id = _trigger_audio(
        notebook_id=notebook_id,
        source_ids=source_ids,
        focus_prompt=focus_prompt,
        length=length,
        fmt=fmt,
    )

    notebook_url = f"https://notebooklm.google.com/notebook/{notebook_id}"
    (cluster_dir / "notebooklm_url.txt").write_text(notebook_url + "\n")
    (cluster_dir / "audio_artifact_id.txt").write_text(artifact_id + "\n")

    status = _poll_audio(notebook_id, artifact_id)
    audio_url = ""
    if status == "completed":
        audio_url = _fetch_audio_url(notebook_id, artifact_id)
        if audio_url:
            (cluster_dir / "audio_url.txt").write_text(audio_url + "\n")
    if status == "rendering":
        (cluster_dir / "audio_pending.txt").write_text(
            f"artifact {artifact_id} still rendering at poll timeout; "
            f"will appear in NotebookLM mobile app when ready.\n"
        )

    return PublishResult(
        notebook_id=notebook_id,
        notebook_url=notebook_url,
        artifact_id=artifact_id,
        status=status,
        audio_url=audio_url,
    )

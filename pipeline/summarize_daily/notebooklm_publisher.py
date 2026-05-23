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

# nlm CLI emits ANSI SGR escapes (color/bold) which break the ID regexes.
_ANSI_RE = re.compile(r"\x1b\[[0-9;]*[a-zA-Z]")


def _strip_ansi(s: str) -> str:
    return _ANSI_RE.sub("", s)


@dataclass
class PublishResult:
    notebook_id: str
    notebook_url: str
    artifact_id: str
    status: str  # audio status: completed | rendering | failed
    audio_url: str = ""  # direct streamable audio URL (Google CDN); empty if not yet rendered
    detail: str = ""
    # Companion artifacts — populated when slides/mindmap/infographic generation
    # is requested alongside the audio. Each path is a local download in
    # cluster_dir; each artifact_id remains useful for re-downloading later.
    slide_deck_artifact_id: str = ""
    slide_deck_path: str = ""
    mindmap_artifact_id: str = ""
    mindmap_path: str = ""
    infographic_artifact_id: str = ""
    infographic_path: str = ""


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
    return completed.returncode, _strip_ansi(completed.stdout), _strip_ansi(completed.stderr)


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


def _poll_artifact(
    notebook_id: str,
    artifact_id: str,
    max_s: int = POLL_MAX_S,
) -> str:
    """Generic poller for any studio artifact (audio, slide_deck, infographic,
    mind_map). Returns: completed | rendering | failed.

    `studio status` returns a JSON array of artifacts; we look up the matching
    id and read its `.status` field directly. Tolerant of `in_progress` (the
    nlm CLI's word for "still rendering") and the simpler `completed/failed`.
    """
    deadline = time.time() + max_s
    while time.time() < deadline:
        rc, stdout, _stderr = _run_nlm(["studio", "status", notebook_id])
        if rc == 0:
            try:
                artifacts = json.loads(stdout)
            except (json.JSONDecodeError, ValueError):
                artifacts = None
            if isinstance(artifacts, list):
                for art in artifacts:
                    if isinstance(art, dict) and art.get("id") == artifact_id:
                        status = art.get("status", "")
                        if status in ("completed", "failed"):
                            return status
                        # in_progress / queued / unknown → keep polling
                        break
        time.sleep(POLL_INTERVAL_S)
    return "rendering"


# NotebookLM rate-limits slide-deck and infographic creation independently
# of audio + mindmap (which use a different orchestration pipeline). Daily
# fires (3 briefs * 2 visuals = 6 calls) stay under the cap, but the
# retrofit script can burst 23+ clusters * 2 visuals in minutes. Sleep
# between attempts when the CLI returns "Rate limited" in stdout.
_RATE_LIMIT_MARKER = "Rate limited"
_RATE_LIMIT_BACKOFF_S = (90, 180, 300)  # cumulative ~9.5 min over 3 retries


def _is_rate_limited(stdout: str, stderr: str) -> bool:
    return _RATE_LIMIT_MARKER in (stdout or "") or _RATE_LIMIT_MARKER in (stderr or "")


def _run_nlm_with_rate_retry(
    args: List[str],
    *,
    timeout: int = 180,
    kind: str = "nlm",
) -> tuple[int, str, str]:
    """Wrap `_run_nlm` with up to 3 retries on detected rate-limit responses.

    The nlm CLI exits non-zero with "Rate limited" in stdout for slide/info
    create when NotebookLM throttles. Mindmap + audio creates go through a
    separate orchestration that doesn't throttle the same way.
    """
    rc, stdout, stderr = _run_nlm(args, timeout=timeout)
    if rc == 0 or not _is_rate_limited(stdout, stderr):
        return rc, stdout, stderr
    for attempt, wait_s in enumerate(_RATE_LIMIT_BACKOFF_S, start=1):
        print(
            f"!! {kind} rate-limited (attempt {attempt}/{len(_RATE_LIMIT_BACKOFF_S)}). "
            f"Sleeping {wait_s}s then retrying.",
            flush=True,
        )
        time.sleep(wait_s)
        rc, stdout, stderr = _run_nlm(args, timeout=timeout)
        if rc == 0:
            return rc, stdout, stderr
        if not _is_rate_limited(stdout, stderr):
            return rc, stdout, stderr
    return rc, stdout, stderr


def _create_slides(
    notebook_id: str,
    source_ids: List[str],
    focus_prompt: str,
    fmt: str = "detailed_deck",
) -> str:
    """Kick off a slide-deck generation. Returns artifact_id (still rendering)."""
    rc, stdout, stderr = _run_nlm_with_rate_retry(
        [
            "slides",
            "create",
            notebook_id,
            "--format",
            fmt,
            "--source-ids",
            ",".join(source_ids),
            "--focus",
            focus_prompt,
            "--confirm",
        ],
        timeout=180,
        kind="slides create",
    )
    if rc != 0:
        # Include stdout in the surfaced error: the nlm CLI prints
        # "Error: Rate limited ..." on stdout (not stderr) for create
        # endpoints, so a stderr-only message would be empty.
        detail = (stderr or "").strip() or (stdout or "").strip()
        raise PublishError(f"nlm slides create failed: {detail[:500]}")
    m = _ARTIFACT_ID_RE.search(stdout)
    if not m:
        raise PublishError(f"could not parse slide-deck artifact id from: {stdout[:500]}")
    return m.group(1)


def _create_infographic(
    notebook_id: str,
    source_ids: List[str],
    focus_prompt: str,
    *,
    orientation: str = "portrait",
    detail: str = "detailed",
    style: str = "scientific",
) -> str:
    """Kick off infographic generation. Returns artifact_id (still rendering)."""
    rc, stdout, stderr = _run_nlm_with_rate_retry(
        [
            "infographic",
            "create",
            notebook_id,
            "--style",
            style,
            "--orientation",
            orientation,
            "--detail",
            detail,
            "--source-ids",
            ",".join(source_ids),
            "--focus",
            focus_prompt,
            "--confirm",
        ],
        timeout=180,
        kind="infographic create",
    )
    if rc != 0:
        detail_msg = (stderr or "").strip() or (stdout or "").strip()
        raise PublishError(f"nlm infographic create failed: {detail_msg[:500]}")
    m = _ARTIFACT_ID_RE.search(stdout)
    if not m:
        raise PublishError(f"could not parse infographic artifact id from: {stdout[:500]}")
    return m.group(1)


def _create_mindmap(notebook_id: str, source_ids: List[str], title: str) -> str:
    """Create mind map (synchronous on the nlm side). Returns artifact_id.

    `mindmap create` doesn't accept --focus; the mechanism scope is encoded in
    the title (e.g. 'rest-guilt — physiology pathway map') and in the source
    set, which is already curated to the cluster's brief files + vault entry.
    """
    rc, stdout, stderr = _run_nlm(
        [
            "mindmap",
            "create",
            notebook_id,
            "--title",
            title,
            "--source-ids",
            ",".join(source_ids),
            "--confirm",
        ],
        timeout=180,
    )
    if rc != 0:
        raise PublishError(f"nlm mindmap create failed: {stderr[:500]}")
    # mindmap create prints `  ID: <uuid>` rather than `Artifact ID:`. Either
    # regex would match; we use _NB_ID_RE because the line literally starts
    # with `ID:`.
    m = _NB_ID_RE.search(stdout)
    if not m:
        raise PublishError(f"could not parse mind-map id from: {stdout[:500]}")
    return m.group(1)


# `nlm download <kind>` does NOT accept the `-p/--profile` flag (unlike the
# create subcommands). Profile must be passed via the NLM_PROFILE env var.
_DOWNLOAD_KIND_ARGS: Dict[str, List[str]] = {
    "slide-deck": ["--format", "pdf", "--no-progress"],
    "mind-map": [],
    "infographic": ["--no-progress"],
}


def _download_artifact(
    notebook_id: str,
    artifact_id: str,
    kind: str,
    dest_path: Path,
) -> Path:
    """Download an artifact into dest_path. kind ∈ {slide-deck, mind-map, infographic}."""
    if kind not in _DOWNLOAD_KIND_ARGS:
        raise PublishError(f"unknown download kind: {kind}")
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    env["NLM_PROFILE"] = NLM_PROFILE
    completed = subprocess.run(
        [
            "nlm",
            "download",
            kind,
            notebook_id,
            "--id",
            artifact_id,
            "-o",
            str(dest_path),
            *_DOWNLOAD_KIND_ARGS[kind],
        ],
        capture_output=True,
        text=True,
        timeout=180,
        env=env,
    )
    if completed.returncode != 0:
        raise PublishError(
            f"nlm download {kind} failed: {_strip_ansi(completed.stderr or completed.stdout)[:500]}"
        )
    if not dest_path.exists():
        raise PublishError(f"nlm download {kind} reported success but {dest_path} is missing")
    return dest_path


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


def render_slides_focus(theme: str, sub_angle: str) -> str:
    """Slide-deck focus prompt — terminology + mechanism only, no application.

    Audios already cover application; slides exist as a reference deck Gonzalo
    can revisit when the neurobiology gets dense in the audio."""
    return (
        f"Generate a slide deck explaining ONLY the neurobiology and physiology "
        f"terminology and mechanisms referenced in these sources. For each term "
        f"(neurotransmitter, brain region, hormone, pathway, neural structure, "
        f"endocrine response, autonomic state): define it, describe what triggers "
        f"it, what it produces, and its downstream effect. Do NOT include personal "
        f"application, protocol adjustments, motivational framing, or anecdotes — "
        f"those live in the audio overview and the application brief. Listener: "
        f"{LISTENER_NAME}, {LISTENER_BIO}. Theme: {theme}. Sub-angle: {sub_angle}. "
        f"Make every slide dense with mechanistic detail; this is a reference deck, "
        f"not a pep talk."
    )


def render_infographic_focus(theme: str, sub_angle: str) -> str:
    """One-page biology poster — pathways, neurons, hormones, structures."""
    return (
        f"One-page biology reference poster. Visualize the pathways, neurons, "
        f"hormones, brain regions, and biological structures named in these "
        f"sources. Show directional arrows between elements where a causal chain "
        f"exists (e.g. inflammation → cytokines → bloodstream → blood-brain "
        f"barrier → microglia → dopamine reduction → motivation loss / sickness "
        f"behavior). Label every node with its function. Concise labels — this "
        f"is a glance-card not a textbook. Listener: {LISTENER_NAME}. Theme: "
        f"{theme}. Sub-angle: {sub_angle}."
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

    # Companion visual artifacts: slide deck + infographic + mind map. All run
    # against the same source set as the audio. Slides + infographic queue on
    # NotebookLM's side and poll later; mindmap returns synchronously. Each
    # failure is isolated so a broken visual doesn't kill the audio result.
    slides_id, slides_path = "", ""
    mindmap_id, mindmap_path = "", ""
    infographic_id, infographic_path = "", ""

    try:
        slides_id = _create_slides(
            notebook_id, source_ids, render_slides_focus(theme, sub_angle)
        )
        (cluster_dir / "slide_deck_artifact_id.txt").write_text(slides_id + "\n")
    except PublishError as exc:
        print(f"!! slides create failed: {exc}")

    try:
        infographic_id = _create_infographic(
            notebook_id, source_ids, render_infographic_focus(theme, sub_angle)
        )
        (cluster_dir / "infographic_artifact_id.txt").write_text(infographic_id + "\n")
    except PublishError as exc:
        print(f"!! infographic create failed: {exc}")

    try:
        mindmap_id = _create_mindmap(
            notebook_id,
            source_ids,
            f"{theme} — {sub_angle} pathway map",
        )
        (cluster_dir / "mindmap_artifact_id.txt").write_text(mindmap_id + "\n")
    except PublishError as exc:
        print(f"!! mindmap create failed: {exc}")

    status = _poll_artifact(notebook_id, artifact_id)
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

    # Visuals are usually done by the time audio polling returns (slides ~3 min,
    # infographic ~2 min, audio ~10 min). If a visual is still rendering or has
    # failed, skip the download silently — the artifact_id was persisted above
    # so it can be retried later.
    for kind, art_id, fname, attr in (
        ("slide-deck", slides_id, "slides.pdf", "slides_path"),
        ("infographic", infographic_id, "infographic.png", "infographic_path"),
        ("mind-map", mindmap_id, "mindmap.json", "mindmap_path"),
    ):
        if not art_id:
            continue
        try:
            visual_status = _poll_artifact(
                notebook_id, art_id, max_s=300
            ) if kind != "mind-map" else "completed"
            if visual_status != "completed":
                print(f"!! {kind} not ready (status={visual_status}); skipping download")
                continue
            dest = cluster_dir / fname
            _download_artifact(notebook_id, art_id, kind, dest)
            if attr == "slides_path":
                slides_path = str(dest)
            elif attr == "infographic_path":
                infographic_path = str(dest)
            elif attr == "mindmap_path":
                mindmap_path = str(dest)
        except PublishError as exc:
            print(f"!! {kind} download failed: {exc}")

    return PublishResult(
        notebook_id=notebook_id,
        notebook_url=notebook_url,
        artifact_id=artifact_id,
        status=status,
        audio_url=audio_url,
        slide_deck_artifact_id=slides_id,
        slide_deck_path=slides_path,
        mindmap_artifact_id=mindmap_id,
        mindmap_path=mindmap_path,
        infographic_artifact_id=infographic_id,
        infographic_path=infographic_path,
    )

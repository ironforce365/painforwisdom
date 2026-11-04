"""YouTube metadata node, package and adapter (spec 2026-11-03-console-operability §7). The client is mocked."""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from pipeline import publish as publish_core
from pipeline import publishing_config
from pipeline.nodes import youtube_metadata, youtube_upload
from pipeline.nodes.validators.branch_youtube import node_bv_youtube
from pipeline.publish import package
from pipeline.publish import youtube as adapter

# What run 2026-11-03_055748_ui's prompt produced: load_agent_prompt strips "## OUTPUT", so the agent never saw JSON.
LABELLED = "**Title:** your watch doesn't know your limit\n\n**Description:** messy data\n\n**Extra tags:** amcc, effort"
GOOD = json.dumps({"title": "stop optimizing for ease", "description": "growth hides", "tags_extra": ["amcc", "Discipline"]})
USAGE = {"input_tokens": 10, "output_tokens": 2, "cache_read_tokens": 3, "cache_creation_tokens": 4, "cost_usd": 0.5}


def _llm(monkeypatch, *responses) -> list[dict]:
    calls: list[dict] = []
    replies = iter(responses)

    def fake(model, system, user, max_tokens):
        calls.append({"system": system, "user": user, "max_tokens": max_tokens})
        reply = next(replies)
        if isinstance(reply, Exception):
            raise reply
        return {"text": reply, "model": model, "stop_reason": "stop", **USAGE}

    monkeypatch.setattr(youtube_metadata, "call_llm", fake)
    return calls


def _config(monkeypatch, tmp_path, **youtube) -> None:
    cfg = json.loads(publishing_config.CONFIG_PATH.read_text())
    cfg["youtube"].update(youtube)
    path = tmp_path / "publishing.json"
    path.write_text(json.dumps(cfg))
    monkeypatch.setattr(publishing_config, "CONFIG_PATH", path)
    monkeypatch.setenv("PIPELINE_PROFILE", "prod")


@pytest.fixture
def state(tmp_path, monkeypatch) -> dict:
    _config(monkeypatch, tmp_path, description_suffix="Subscribe.", default_tags=["painforwisdom", "discipline"])
    video = tmp_path / "v.mp4"
    video.write_bytes(b"\0")
    monkeypatch.setattr(youtube_upload, "get_duration_seconds", lambda _: 42.04)
    rd = tmp_path / "rd"
    rd.mkdir()
    return {"run_dir": str(rd), "video_path": str(video), "video_date": "2026-11-03", "extraction_report": "R",
            "blog_post_title": "The blog title", "blog_post_text": "The blog body"}


def _rows(state: dict) -> list[dict]:
    return [json.loads(line) for line in (Path(state["run_dir"]) / "runs.jsonl").read_text().splitlines()]


def _uploader(monkeypatch) -> list[dict]:
    calls: list[dict] = []

    def fake(path, **kwargs):
        calls.append({"path": path, **kwargs})
        return SimpleNamespace(video_id="vid", url="https://youtu.be/vid")

    monkeypatch.setattr(adapter, "upload_draft_short", fake)
    return calls


# --- metadata node ---


def test_agent_sees_the_json_output_section_and_the_blog(state, monkeypatch):
    # The fake answers like the real model: labelled prose unless the prompt asks for the JSON keys.
    calls: list[dict] = []

    def fake(model, system, user, max_tokens):
        calls.append(user)
        return {"text": GOOD if "tags_extra" in system else LABELLED, "model": model, "stop_reason": "stop", **USAGE}

    monkeypatch.setattr(youtube_metadata, "call_llm", fake)
    out = youtube_metadata.node_youtube_metadata(state)
    written = json.loads(Path(out["youtube_metadata_path"]).read_text())
    assert written["title"] == "stop optimizing for ease" and written["warning"] is None
    assert written["description"] == "growth hides Subscribe."
    assert written["tags"] == ["painforwisdom", "discipline", "amcc"]  # defaults first, deduped case-blind
    assert len(calls) == 1 and "The blog title" in calls[0] and "The blog body" in calls[0]
    assert Path(out["youtube_metadata_path"]) == Path(state["run_dir"]) / "youtube-upload" / "metadata.json"


def test_c4_row_has_stage_and_summed_tokens_across_the_retry(state, monkeypatch):
    calls = _llm(monkeypatch, LABELLED, GOOD)
    youtube_metadata.node_youtube_metadata(state)
    row = _rows(state)[-1]
    assert len(calls) == 2 and row["stage"] == "youtube-metadata" and row["title"] == "stop optimizing for ease"
    assert (row["input_tokens"], row["output_tokens"], row["cache_read_tokens"], row["cache_creation_tokens"]) == (20, 4, 6, 8)
    assert row["cost_usd"] == 1.0 and row["llm_calls"] == 2 and "warning" not in row


@pytest.mark.parametrize("responses", [(LABELLED, LABELLED), (RuntimeError("boom"), "")])
def test_empty_title_after_one_retry_writes_a_warning_and_never_raises(state, monkeypatch, responses):
    calls = _llm(monkeypatch, *responses)
    out = youtube_metadata.node_youtube_metadata(state)
    written = json.loads(Path(out["youtube_metadata_path"]).read_text())
    assert len(calls) == 2 and written["title"] == "" and "empty title" in written["warning"]
    assert out["youtube_metadata_warning"] == written["warning"] and _rows(state)[-1]["warning"] == written["warning"]


@pytest.mark.parametrize("replies,stored", [
    ([(GOOD, "stop")], "stop"),
    ([(LABELLED, "stop"), (LABELLED, "length")], "length"),
    ([(LABELLED, "length"), RuntimeError("boom")], None),
])
def test_last_stop_reason_is_stored_in_metadata_and_c4(state, monkeypatch, replies, stored):
    answers = iter(replies)

    def fake(model, system, user, max_tokens):
        answer = next(answers)
        if isinstance(answer, Exception):
            raise answer
        return {"text": answer[0], "model": model, "stop_reason": answer[1], **USAGE}

    monkeypatch.setattr(youtube_metadata, "call_llm", fake)
    out = youtube_metadata.node_youtube_metadata(state)
    assert json.loads(Path(out["youtube_metadata_path"]).read_text())["stop_reason"] == stored
    assert _rows(state)[-1]["stop_reason"] == stored


def test_title_capped_at_100(state, monkeypatch):
    _llm(monkeypatch, json.dumps({"title": "x" * 150, "description": "", "tags_extra": []}))
    out = youtube_metadata.node_youtube_metadata(state)
    assert len(json.loads(Path(out["youtube_metadata_path"]).read_text())["title"]) == 100


def test_merged_tags_stay_under_the_youtube_total():
    tags = youtube_metadata.merge_tags(["a" * 300, "b" * 150], ["c" * 100, "d"])
    assert tags == ["a" * 300, "b" * 150, "d"]


@pytest.mark.parametrize("change,reason", [({"validator_verdict": "FAIL"}, "validator FAIL"), ({"video_path": ""}, "no video")])
def test_nothing_to_upload_skips_without_a_call(state, monkeypatch, change, reason):
    calls = _llm(monkeypatch)
    out = youtube_metadata.node_youtube_metadata({**state, **change})
    assert not calls and out == {"youtube_skipped": True, "youtube_skip_reason": reason}
    assert _rows(state)[-1] == {**_rows(state)[-1], "stage": "youtube-metadata", "skipped": True, "reason": reason}


# --- upload node: the mode table in spec §1.3 ---


def _with_metadata(state: dict, **metadata) -> dict:
    path = Path(state["run_dir"]) / "youtube-upload" / "metadata.json"
    path.parent.mkdir(exist_ok=True)
    path.write_text(json.dumps({"title": "T", "description": "D", "tags": ["a"], "warning": None, **metadata}))
    return {**state, "youtube_metadata_path": str(path)}


def test_off_writes_no_package(state, monkeypatch, tmp_path):
    _config(monkeypatch, tmp_path, mode="off")
    out = youtube_upload.node_youtube_upload(_with_metadata(state))
    assert out == {"youtube_skipped": True, "youtube_skip_reason": "off"}
    assert package.read(Path(state["run_dir"]), "youtube") is None
    assert _rows(state)[-1]["skipped"] is True and _rows(state)[-1]["reason"] == "off"


def test_manual_writes_a_pending_package_and_uploads_nothing(state, monkeypatch):
    uploads = _uploader(monkeypatch)
    out = youtube_upload.node_youtube_upload(_with_metadata(state))
    pkg = package.read(Path(state["run_dir"]), "youtube")
    assert out == {"youtube_skipped": True, "youtube_skip_reason": "manual"} and not uploads
    assert "youtube_metadata_path" not in out
    assert pkg["status"] == "pending" and pkg["mode"] == "manual"
    assert pkg["fields"] == {"title": "T", "description": "D", "tags": ["a"], "privacy_status": "private", "kind": "short"}
    assert pkg["readonly"] == {"duration_s": 42.0}
    assert pkg["assets"] == [{"role": "video", "path": state["video_path"]}]
    specs = pkg["field_specs"]
    assert (specs["title"]["max"], specs["title"]["required"], specs["description"]["max"]) == (100, True, 5000)
    assert specs["tags"]["max"] == 500
    assert specs["privacy_status"]["options"] == ["private", "unlisted", "public"]
    assert specs["kind"]["options"] == ["short", "video"]
    assert package.validate_fields(pkg, pkg["fields"]) == []
    assert _rows(state)[-1]["reason"] == "manual"


def test_empty_title_package_carries_the_warning_and_needs_the_user(state, monkeypatch):
    youtube_upload.node_youtube_upload(_with_metadata(state, title="", warning="empty title after 2 attempts"))
    pkg = package.read(Path(state["run_dir"]), "youtube")
    assert pkg["readonly"]["warning"] == "empty title after 2 attempts"
    assert package.validate_fields(pkg, pkg["fields"]) == ["title: required"]


def test_auto_publishes_in_process(state, monkeypatch, tmp_path):
    _config(monkeypatch, tmp_path, mode="auto", kind="video")
    uploads = _uploader(monkeypatch)
    out = youtube_upload.node_youtube_upload(_with_metadata(state))
    assert out == {"youtube_video_id": "vid", "youtube_url": "https://youtu.be/vid", "youtube_skipped": False}
    assert package.read(Path(state["run_dir"]), "youtube")["status"] == "published" and len(uploads) == 1
    assert _rows(state)[-1]["url"] == "https://youtu.be/vid"
    # A replay never uploads twice.
    assert youtube_upload.node_youtube_upload(_with_metadata(state))["youtube_url"] == "https://youtu.be/vid"
    assert len(uploads) == 1


def test_auto_failure_lands_in_the_package_and_the_row(state, monkeypatch, tmp_path):
    _config(monkeypatch, tmp_path, mode="auto")
    monkeypatch.setattr(adapter, "upload_draft_short", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("quota")))
    out = youtube_upload.node_youtube_upload(_with_metadata(state))
    assert out["youtube_skipped"] is True and "quota" in out["youtube_skip_reason"]
    assert package.read(Path(state["run_dir"]), "youtube")["status"] == "failed"
    assert "quota" in _rows(state)[-1]["error"]
    assert node_bv_youtube(out)["branch_verdict_youtube"] == "PARTIAL"


def test_unprobeable_video_still_builds_the_package(state, monkeypatch):
    monkeypatch.setattr(youtube_upload, "get_duration_seconds", lambda _: (_ for _ in ()).throw(OSError("no ffprobe")))
    youtube_upload.node_youtube_upload(_with_metadata(state))
    assert package.read(Path(state["run_dir"]), "youtube")["readonly"]["duration_s"] is None


@pytest.mark.parametrize("change,reason", [({"validator_verdict": "FAIL"}, "validator FAIL"), ({"video_path": ""}, "no video")])
def test_upload_skips_when_there_is_nothing_to_upload(state, change, reason):
    assert youtube_upload.node_youtube_upload({**state, **change}) == {"youtube_skipped": True, "youtube_skip_reason": reason}
    assert package.read(Path(state["run_dir"]), "youtube") is None


# --- adapter ---


def _package(tmp_path: Path, duration=42.0, **fields) -> dict:
    video = tmp_path / "v.mp4"
    video.write_bytes(b"\0")
    base = {"title": "T", "description": "D", "tags": ["a"], "privacy_status": "unlisted", "kind": "short"}
    return package.new_package("youtube", "manual", {**base, **fields}, {}, readonly={"duration_s": duration},
                               assets=[{"role": "video", "path": str(video)}])


def test_adapter_short_appends_shorts_tag_and_returns_the_result(tmp_path, monkeypatch):
    _config(monkeypatch, tmp_path, category_id="27", made_for_kids=True)
    uploads = _uploader(monkeypatch)
    assert adapter.publish(tmp_path, _package(tmp_path)) == {"id": "vid", "url": "https://youtu.be/vid"}
    call = uploads[0]
    assert call["description"] == "D #Shorts" and call["privacy_status"] == "unlisted" and call["title"] == "T"
    assert (call["category_id"], call["made_for_kids"], call["tags"]) == ("27", True, ["a"])
    assert call["path"] == tmp_path / "v.mp4"


def test_adapter_short_keeps_an_existing_shorts_tag(tmp_path, monkeypatch):
    _config(monkeypatch, tmp_path)
    uploads = _uploader(monkeypatch)
    adapter.publish(tmp_path, _package(tmp_path, description="D #shorts"))
    assert uploads[0]["description"] == "D #shorts"


def test_adapter_long_short_fails_with_a_clear_error(tmp_path, monkeypatch):
    _config(monkeypatch, tmp_path)
    uploads = _uploader(monkeypatch)
    with pytest.raises(ValueError, match="181.0 s.*180"):
        adapter.publish(tmp_path, _package(tmp_path, duration=181.0))
    assert not uploads


def test_adapter_video_uploads_as_is_at_any_length(tmp_path, monkeypatch):
    _config(monkeypatch, tmp_path)
    uploads = _uploader(monkeypatch)
    adapter.publish(tmp_path, _package(tmp_path, duration=900.0, kind="video"))
    assert uploads[0]["description"] == "D"


def test_adapter_refuses_an_empty_title(tmp_path, monkeypatch):
    _config(monkeypatch, tmp_path)
    uploads = _uploader(monkeypatch)
    with pytest.raises(ValueError, match="title"):
        adapter.publish(tmp_path, _package(tmp_path, title=" "))
    assert not uploads


def test_youtube_is_registered_and_reaches_the_adapter(state, monkeypatch):
    uploads = _uploader(monkeypatch)
    rd = Path(state["run_dir"])
    package.write(rd, _package(rd))
    assert "youtube" in publish_core.TARGETS
    done = publish_core.run_target(rd, "youtube")
    assert done["status"] == "published" and done["result"]["url"] == "https://youtu.be/vid" and len(uploads) == 1
    assert not package.lock_path(rd, "youtube").exists()


# --- branch validator ---


@pytest.mark.parametrize("update,verdict", [
    ({"youtube_url": "https://youtu.be/a"}, "PASS"),
    ({"youtube_skipped": True, "youtube_skip_reason": "off"}, "PASS"),
    ({"youtube_skipped": True, "youtube_skip_reason": "manual"}, "PASS"),
    ({"youtube_skipped": True, "youtube_skip_reason": "publish error: x"}, "PARTIAL"),
    ({}, "PARTIAL"),
])
def test_validator_modes(update, verdict):
    assert node_bv_youtube(update)["branch_verdict_youtube"] == verdict

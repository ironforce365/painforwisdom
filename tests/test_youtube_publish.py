"""YouTube metadata node, package and adapter (spec 2026-11-03-console-operability §7). The client is mocked."""
from __future__ import annotations

import json
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from pipeline import publish as publish_core
from pipeline import publishing_config, youtube_client
from pipeline.nodes import youtube_metadata, youtube_upload
from pipeline.nodes.validators.branch_youtube import node_bv_youtube
from pipeline.publish import package, specs, video_meta
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
    assert pkg["fields"] == {"title": "T", "description": "D", "tags": ["a"], "privacy_status": "private", "kind": "short",
                             "recording_date": "", "location": "", "publish_at": "", "audio_language": "en", "text_language": "en"}
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


def _edited(state: dict, status: str, error: str = None) -> dict:
    rd = Path(state["run_dir"])
    pkg = package.read(rd, "youtube")
    return package.write(rd, {**pkg, "status": status, "error": error, "fields": {**pkg["fields"], "title": "My edit"}})


@pytest.mark.parametrize("status,error", [("pending", None), ("failed", "quota")])
def test_a_replay_keeps_the_edits_of_a_package_at_rest(state, monkeypatch, status, error):  # [DT-P3] [DT-P5]
    uploads = _uploader(monkeypatch)
    prepared = _with_metadata(state)
    youtube_upload.node_youtube_upload(prepared)
    _edited(state, status, error)
    out = youtube_upload.node_youtube_upload(prepared)
    pkg = package.read(Path(state["run_dir"]), "youtube")
    assert pkg["fields"]["title"] == "My edit" and pkg["fields"]["description"] == "D"
    assert (pkg["status"], pkg["error"]) == (status, error)
    assert out == {"youtube_skipped": True, "youtube_skip_reason": "manual"} and not uploads


def test_a_replay_in_auto_mode_publishes_the_edit_once(state, monkeypatch, tmp_path):  # [DT-P5] [DT-P10]
    _config(monkeypatch, tmp_path, mode="auto")
    monkeypatch.setattr(adapter, "upload_draft_short", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("quota")))
    prepared = _with_metadata(state)
    youtube_upload.node_youtube_upload(prepared)
    _edited(state, "failed", "quota")
    uploads = _uploader(monkeypatch)
    out = youtube_upload.node_youtube_upload(prepared)
    assert out["youtube_url"] == "https://youtu.be/vid"
    assert [u["title"] for u in uploads] == ["My edit"]
    assert youtube_upload.node_youtube_upload(prepared)["youtube_url"] == "https://youtu.be/vid"
    assert len(uploads) == 1


def test_a_replay_never_auto_publishes_a_package_the_console_prepared(state, monkeypatch, tmp_path):  # [DT-P2]
    """Prepare marks its package manual whatever the config says: nothing publishes it behind the user's back."""
    uploads = _uploader(monkeypatch)
    prepared = _with_metadata(state)
    youtube_upload.node_youtube_upload(prepared)  # config manual: mode manual, as prepare writes it
    _config(monkeypatch, tmp_path, mode="auto")
    out = youtube_upload.node_youtube_upload(prepared)
    assert out == {"youtube_skipped": True, "youtube_skip_reason": "manual"} and not uploads
    assert package.read(Path(state["run_dir"]), "youtube")["mode"] == "manual"


def test_a_replay_leaves_a_publish_in_progress_alone(state, monkeypatch, tmp_path):  # [DT-P4]
    _config(monkeypatch, tmp_path, mode="auto")
    uploads = _uploader(monkeypatch)
    prepared = _with_metadata(state)
    rd = Path(state["run_dir"])
    package.write(rd, package.new_package("youtube", "auto", {"title": "My edit"}, {}))
    package.write(rd, {**package.read(rd, "youtube"), "status": "publishing"})
    before = package.package_path(rd, "youtube").read_bytes()
    out = youtube_upload.node_youtube_upload(prepared)
    assert out == {"youtube_skipped": True, "youtube_skip_reason": "publishing"} and not uploads
    assert package.package_path(rd, "youtube").read_bytes() == before
    assert _rows(state)[-1]["reason"] == "publishing"
    assert node_bv_youtube(out)["branch_verdict_youtube"] != "FAIL"


def test_a_replay_with_publishing_switched_off_leaves_the_package_alone(state, monkeypatch, tmp_path):  # [DT-P1]
    prepared = _with_metadata(state)
    youtube_upload.node_youtube_upload(prepared)
    _edited(state, "pending")
    before = package.package_path(Path(state["run_dir"]), "youtube").read_bytes()
    _config(monkeypatch, tmp_path, mode="off")
    assert youtube_upload.node_youtube_upload(prepared)["youtube_skip_reason"] == "off"
    assert package.package_path(Path(state["run_dir"]), "youtube").read_bytes() == before


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


def test_adapter_measures_a_prepared_package_and_refuses_a_long_short(tmp_path, monkeypatch):  # [DT-P2] [DT-P5]
    """A prepared package records no duration: the 180 s check must still run, never upload it tagged #Shorts."""
    uploads = _uploader(monkeypatch)
    seen = []
    monkeypatch.setattr(adapter, "get_duration_seconds", lambda path: seen.append(path) or 600.5)
    pkg = _package(tmp_path, duration=None)
    with pytest.raises(ValueError, match=r"600\.5 s.*set kind to video"):
        adapter.publish(tmp_path, pkg)
    assert not uploads and seen == [Path(pkg["assets"][0]["path"])]


def test_adapter_uploads_a_measured_short_tagged(tmp_path, monkeypatch):  # [DT-P4]
    uploads = _uploader(monkeypatch)
    monkeypatch.setattr(adapter, "get_duration_seconds", lambda _: 59.0)
    adapter.publish(tmp_path, _package(tmp_path, duration=None))
    assert len(uploads) == 1 and uploads[0]["description"].endswith("#Shorts")


def test_adapter_unmeasurable_video_uploads_with_a_warning(tmp_path, monkeypatch, capsys):  # [DT-P4]
    uploads = _uploader(monkeypatch)
    monkeypatch.setattr(adapter, "get_duration_seconds", lambda _: (_ for _ in ()).throw(OSError("no ffprobe")))
    adapter.publish(tmp_path, _package(tmp_path, duration=None))
    assert len(uploads) == 1
    out = capsys.readouterr().out
    assert "WARN" in out and "no ffprobe" in out and "180" in out


def test_adapter_uses_a_recorded_duration_without_measuring(tmp_path, monkeypatch):  # [DT-P4]
    uploads = _uploader(monkeypatch)
    monkeypatch.setattr(adapter, "get_duration_seconds", lambda _: pytest.fail("already known"))
    adapter.publish(tmp_path, _package(tmp_path, duration=42.0))
    assert len(uploads) == 1


def test_adapter_does_not_measure_for_a_plain_video(tmp_path, monkeypatch):  # [DT-P4]
    uploads = _uploader(monkeypatch)
    monkeypatch.setattr(adapter, "get_duration_seconds", lambda _: pytest.fail("no limit to check"))
    adapter.publish(tmp_path, _package(tmp_path, duration=None, kind="video"))
    assert len(uploads) == 1


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


# --- recording date, location, languages ---


def _probe(monkeypatch, tags=None, raises=None):
    def run(argv, **_):
        if raises:
            raise raises
        return SimpleNamespace(returncode=0, stdout=json.dumps({"format": {"tags": tags or {}}}))

    monkeypatch.setattr(video_meta.subprocess, "run", run)


def test_recording_date_is_the_local_day_and_location_is_coarse(monkeypatch):  # [DT-P3]
    """A phone records UTC: an evening run must not land on the next day. 2 decimals is about 1 km, not a front door."""
    monkeypatch.setenv("TZ", "America/Los_Angeles")
    time.tzset()
    _probe(monkeypatch, {"creation_time": "2026-09-03T01:30:00.000000Z", "location": "+33.1750-117.1864/"})
    try:
        assert video_meta.recorded(Path("v.mp4")) == {"recording_date": "2026-09-02", "location": "33.17, -117.19"}
    finally:
        monkeypatch.delenv("TZ")
        time.tzset()


@pytest.mark.parametrize("tags,raises", [({}, None), ({"creation_time": "nonsense", "location": "nowhere"}, None),
                                         (None, FileNotFoundError("ffprobe"))])
def test_a_video_without_usable_tags_leaves_them_for_the_form(monkeypatch, tags, raises):  # [DT-P3]
    _probe(monkeypatch, tags, raises)
    assert video_meta.recorded(Path("v.mp4")) == {"recording_date": "", "location": ""}


def test_unreadable_probe_output_leaves_them_for_the_form(monkeypatch):  # [DT-P3]
    monkeypatch.setattr(video_meta.subprocess, "run", lambda *a, **k: SimpleNamespace(returncode=1, stdout="not json"))
    assert video_meta.recorded(Path("v.mp4")) == {"recording_date": "", "location": ""}


def test_the_adapter_sends_recording_details_and_languages(tmp_path, monkeypatch):  # [DT-P4]
    uploads = _uploader(monkeypatch)
    adapter.publish(tmp_path, _package(tmp_path, recording_date="2026-09-02", location="33.18, -117.19",
                                       audio_language="en", text_language="es"))
    call = uploads[0]
    assert call["recording_date"] == "2026-09-02T00:00:00.000Z"
    assert call["location"] == (33.18, -117.19)
    assert call["default_audio_language"] == "en" and call["default_language"] == "es"


def test_a_package_older_than_these_fields_still_publishes(tmp_path, monkeypatch):  # [DT-P4]
    uploads = _uploader(monkeypatch)
    adapter.publish(tmp_path, _package(tmp_path))
    call = uploads[0]
    assert call["recording_date"] is None and call["location"] is None
    assert call["default_audio_language"] == "" and call["default_language"] == ""


@pytest.mark.parametrize("fields,problem", [
    ({"recording_date": "09/02/2026"}, "recording_date"),
    ({"location": "somewhere"}, "location"),
    ({"location": "133.18, -117.19"}, "location"),
    ({"location": "33.18, -217.19"}, "location"),
])
def test_a_bad_value_stops_before_anything_is_uploaded(tmp_path, monkeypatch, fields, problem):  # [DT-P5]
    uploads = _uploader(monkeypatch)
    with pytest.raises(ValueError, match=problem):
        adapter.publish(tmp_path, _package(tmp_path, **fields))
    assert not uploads


def test_DT_P14_a_publish_time_schedules_a_private_video_for_then(tmp_path, monkeypatch):
    """[DT-P14] The form's local time becomes YouTube's UTC publishAt; YouTube schedules only a private video."""
    uploads = _uploader(monkeypatch)
    later = (datetime.now() + timedelta(days=1)).replace(second=0, microsecond=0)
    value = later.strftime("%Y-%m-%dT%H:%M")
    result = adapter.publish(tmp_path, _package(tmp_path, publish_at=value, privacy_status="public"))
    call = uploads[0]
    assert call["privacy_status"] == "private"
    assert call["publish_at"] == later.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    assert result["publish_at"] == value


def test_DT_P14_no_publish_time_publishes_now_with_the_chosen_privacy(tmp_path, monkeypatch):
    """[DT-P14]"""
    uploads = _uploader(monkeypatch)
    result = adapter.publish(tmp_path, _package(tmp_path, privacy_status="public"))
    assert uploads[0]["privacy_status"] == "public" and uploads[0]["publish_at"] is None
    assert "publish_at" not in result


@pytest.mark.parametrize("value,problem", [("2020-01-01T09:00", "has passed"), ("tomorrow", "not YYYY-MM-DDTHH:MM")])
def test_DT_P14_a_past_or_bad_time_stops_before_anything_is_uploaded(tmp_path, monkeypatch, value, problem):
    """[DT-P14] YouTube refuses a past publishAt: say so here, with nothing uploaded."""
    uploads = _uploader(monkeypatch)
    with pytest.raises(ValueError, match=problem):
        adapter.publish(tmp_path, _package(tmp_path, publish_at=value))
    assert not uploads


@pytest.mark.parametrize("value,ok", [("", True), ("2026-11-08T09:30", True), ("2026-11-08", False), ("09:30", False)])
def test_DT_P14_the_field_takes_what_the_date_and_time_picker_gives(value, ok):
    """[DT-P14]"""
    pkg = {"field_specs": {"publish_at": specs.YOUTUBE["publish_at"]}}
    assert (package.validate_fields(pkg, {"publish_at": value}) == []) is ok


def test_DT_P14_the_client_sends_publish_at(tmp_path, monkeypatch):
    """[DT-P14]"""
    sent = _client_upload(monkeypatch, tmp_path, privacy_status="private", publish_at="2026-11-08T17:30:00.000Z")
    assert sent["body"]["status"] == {"privacyStatus": "private", "selfDeclaredMadeForKids": False, "publishAt": "2026-11-08T17:30:00.000Z"}


def _client_upload(monkeypatch, tmp_path, **kwargs) -> dict:
    sent: dict = {}

    class Request:
        def next_chunk(self):
            return None, {"id": "vid", "snippet": {}, "recordingDetails": {}}

    def insert(part, body, media_body):
        sent.update(part=part, body=body)
        return Request()

    service = SimpleNamespace(videos=lambda: SimpleNamespace(insert=insert))
    monkeypatch.setattr(youtube_client, "build_youtube_service", lambda: service)
    video = tmp_path / "v.mp4"
    video.write_bytes(b"\0")
    youtube_client.upload_draft_short(video, title="T", description="D", tags=[], **kwargs)
    return sent


def test_the_client_asks_youtube_to_store_them(tmp_path, monkeypatch):  # [DT-P4]
    sent = _client_upload(monkeypatch, tmp_path, recording_date="2026-09-02T00:00:00.000Z", location=(33.18, -117.19),
                          default_language="es", default_audio_language="en")
    assert sent["part"] == "snippet,status,recordingDetails"
    assert sent["body"]["recordingDetails"] == {"recordingDate": "2026-09-02T00:00:00.000Z",
                                                "location": {"latitude": 33.18, "longitude": -117.19}}
    assert sent["body"]["snippet"]["defaultLanguage"] == "es"
    assert sent["body"]["snippet"]["defaultAudioLanguage"] == "en"


def test_the_client_sends_nothing_for_what_is_empty(tmp_path, monkeypatch):  # [DT-P4]
    sent = _client_upload(monkeypatch, tmp_path)
    assert sent["part"] == "snippet,status" and "recordingDetails" not in sent["body"]
    assert "publishAt" not in sent["body"]["status"]
    assert "defaultLanguage" not in sent["body"]["snippet"] and "defaultAudioLanguage" not in sent["body"]["snippet"]


def test_the_package_languages_follow_the_run(state, monkeypatch):  # [DT-S6]
    _uploader(monkeypatch)
    youtube_upload.node_youtube_upload(_with_metadata({**state, "language": "es"}))
    fields = package.read(Path(state["run_dir"]), "youtube")["fields"]
    assert fields["audio_language"] == "es" and fields["text_language"] == "es"

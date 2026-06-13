"""detect_validation_signals: incoming user text vs open validations, fail-safe."""
import eval.grounding.integration as integration
from eval.grounding.corpus import RegressionCorpus
from eval.grounding.validations import PendingValidations


def _seed_open(vdir):
    pv = PendingValidations(vdir)
    pv.open({
        "ts": "2026-06-10T23:00:00", "thread_id": "t1", "user_id": "u1",
        "claim_id": "c3", "claim_text": "You were punishing yourself.",
        "claim_type": "interpretation", "confidence": 3,
        "action": "demote", "question": "Were you punishing yourself?",
    })
    return pv


def test_disabled_flag_returns_empty(tmp_path, monkeypatch):
    monkeypatch.delenv("COACH_GROUNDING_GATE", raising=False)
    monkeypatch.setenv("COACH_VALIDATIONS_DIR", str(tmp_path / "v"))
    _seed_open(tmp_path / "v")
    assert integration.detect_validation_signals("no!", thread_id="t1", user_id="u1") == []


def test_corrected_logs_correction_and_resolves(tmp_path, monkeypatch):
    monkeypatch.setenv("COACH_GROUNDING_GATE", "1")
    monkeypatch.setenv("COACH_VALIDATIONS_DIR", str(tmp_path / "v"))
    monkeypatch.setenv("COACH_GROUNDING_CORPUS_DIR", str(tmp_path / "c"))
    _seed_open(tmp_path / "v")
    monkeypatch.setattr(integration, "detect", lambda text, items, **kw: [
        {"claim_id": "c3", "outcome": "corrected",
         "correction_text": "No — I was banking miles before the storm."},
    ])
    out = integration.detect_validation_signals("No — banking miles before the storm.",
                                                thread_id="t1", user_id="u1")
    assert out[0]["outcome"] == "corrected"
    # corpus got a correction signal
    recs = RegressionCorpus(tmp_path / "c").load(signal="correction")
    assert len(recs) == 1
    assert recs[0]["claim_id"] == "c3"
    assert "banking miles" in recs[0]["user_correction"]
    assert recs[0]["ts"]  # runtime stamped
    # item closed
    assert PendingValidations(tmp_path / "v").list_open("t1") == []


def test_confirmed_logs_validation_signal(tmp_path, monkeypatch):
    monkeypatch.setenv("COACH_GROUNDING_GATE", "1")
    monkeypatch.setenv("COACH_VALIDATIONS_DIR", str(tmp_path / "v"))
    monkeypatch.setenv("COACH_GROUNDING_CORPUS_DIR", str(tmp_path / "c"))
    _seed_open(tmp_path / "v")
    monkeypatch.setattr(integration, "detect", lambda text, items, **kw: [
        {"claim_id": "c3", "outcome": "confirmed", "correction_text": ""},
    ])
    integration.detect_validation_signals("yes exactly", thread_id="t1", user_id="u1")
    recs = RegressionCorpus(tmp_path / "c").load(signal="validation")
    assert len(recs) == 1
    assert PendingValidations(tmp_path / "v").list_open("t1") == []


def test_unaddressed_stays_open_no_corpus_record(tmp_path, monkeypatch):
    monkeypatch.setenv("COACH_GROUNDING_GATE", "1")
    monkeypatch.setenv("COACH_VALIDATIONS_DIR", str(tmp_path / "v"))
    monkeypatch.setenv("COACH_GROUNDING_CORPUS_DIR", str(tmp_path / "c"))
    _seed_open(tmp_path / "v")
    monkeypatch.setattr(integration, "detect", lambda text, items, **kw: [
        {"claim_id": "c3", "outcome": "unaddressed", "correction_text": ""},
    ])
    integration.detect_validation_signals("what's a good warmup?", thread_id="t1", user_id="u1")
    assert RegressionCorpus(tmp_path / "c").load() == []
    assert len(PendingValidations(tmp_path / "v").list_open("t1")) == 1


def test_detector_blowup_is_swallowed(tmp_path, monkeypatch):
    monkeypatch.setenv("COACH_GROUNDING_GATE", "1")
    monkeypatch.setenv("COACH_VALIDATIONS_DIR", str(tmp_path / "v"))
    _seed_open(tmp_path / "v")

    def boom(*a, **kw):
        raise RuntimeError("llm down")
    monkeypatch.setattr(integration, "detect", boom)
    out = integration.detect_validation_signals("hi", thread_id="t1", user_id="u1")
    assert out == []  # never breaks a live turn

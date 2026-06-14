"""Doctrine distillation: turn raw vault entries into transferable, DE-PERSONALISED
principles. The QA gate (`is_depersonalized`) is trust-critical — it's what keeps
biography ('four months of recovery') out of the doctrine the coach grounds on.
"""
import json

import doctrine.distill as dd


# ---- is_depersonalized (the QA gate) --------------------------------------

def test_rejects_english_first_person():
    assert not dd.is_depersonalized("I took four months to recover.")
    assert not dd.is_depersonalized("My Achilles flared after the race.")
    assert not dd.is_depersonalized("That taught me to back off.")
    assert not dd.is_depersonalized("We learned to rest sooner.")


def test_rejects_spanish_first_person():
    assert not dd.is_depersonalized("Me costumbré a este tipo de dolor.")
    assert not dd.is_depersonalized("Yo corrí con dolor durante meses.")
    assert not dd.is_depersonalized("Mi tendón tardó en sanar.")


def test_rejects_author_name():
    assert not dd.is_depersonalized("Gonzalo learned to read the signal.")


def test_accepts_impersonal_and_second_person_principles():
    assert dd.is_depersonalized("Pain that quiets under load can mask accumulating damage.")
    assert dd.is_depersonalized("You grow by confronting fear, not avoiding it.")
    assert dd.is_depersonalized("Recovery is a strategy, not a concession.")


def test_does_not_false_positive_on_substrings():
    # 'came', 'mine' style substrings must not trip the word-boundary check
    assert dd.is_depersonalized("Improvement came from consistency over intensity.")


# ---- extract_principles ---------------------------------------------------

def test_extract_filters_contaminated(monkeypatch):
    fake = json.dumps({"principles": [
        {"text": "Pain before and after effort but silent during it can signal damage.", "theme": "body-literacy"},
        {"text": "I learned this over four months of recovery.", "theme": "recovery"},
    ]})
    monkeypatch.setattr(dd, "call_llm", lambda **kw: fake)
    out = dd.extract_principles("raw entry text", model="m", source_slug="entry-1")
    texts = [p.text for p in out]
    assert "Pain before and after effort but silent during it can signal damage." in texts
    assert all("four months of recovery" not in t for t in texts)  # contaminated dropped
    assert out[0].theme == "body-literacy"
    assert out[0].source_slug == "entry-1"


def test_extract_malformed_json_returns_empty(monkeypatch):
    monkeypatch.setattr(dd, "call_llm", lambda **kw: "not json at all")
    assert dd.extract_principles("x", model="m") == []


def test_extract_empty_text_no_llm_call(monkeypatch):
    called = {"n": 0}

    def boom(**kw):
        called["n"] += 1
        return "{}"

    monkeypatch.setattr(dd, "call_llm", boom)
    assert dd.extract_principles("   ", model="m") == []
    assert called["n"] == 0

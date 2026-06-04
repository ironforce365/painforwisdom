"""Crisis filter intercepts trigger phrases and emits canned reply."""
from __future__ import annotations
from crisis.filter import check_message, CrisisHit


def test_clean_message_passes():
    assert check_message("how do I push through pain today") is None


def test_english_trigger_matches():
    hit = check_message("I want to die")
    assert isinstance(hit, CrisisHit)
    assert "988" in hit.canned_reply


def test_spanish_trigger_matches():
    hit = check_message("últimamente quiero quitarme la vida")
    assert hit is not None


def test_case_insensitive():
    assert check_message("KILL MYSELF") is not None

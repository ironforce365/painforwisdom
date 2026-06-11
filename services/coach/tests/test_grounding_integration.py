import eval.grounding.integration as integ
from eval.grounding.types import ClaimType, Verdict


def test_flag_off_returns_reply_unchanged(monkeypatch):
    monkeypatch.delenv("COACH_GROUNDING_GATE", raising=False)
    reply = "[[claim id=c1 type=fact]] You were punishing yourself."
    out = integ.maybe_gate(reply, sources=[], user_id="u", thread_id="t")
    assert out == reply  # untouched when OFF


def test_flag_on_gates(monkeypatch, tmp_path):
    monkeypatch.setenv("COACH_GROUNDING_GATE", "1")
    monkeypatch.setenv("COACH_GROUNDING_CORPUS_DIR", str(tmp_path / "corpus"))
    monkeypatch.setattr(
        integ, "judge_claims",
        lambda claims, srcs: [Verdict("c1", ClaimType.FACT, grounded=False, contradicts=False, rationale="")],
    )
    monkeypatch.setattr(integ, "demote_to_question", lambda c, s: "Were you punishing yourself?")
    out = integ.maybe_gate(
        "[[claim id=c1 type=fact]] You were punishing yourself.",
        sources=[], user_id="u", thread_id="t",
    )
    assert out == "Were you punishing yourself?"


def test_gate_failure_falls_back_to_ungated(monkeypatch):
    monkeypatch.setenv("COACH_GROUNDING_GATE", "1")

    def boom(claims, srcs):
        raise RuntimeError("judge down")

    monkeypatch.setattr(integ, "judge_claims", boom)
    reply = "[[claim id=c1 type=fact]] Something."
    # must not raise into the live turn; returns the original reply on error
    assert integ.maybe_gate(reply, sources=[], user_id="u", thread_id="t") == reply


def _fake_chat_factory(reply: str):
    async def fake_chat(user_id, text):
        return (reply, ["some-slug"])

    return fake_chat


def test_turn_endpoint_unchanged_when_flag_off(monkeypatch, tmp_path):
    """The critical safety property: flag OFF => /turn behaves exactly as before."""
    import pytest

    pytest.importorskip("claude_agent_sdk")  # service import needs the SDK + fastapi
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient

    import agent.service as svc

    monkeypatch.setenv("COACH_INBOX_ROOT", str(tmp_path))
    monkeypatch.delenv("COACH_GROUNDING_GATE", raising=False)
    monkeypatch.setenv("COACH_DEBUG", "0")  # canary footer off: assert the bare reply
    reply = "[[claim id=c1 type=fact]] You were punishing yourself."
    monkeypatch.setattr(svc, "_chat_with_agent", _fake_chat_factory(reply))
    r = TestClient(svc.app).post("/turn", json={"user_id": "1", "text": "hi"})
    assert r.status_code == 200
    assert r.json()["reply"] == reply  # untouched


def test_turn_endpoint_gates_when_flag_on(monkeypatch, tmp_path):
    import pytest

    pytest.importorskip("claude_agent_sdk")
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient

    import agent.service as svc
    import eval.grounding.integration as integ
    from eval.grounding.types import ClaimType, Verdict

    monkeypatch.setenv("COACH_INBOX_ROOT", str(tmp_path))
    monkeypatch.setenv("COACH_GROUNDING_GATE", "1")
    monkeypatch.setenv("COACH_DEBUG", "0")  # canary footer off: assert the bare gated reply
    monkeypatch.setenv("COACH_GROUNDING_CORPUS_DIR", str(tmp_path / "corpus"))
    monkeypatch.setattr(
        svc, "_chat_with_agent",
        _fake_chat_factory("[[claim id=c1 type=fact]] You were punishing yourself."),
    )
    monkeypatch.setattr(
        integ, "judge_claims",
        lambda c, s: [Verdict("c1", ClaimType.FACT, grounded=False, contradicts=False, rationale="")],
    )
    monkeypatch.setattr(integ, "demote_to_question", lambda c, s: "Were you punishing yourself?")
    r = TestClient(svc.app).post("/turn", json={"user_id": "1", "text": "hi"})
    assert r.status_code == 200
    assert r.json()["reply"] == "Were you punishing yourself?"

"""Service wiring for the doctrine/memory world (Stream 3). Runs in docker/CI
(service import needs fastapi + claude_agent_sdk).

Asserts: flag-ON injects <doctrine> + <about_this_user>, builds D1 (doctrine) /
M1 (memory) typed sources, writes conversation-only memory, and the gate demotes
a doctrine-only biographical fact while keeping a memory-grounded one.
"""
import pytest

pytest.importorskip("claude_agent_sdk")
pytest.importorskip("fastapi")


def _composing_chat_factory(svc, reply: str):
    """Fake _chat_with_agent that — like the real one — runs _compose_turn_prompt
    first, so the doctrine/memory ContextVars are populated before the gate reads
    them via _slugs_to_sources."""
    async def fake_chat(user_id, text):
        svc._compose_turn_prompt(user_id, text)
        return (reply, ["body-literacy"])

    return fake_chat


def _wire_retrieval(monkeypatch, svc):
    monkeypatch.setattr(
        svc, "retrieve_doctrine_for_turn",
        lambda text: ("<doctrine>\nteaching material\nPain that quiets under load can mask damage.\n</doctrine>",
                      ["body-literacy"], "Pain that quiets under load can mask damage."),
    )
    monkeypatch.setattr(
        svc, "read_user_memory",
        lambda user_id, text, **kw: ("<about_this_user>\nThey said the Achilles bugs them all week.\n</about_this_user>",
                                     "They said the Achilles bugs them all week."),
    )


def test_compose_injects_doctrine_and_memory_blocks(monkeypatch):
    import agent.service as svc

    monkeypatch.setenv("COACH_GROUNDING_GATE", "1")
    _wire_retrieval(monkeypatch, svc)
    query, slugs = svc._compose_turn_prompt("123", "my achilles hurts")
    assert "<doctrine>" in query and "<about_this_user>" in query
    assert "my achilles hurts" in query
    assert slugs == ["body-literacy"]


def test_slugs_to_sources_builds_typed_D1_M1(monkeypatch):
    import agent.service as svc
    from eval.grounding.types import KIND_DOCTRINE, KIND_MEMORY

    monkeypatch.setenv("COACH_GROUNDING_GATE", "1")
    _wire_retrieval(monkeypatch, svc)
    svc._compose_turn_prompt("123", "q")  # populates the doctrine/memory ContextVars
    sources = svc._slugs_to_sources(["body-literacy"])
    by_id = {s.id: s for s in sources}
    assert by_id["D1"].kind == KIND_DOCTRINE and by_id["D1"].tier == 2
    assert by_id["M1"].kind == KIND_MEMORY and by_id["M1"].tier == 1
    assert "mask damage" in by_id["D1"].text
    assert "Achilles bugs them" in by_id["M1"].text


def test_flag_off_uses_legacy_vault_path(monkeypatch):
    import agent.service as svc

    monkeypatch.delenv("COACH_GROUNDING_GATE", raising=False)
    monkeypatch.setattr(svc, "retrieve_for_turn_rich", lambda text: ("<vault_context>\nx\n</vault_context>", ["s1"], {"s1": "x"}))
    # doctrine path must NOT be taken when flag off
    monkeypatch.setattr(svc, "retrieve_doctrine_for_turn", lambda text: (_ for _ in ()).throw(AssertionError("doctrine called with flag off")))
    query, slugs = svc._compose_turn_prompt("123", "q")
    assert "<vault_context>" in query and "<doctrine>" not in query
    assert slugs == ["s1"]


def test_turn_writes_conversation_only_memory(monkeypatch, tmp_path):
    from fastapi.testclient import TestClient

    import agent.service as svc
    import eval.grounding.integration as integ
    from eval.grounding.types import ClaimType, Verdict

    monkeypatch.setenv("COACH_INBOX_ROOT", str(tmp_path))
    monkeypatch.setenv("COACH_GROUNDING_GATE", "1")
    monkeypatch.setenv("COACH_DEBUG", "0")
    monkeypatch.setenv("COACH_GROUNDING_CORPUS_DIR", str(tmp_path / "corpus"))
    monkeypatch.setenv("COACH_VALIDATIONS_DIR", str(tmp_path / "v"))
    _wire_retrieval(monkeypatch, svc)

    writes = []
    monkeypatch.setattr(svc, "write_user_memory", lambda user_id, text, **kw: writes.append((user_id, text)))
    monkeypatch.setattr(svc, "_chat_with_agent", _composing_chat_factory(svc, "[[claim id=c1 type=conceptual cite=D1]] Pain can mask damage."))
    monkeypatch.setattr(
        integ, "judge_claims",
        lambda c, s: [Verdict("c1", ClaimType.CONCEPTUAL, grounded=True, grounded_by=["D1"], contradicts=False, rationale="")],
    )
    r = TestClient(svc.app).post("/turn", json={"user_id": "123", "text": "the achilles hurts"})
    assert r.status_code == 200
    # conversation-only: the user's OWN words were written, nothing else
    assert writes == [("123", "the achilles hurts")]


def test_turn_demotes_doctrine_only_fact_end_to_end(monkeypatch, tmp_path):
    from fastapi.testclient import TestClient

    import agent.service as svc
    import eval.grounding.integration as integ
    from eval.grounding.types import ClaimType, Verdict

    monkeypatch.setenv("COACH_INBOX_ROOT", str(tmp_path))
    monkeypatch.setenv("COACH_GROUNDING_GATE", "1")
    monkeypatch.setenv("COACH_DEBUG", "0")
    monkeypatch.setenv("COACH_GROUNDING_CORPUS_DIR", str(tmp_path / "corpus"))
    monkeypatch.setenv("COACH_VALIDATIONS_DIR", str(tmp_path / "v"))
    _wire_retrieval(monkeypatch, svc)
    monkeypatch.setattr(svc, "write_user_memory", lambda *a, **k: None)
    # coach asserts a biographical fact warranted only by doctrine -> must demote
    monkeypatch.setattr(
        svc, "_chat_with_agent",
        _composing_chat_factory(svc, "[[claim id=c1 type=fact cite=D1]] That cost you four months of recovery."),
    )
    monkeypatch.setattr(
        integ, "judge_claims",
        lambda c, s: [Verdict("c1", ClaimType.FACT, grounded=True, grounded_by=["D1"], contradicts=False, rationale="doctrine only")],
    )
    monkeypatch.setattr(integ, "demote_to_question", lambda c, s: "Has a flare like this cost you a long recovery before?")
    r = TestClient(svc.app).post("/turn", json={"user_id": "123", "text": "achilles flaring"})
    assert r.status_code == 200
    body = r.json()["reply"]
    assert "four months of recovery" not in body
    assert "Has a flare like this cost you a long recovery before?" in body

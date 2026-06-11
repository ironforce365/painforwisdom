from eval.grounding.segmenter import segment
from eval.grounding.types import ClaimType


def test_parses_tagged_claims():
    draft = (
        "[[claim id=c1 type=fact cite=S1,S2]] You missed Thursday and Friday.\n"
        "[[claim id=c2 type=interpretation conf=7]] This reads like self-punishment.\n"
        "Plain line with no tag.\n"
        "[[claim id=c3 type=conceptual cite=F1]] Hormesis is adaptation to mild stress."
    )
    claims, passthrough = segment(draft)
    assert [c.id for c in claims] == ["c1", "c2", "c3"]
    assert claims[0].type is ClaimType.FACT and claims[0].cites == ["S1", "S2"]
    assert claims[1].type is ClaimType.INTERPRETATION
    assert claims[1].confidence == 7 and claims[1].cites == []
    assert claims[2].cites == ["F1"]
    assert "Plain line with no tag." in passthrough


def test_uncited_fact_has_empty_cites():
    claims, _ = segment("[[claim id=c1 type=fact]] You were punishing yourself.")
    assert claims[0].cites == []  # fail-safe: treated as ungrounded downstream
    assert claims[0].text == "You were punishing yourself."


def test_no_claims_all_passthrough():
    claims, passthrough = segment("Hello.\nHow did the week go?")
    assert claims == []
    assert "Hello." in passthrough and "How did the week go?" in passthrough

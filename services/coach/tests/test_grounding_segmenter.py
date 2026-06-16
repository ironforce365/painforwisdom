from eval.grounding.segmenter import segment, strip_claim_tags
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


def test_midline_tag_is_lifted_into_a_judged_claim():
    # The coach violated the one-per-line contract and buried a tag mid-sentence.
    # It must STILL be parsed as a claim (so the gate judges it), with the text
    # before it dropping to passthrough — not the whole line leaking through.
    draft = "It provides shelter, but freedom is the price. [[claim id=c1 type=interpretation conf=7]] You're already aware you're in one."
    claims, passthrough = segment(draft)
    assert [c.id for c in claims] == ["c1"]
    assert claims[0].type is ClaimType.INTERPRETATION and claims[0].confidence == 7
    assert claims[0].text == "You're already aware you're in one."
    assert "It provides shelter, but freedom is the price." in passthrough
    # And nothing tag-shaped survives anywhere.
    assert "[[claim" not in passthrough


def test_multiple_midline_tags_on_one_line_all_lifted():
    draft = "Intro. [[claim id=c1 type=fact cite=M1]] You ran Tuesday. [[claim id=c2 type=interpretation conf=5]] You're avoiding hills."
    claims, _ = segment(draft)
    assert [c.id for c in claims] == ["c1", "c2"]
    assert claims[0].text == "You ran Tuesday."
    assert claims[1].text == "You're avoiding hills."


def test_strip_claim_tags_removes_midline_markers():
    # The exact leaked Telegram message: tags buried mid-line must be scrubbed,
    # claim text preserved.
    leaked = (
        "Not a comfort zone — a cage. It provides shelter, but freedom is the "
        "price. [[claim id=c1 type=interpretation conf=7]] You're already aware "
        "you're in one.\nEvery avoided discomfort is a silent withdrawal. "
        "[[claim id=c2 type=fact cite=M1]] A 100-miler will demand that account be full."
    )
    cleaned = strip_claim_tags(leaked)
    assert "[[claim" not in cleaned
    assert "You're already aware you're in one." in cleaned
    assert "A 100-miler will demand that account be full." in cleaned

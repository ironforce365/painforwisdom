from eval.grounding.fixtures import load_fixtures


def test_fixtures_load_and_have_expectations():
    fx = load_fixtures()
    ids = {f["id"] for f in fx}
    assert "f000_punishing_yourself" in ids
    f000 = next(f for f in fx if f["id"] == "f000_punishing_yourself")
    assert "c2" in f000["expect"]["demote"]
    assert all({"sources", "draft", "expect"} <= set(f) for f in fx)


def test_every_fixture_claim_has_one_expectation():
    from eval.grounding.segmenter import segment

    for f in load_fixtures():
        claims, _ = segment(f["draft"])
        labelled = set(f["expect"].get("assert", []) + f["expect"].get("demote", []) + f["expect"].get("state_as_read", []))
        for c in claims:
            assert c.id in labelled, f"{f['id']}: claim {c.id} has no expectation"

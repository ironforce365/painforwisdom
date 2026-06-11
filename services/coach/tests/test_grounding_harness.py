from eval.grounding.harness import run_all, score_fixture
from eval.grounding.types import ClaimType, Verdict


def test_score_fixture_perfect_judge():
    fx = {
        "id": "t",
        "sources": [{"id": "S1", "tier": 1, "kind": "debrief", "text": "missed Thu/Fri"}],
        "draft": "[[claim id=c1 type=fact cite=S1]] You missed two runs.\n[[claim id=c2 type=fact]] You hated yourself.",
        "expect": {"assert": ["c1"], "demote": ["c2"], "state_as_read": []},
    }

    def judge_fn(claims, sources):
        return [
            Verdict("c1", ClaimType.FACT, grounded=True, contradicts=False, rationale=""),
            Verdict("c2", ClaimType.FACT, grounded=False, contradicts=False, rationale=""),
        ]

    report = score_fixture(fx, temperature=6, judge_fn=judge_fn, rewrite_fn=lambda c, s: "Q?")
    assert report["correct"] == 2 and report["total"] == 2 and report["agreement"] == 1.0
    assert report["mismatches"] == []


def test_score_fixture_detects_mismatch():
    fx = {
        "id": "t2",
        "sources": [{"id": "S1", "tier": 1, "kind": "debrief", "text": "x"}],
        "draft": "[[claim id=c1 type=fact]] Ungrounded fact.",
        "expect": {"assert": [], "demote": ["c1"], "state_as_read": []},
    }

    # buggy judge says grounded -> gate asserts -> mismatch vs expected demote
    def judge_fn(claims, sources):
        return [Verdict("c1", ClaimType.FACT, grounded=True, contradicts=False, rationale="")]

    report = score_fixture(fx, temperature=6, judge_fn=judge_fn, rewrite_fn=lambda c, s: "Q?")
    assert report["agreement"] == 0.0
    assert report["mismatches"][0] == {"claim_id": "c1", "expected": "demote", "got": "assert"}


def test_run_all_uses_real_fixtures_with_injected_judge():
    # An injected judge that grounds everything cited, treats uncited fact as ungrounded.
    def judge_fn(claims, sources):
        out = []
        for c in claims:
            grounded = bool(c.cites)
            dt = c.type
            out.append(Verdict(c.id, dt, grounded=grounded, contradicts=False, rationale=""))
        return out

    reports = run_all(temperature=6, judge_fn=judge_fn, rewrite_fn=lambda c, s: "Is that right?")
    ids = {r["id"] for r in reports}
    assert "f000_punishing_yourself" in ids
    # f000: c1 cited->assert (matches), c2 uncited->demote (matches) => agreement 1.0
    f000 = next(r for r in reports if r["id"] == "f000_punishing_yourself")
    assert f000["agreement"] == 1.0

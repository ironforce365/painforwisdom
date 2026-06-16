from eval.grounding.corpus import RegressionCorpus
from eval.grounding.gate import run_gate
from eval.grounding.types import ClaimType, Source, Verdict


def test_gate_demotes_ungrounded_fact(tmp_path):
    draft = (
        "[[claim id=c1 type=fact cite=S1]] You missed Thursday and Friday.\n"
        "[[claim id=c2 type=fact]] You were punishing yourself.\n"
        "How did the week feel overall?"
    )
    sources = [Source(id="S1", tier=1, kind="debrief", text="missed Thu/Fri, legs heavy")]
    verdicts = [
        Verdict("c1", ClaimType.FACT, grounded=True, contradicts=False, rationale=""),
        Verdict("c2", ClaimType.FACT, grounded=False, contradicts=False, rationale="no source"),
    ]
    corpus = RegressionCorpus(tmp_path / "c")
    result = run_gate(
        draft, sources, temperature=5,
        judge_fn=lambda claims, srcs: verdicts,
        rewrite_fn=lambda claim, srcs: "Were you punishing yourself, or just noticing the heaviness?",
        corpus=corpus,
    )
    assert "You missed Thursday and Friday." in result.message       # grounded fact survives
    assert "punishing yourself." not in result.message                # assertion removed
    assert "Were you punishing yourself" in result.message            # replaced by question
    assert "How did the week feel overall?" in result.message         # passthrough kept
    assert "c2" in result.logged_ids
    assert corpus.load(signal="catch")[0]["claim_id"] == "c2"


def test_gate_states_confident_interpretation(tmp_path):
    draft = "[[claim id=c1 type=interpretation conf=8]] This reads like self-punishment."
    verdicts = [Verdict("c1", ClaimType.INTERPRETATION, grounded=False, contradicts=False, rationale="")]
    result = run_gate(
        draft, [], temperature=6,
        judge_fn=lambda c, s: verdicts,
        rewrite_fn=lambda c, s: "Q?",
        corpus=RegressionCorpus(tmp_path / "c"),
    )
    assert "self-punishment" in result.message
    assert "my read" in result.message.lower()  # framed as the coach's read
    assert result.logged_ids == []              # nothing demoted


def test_gate_judges_midline_tag_and_never_leaks_it(tmp_path):
    # Regression for the Telegram leak: a tag buried mid-line must be (a) judged,
    # not skipped, and (b) absent from the user-facing message either way.
    draft = (
        "Every avoided discomfort is a silent withdrawal. "
        "[[claim id=c1 type=fact cite=M1]] A 100-miler will demand that account be full."
    )
    sources = [Source(id="M1", tier=1, kind="memory", text="signed up for a 100-miler")]
    verdicts = [Verdict("c1", ClaimType.FACT, grounded=False, contradicts=False, rationale="no memory")]
    result = run_gate(
        draft, sources, temperature=5,
        judge_fn=lambda c, s: verdicts,
        rewrite_fn=lambda c, s: "Will that account need to be full on race day?",
        corpus=RegressionCorpus(tmp_path / "c"),
    )
    assert "[[claim" not in result.message                         # no protocol leak
    assert "A 100-miler will demand" not in result.message         # ungrounded fact demoted
    assert "Will that account need to be full" in result.message   # replaced by question
    assert "Every avoided discomfort is a silent withdrawal." in result.message  # passthrough
    assert "c1" in result.logged_ids                               # it WAS judged


def test_gate_missing_verdict_fails_safe_to_demote(tmp_path):
    draft = "[[claim id=c1 type=fact cite=S1]] Some fact."
    result = run_gate(
        draft, [], temperature=5,
        judge_fn=lambda c, s: [],                # judge returned nothing for c1
        rewrite_fn=lambda c, s: "Is that right?",
        corpus=RegressionCorpus(tmp_path / "c"),
    )
    assert "Is that right?" in result.message
    assert "Some fact." not in result.message

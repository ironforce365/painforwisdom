from eval.grounding.corpus import RegressionCorpus


def test_append_and_load(tmp_path):
    corpus = RegressionCorpus(tmp_path / "corpus")
    corpus.append({"claim_id": "c1", "signal": "catch", "claim_text": "you were punishing yourself",
                   "demoted_question": "were you?", "judge_rationale": "ungrounded"})
    corpus.append({"claim_id": "c2", "signal": "correction", "claim_text": "x", "user_correction": "no"})
    records = corpus.load()
    assert len(records) == 2
    assert records[0]["signal"] == "catch"
    assert (tmp_path / "corpus" / "corpus.md").exists()
    assert (tmp_path / "corpus" / "corpus.jsonl").exists()


def test_signal_filter(tmp_path):
    corpus = RegressionCorpus(tmp_path / "c")
    corpus.append({"claim_id": "a", "signal": "catch", "claim_text": "t"})
    corpus.append({"claim_id": "b", "signal": "validation", "claim_text": "t"})
    assert [r["claim_id"] for r in corpus.load(signal="catch")] == ["a"]
    assert [r["claim_id"] for r in corpus.load(signal="validation")] == ["b"]


def test_load_empty(tmp_path):
    assert RegressionCorpus(tmp_path / "fresh").load() == []

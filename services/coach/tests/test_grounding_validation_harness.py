import json

from eval.grounding.validation_harness import load_cases, run_cases


def test_cases_load_and_are_well_formed():
    cases = load_cases()
    assert len(cases) >= 8
    for c in cases:
        assert c["id"] and c["open_items"] and c["user_reply"]
        item_ids = {i["claim_id"] for i in c["open_items"]}
        assert set(c["expected"]) == item_ids  # every open item has an expectation


def test_run_cases_scores_with_fake_llm():
    cases = load_cases()

    def oracle(*, system, user, model="m"):
        # echo back the expected outcomes by matching the case via its reply text
        for c in cases:
            if c["user_reply"] in user:
                return json.dumps({"outcomes": [
                    {"claim_id": cid, "outcome": want, "correction_text": ""}
                    for cid, want in c["expected"].items()
                ]})
        return "{}"

    report = run_cases(cases, llm_fn=oracle)
    assert report["items_correct"] == report["items_total"] > 0

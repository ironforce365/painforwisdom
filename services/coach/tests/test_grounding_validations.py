from eval.grounding.validations import PendingValidations


def _open_item(claim_id="c1", thread_id="t1", **kw):
    rec = {
        "ts": "2026-06-10T22:00:00",
        "thread_id": thread_id,
        "user_id": "u1",
        "claim_id": claim_id,
        "claim_text": "This reads like self-punishment.",
        "claim_type": "interpretation",
        "confidence": 6,
        "action": "demote",
        "question": "Were you pushing to punish yourself, or something else?",
    }
    rec.update(kw)
    return rec


def test_open_and_list_open_roundtrip(tmp_path):
    pv = PendingValidations(tmp_path)
    pv.open(_open_item())
    items = pv.list_open("t1")
    assert len(items) == 1
    assert items[0]["claim_id"] == "c1"
    assert items[0]["status"] == "open"
    assert pv.list_open("other-thread") == []


def test_resolve_closes_item(tmp_path):
    pv = PendingValidations(tmp_path)
    pv.open(_open_item(claim_id="c1"))
    pv.open(_open_item(claim_id="c2"))
    pv.resolve("t1", "c1", status="corrected", ts="2026-06-10T22:05:00",
               note="No, it was a tactic.")
    open_items = pv.list_open("t1")
    assert [i["claim_id"] for i in open_items] == ["c2"]
    all_items = pv.load()
    resolved = next(i for i in all_items if i["claim_id"] == "c1")
    assert resolved["status"] == "corrected"
    assert resolved["resolved_ts"] == "2026-06-10T22:05:00"
    assert resolved["resolution_note"] == "No, it was a tactic."


def test_resolve_unknown_claim_is_noop(tmp_path):
    pv = PendingValidations(tmp_path)
    pv.open(_open_item())
    pv.resolve("t1", "missing", status="confirmed", ts="2026-06-10T22:05:00")
    assert len(pv.list_open("t1")) == 1


def test_open_items_isolated_per_thread(tmp_path):
    pv = PendingValidations(tmp_path)
    pv.open(_open_item(claim_id="c1", thread_id="t1"))
    pv.open(_open_item(claim_id="c1", thread_id="t2"))
    pv.resolve("t1", "c1", status="confirmed", ts="2026-06-10T22:05:00")
    assert pv.list_open("t1") == []
    assert len(pv.list_open("t2")) == 1

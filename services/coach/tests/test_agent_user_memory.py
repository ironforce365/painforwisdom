"""Conversation-only user memory (Stream 3): the coach remembers the person it's
talking to from mem0 — written from the user's OWN words, read back as an
`<about_this_user>` block + an M1 grounding source. Never vault-seeded.
"""
import agent.memory as mem


class FakeClient:
    def __init__(self, results=None, fail=False):
        self.results = results if results is not None else []
        self.added: list[tuple[str, str]] = []
        self.deleted: list[str] = []
        self.fail = fail

    def search(self, user_id, query, limit=5):
        if self.fail:
            raise RuntimeError("mem0 down")
        self.last_search = (user_id, query, limit)
        return self.results

    def add(self, user_id, text):
        if self.fail:
            raise RuntimeError("mem0 down")
        self.added.append((user_id, text))
        return {"ok": True}

    def delete(self, user_id):
        if self.fail:
            raise RuntimeError("mem0 down")
        self.deleted.append(user_id)
        return {"deleted": True}


# ---- delete (used by /restart) -------------------------------------------

def test_delete_user_memory_calls_client():
    client = FakeClient()
    mem.delete_user_memory("123", client=client)
    assert client.deleted == ["123"]


def test_delete_user_memory_swallows_failure():
    # A mem0 outage must never break /restart.
    mem.delete_user_memory("123", client=FakeClient(fail=True))  # no raise


# ---- format ---------------------------------------------------------------

def test_format_about_user_renders_block():
    block = mem.format_about_user(["Left Achilles bugs them after runs.", "Targeting a 100k."])
    assert "<about_this_user>" in block and "</about_this_user>" in block
    assert "Left Achilles" in block and "100k" in block


def test_format_about_user_empty_is_blank():
    assert mem.format_about_user([]) == ""


def test_memory_text_handles_dict_and_str():
    assert mem._memory_text({"memory": "x"}) == "x"
    assert mem._memory_text({"text": "y"}) == "y"
    assert mem._memory_text("z") == "z"


# ---- read -----------------------------------------------------------------

def test_read_returns_block_and_source_text():
    client = FakeClient(results=[{"memory": "Achilles bugs them all week."}, {"memory": "Vacation in Europe soon."}])
    block, source_text = mem.read_user_memory("123", "achilles pain", client=client, limit=5)
    assert "Achilles bugs them all week." in block
    assert "<about_this_user>" in block
    # source text is the raw memories joined, for the grounding judge (no XML)
    assert "Achilles bugs them all week." in source_text
    assert "<about_this_user>" not in source_text
    assert client.last_search == ("123", "achilles pain", 5)


def test_read_empty_results_blank():
    block, source_text = mem.read_user_memory("123", "q", client=FakeClient(results=[]))
    assert block == "" and source_text == ""


def test_read_failure_degrades_to_blank():
    block, source_text = mem.read_user_memory("123", "q", client=FakeClient(fail=True))
    assert block == "" and source_text == ""


# ---- write (conversation-only) --------------------------------------------

def test_write_feeds_only_user_text():
    client = FakeClient()
    mem.write_user_memory("123", "My left Achilles has hurt all week.", client=client)
    assert client.added == [("123", "My left Achilles has hurt all week.")]


def test_write_failure_is_swallowed():
    # must never break a live turn
    mem.write_user_memory("123", "anything", client=FakeClient(fail=True))


def test_write_blank_text_noops():
    client = FakeClient()
    mem.write_user_memory("123", "   ", client=client)
    assert client.added == []

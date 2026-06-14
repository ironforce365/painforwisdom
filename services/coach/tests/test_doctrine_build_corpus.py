"""Build the doctrine corpus from raw vault dirs: distill each entry, drop
contaminated principles, write clean `.md` the existing vault_rag builder can
index. The corpus must contain ZERO first-person/biographical text.
"""
import json

import doctrine.build_corpus as bc
import doctrine.distill as dd


def _make_vault(tmp_path):
    v = tmp_path / "vault"
    (v / "gonzalo-book" / "deep-dive").mkdir(parents=True)
    (v / "thoughts").mkdir(parents=True)
    (v / "_inbox").mkdir(parents=True)
    (v / "gonzalo-book" / "deep-dive" / "body-literacy.md").write_text("raw deep dive text")
    (v / "thoughts" / "2026-05-09-pain.md").write_text("raw thought text")
    (v / "thoughts" / "_candidate-themes-report.md").write_text("scratch, skip me")
    (v / "_inbox" / "convo.md").write_text("coach log, must be excluded")
    return v


def test_iter_source_files_scopes_and_skips_underscore(tmp_path):
    v = _make_vault(tmp_path)
    files = bc.iter_source_files(v, ["gonzalo-book/deep-dive", "thoughts"])
    names = sorted(p.name for p in files)
    assert names == ["2026-05-09-pain.md", "body-literacy.md"]  # underscore + _inbox excluded


def test_build_writes_clean_corpus_and_drops_contaminated(tmp_path, monkeypatch):
    v = _make_vault(tmp_path)
    out = tmp_path / "doctrine_corpus"

    def fake_llm(*, system, user, model):
        # one clean principle, one contaminated — contaminated must be dropped
        return json.dumps({"principles": [
            {"text": "Pain that quiets under load can mask accumulating damage.", "theme": "body-literacy"},
            {"text": "I learned this the hard way over four months.", "theme": "recovery"},
        ]})

    summary = bc.build(v, out, ["gonzalo-book/deep-dive", "thoughts"], llm_fn=fake_llm, model="m")

    assert summary["files_in"] == 2
    assert summary["principles_kept"] == 2   # one clean per file
    assert summary["principles_dropped"] == 2

    corpus_text = "\n".join(p.read_text() for p in out.glob("*.md"))
    assert "Pain that quiets under load" in corpus_text
    assert "four months" not in corpus_text          # contamination never written
    # the whole corpus passes the QA gate line by line
    for line in corpus_text.splitlines():
        if line.strip() and not line.startswith("#"):
            assert dd.is_depersonalized(line), f"contaminated line in corpus: {line!r}"


def test_build_empty_when_no_sources(tmp_path):
    v = _make_vault(tmp_path)
    out = tmp_path / "c"
    summary = bc.build(v, out, ["does-not-exist"], llm_fn=lambda **kw: "{}", model="m")
    assert summary["files_in"] == 0 and summary["principles_kept"] == 0

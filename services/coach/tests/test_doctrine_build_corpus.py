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
    # every retrievable principle line passes the QA gate (skip frontmatter, which
    # carries the path/slug + theme and is excluded from embeddings, not content)
    _meta = ("#", "---", "source:", "theme:")
    for line in corpus_text.splitlines():
        s = line.strip()
        if s and not s.startswith(_meta):
            assert dd.is_depersonalized(s), f"contaminated principle line: {s!r}"


def test_build_slugs_by_relative_path_no_collision(tmp_path):
    # deep-dives repeat the stem 'theory.md' across theme dirs — they must NOT
    # collapse into one corpus file (provenance loss).
    v = tmp_path / "vault"
    (v / "gonzalo-book" / "deep-dive" / "theme-a").mkdir(parents=True)
    (v / "gonzalo-book" / "deep-dive" / "theme-b").mkdir(parents=True)
    (v / "gonzalo-book" / "deep-dive" / "theme-a" / "theory.md").write_text("a")
    (v / "gonzalo-book" / "deep-dive" / "theme-b" / "theory.md").write_text("b")
    out = tmp_path / "c"
    bc.build(v, out, ["gonzalo-book/deep-dive"],
             llm_fn=lambda **kw: json.dumps({"principles": [{"text": "You grow by confronting fear.", "theme": "t"}]}),
             model="m")
    written = sorted(p.name for p in out.glob("*.md"))
    assert len(written) == 2, f"slug collision: {written}"  # two distinct files


def test_build_empty_when_no_sources(tmp_path):
    v = _make_vault(tmp_path)
    out = tmp_path / "c"
    summary = bc.build(v, out, ["does-not-exist"], llm_fn=lambda **kw: "{}", model="m")
    assert summary["files_in"] == 0 and summary["principles_kept"] == 0

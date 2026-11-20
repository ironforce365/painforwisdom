"""Writer decline: the refusal from run 2026-11-19_170856_ui must not become a post."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from pipeline.nodes.notion_blog import node_notion_blog
from pipeline.nodes.validators.branch_wordpress import node_bv_wordpress
from pipeline.nodes.validators.summarizer import node_summarizer
from pipeline.nodes.wordpress_draft import node_wordpress_draft
from pipeline.nodes.writer import DECLINE_MARKER, _build_system_prompt, node_writer

REFUSAL = (Path(__file__).parent / "fixtures" / "writer_refusal_2026-11-19.md").read_text()
REASON = "the inputs lack the required opening scene and story anchor"


def _llm(text):
    return {"text": text, "model": "m", "input_tokens": 1, "output_tokens": 1,
            "cache_read_tokens": 0, "cache_creation_tokens": 0, "cost_usd": 0.0}


def _write(tmp_path, text):
    state = {"extraction_report": "r", "transcript_text": "t", "video_date": "2026-11-18", "run_dir": str(tmp_path)}
    with patch("pipeline.nodes.writer.call_llm", return_value=_llm(text)), \
         patch("pipeline.nodes.writer._build_cross_post_context", return_value=""):
        return state, node_writer(state)


def test_prompt_defines_marker():
    assert DECLINE_MARKER in _build_system_prompt()


def test_declined_refusal_flows_to_warn_and_telegram(tmp_path):
    state, out = _write(tmp_path, f"{DECLINE_MARKER} {REASON}\n{REFUSAL}")
    assert out == {"writer_declined": True, "writer_decline_reason": REASON, "writer_decline_needed": REFUSAL.strip()}
    assert not (tmp_path / "painforwisdom-writer" / "blog_post.md").exists()
    assert (tmp_path / "painforwisdom-writer" / "DECLINED.md").is_file()
    state.update(out)

    assert node_notion_blog(state) == {}
    state.update(node_wordpress_draft(state))
    assert state["wordpress_skip_reason"] == "writer declined"
    assert not (tmp_path / "blog-publish").exists()

    state.update(node_bv_wordpress(state))
    assert state["branch_verdict_wordpress"] == "PARTIAL"
    assert state["branch_findings_wordpress"][0]["detail"] == f"writer declined: {REASON}"

    state.update(run_id="r", branch_validations_done=["research", "youtube", "wordpress"])
    with patch("pipeline.nodes.validators.summarizer.telegram_send", return_value=0) as ts:
        assert node_summarizer(state)["validator_verdict"] == "PARTIAL"
    sent = ts.call_args.args[0]
    assert f"declined ({REASON})" in sent
    assert "What is needed to proceed" in sent


def test_post_without_marker_is_a_post(tmp_path):
    post = "**Title:** a title\n\n*11/18/26*\n" + "word " * 300
    _, out = _write(tmp_path, post)
    assert out["blog_post_title"] == "a title"
    assert "writer_declined" not in out

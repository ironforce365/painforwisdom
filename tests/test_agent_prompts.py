"""Stage prompts live in pipeline/agents/, not .claude/agents/ (spec 2026-10-30-harness-housekeeping §2.3a)."""
import pytest

from pipeline.runtime import PROJECT_ROOT, load_agent_prompt

STAGE_PROMPTS = [
    "coaching-thought-extractor.md",
    "kb-curator.md",
    "painforwisdom-writer.md",
    "research-curator.md",
    "youtube-upload-agent.md",
]


@pytest.mark.parametrize("filename", STAGE_PROMPTS)
def test_stage_prompt_loads_without_frontmatter(filename):
    body = load_agent_prompt(filename)
    assert body.strip()
    assert not body.lstrip().startswith("---")


def test_claude_agents_dir_is_gone():
    assert not (PROJECT_ROOT / ".claude" / "agents").exists()


@pytest.mark.parametrize("filename", STAGE_PROMPTS)
def test_stage_prompt_keeps_execution_context(filename):
    # Spec 2026-10-30-harness-housekeeping §6.4a: appendix survived the rename.
    assert "EXECUTION CONTEXT" in load_agent_prompt(filename)


def test_model_constants_are_priced():
    from pipeline.cost_forecast import PRICES
    from pipeline.runtime import DEEP_MODEL, DEFAULT_MODEL

    assert DEFAULT_MODEL in PRICES and DEEP_MODEL in PRICES

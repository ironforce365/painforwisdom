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

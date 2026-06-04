"""Unit test for the PoC react-audio harness source parsing."""
from pathlib import Path

import pytest

from pipeline.scripts.poc_react_audio import parse_sources


def test_parse_sources_returns_title_path_pairs(tmp_path):
    f = tmp_path / "resp.md"
    f.write_text("hi")
    pairs = parse_sources([f"response={f}"])
    assert pairs == [("response", f)]


def test_parse_sources_rejects_missing_equals(tmp_path):
    with pytest.raises(ValueError):
        parse_sources(["no-equals-sign"])


def test_parse_sources_rejects_missing_file(tmp_path):
    with pytest.raises(FileNotFoundError):
        parse_sources([f"x={tmp_path/'nope.md'}"])

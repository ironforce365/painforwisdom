"""Per-node input contract tests (stdlib unittest, no pytest dep).

Run:  python -m unittest tests.test_contracts
or:   python tests/test_contracts.py
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

# Allow `python tests/test_contracts.py` from the project root.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pipeline.contracts import (  # noqa: E402
    NODE_INPUTS,
    InputContractError,
    assert_inputs,
)


VALID_STATE = {
    "video_path": "/tmp/v.mp4",
    "run_dir": "/tmp/run",
    "transcript_text": "hello world this is a transcript",
    "transcript_path": "/tmp/t.txt",
    "extraction_report": "**Content Quality:** Strong\n**Core Insight:** ...",
    "video_date": "2026-04-14",
    "vault_entry_slug": "2026-04-14-foo",
    "vault_entry_path": "/tmp/vault/foo.md",
    "blog_post_title": "title",
    "blog_post_text": "body body body",
    "research_csv_path": "/tmp/research.csv",
    "themes": ["theme-a"],
    "frameworks": [],
}


class ContractsTest(unittest.TestCase):
    def test_valid_state_passes_every_node(self) -> None:
        for node in NODE_INPUTS:
            with self.subTest(node=node):
                assert_inputs(node, VALID_STATE)  # must not raise

    def test_missing_required_field_raises_for_every_node(self) -> None:
        for node, keys in NODE_INPUTS.items():
            for required in keys:
                with self.subTest(node=node, missing=required):
                    state = {k: v for k, v in VALID_STATE.items() if k != required}
                    with self.assertRaises(InputContractError) as cm:
                        assert_inputs(node, state)
                    self.assertEqual(cm.exception.node, node)
                    self.assertIn(required, cm.exception.missing)

    def test_unknown_node_raises_keyerror(self) -> None:
        with self.assertRaises(KeyError):
            assert_inputs("does_not_exist", VALID_STATE)

    def test_validator_node_has_no_required_fields(self) -> None:
        # Validator inspects state itself; contract is intentionally empty.
        assert_inputs("validator", {})

    def test_empty_string_counts_as_missing(self) -> None:
        state = dict(VALID_STATE)
        state["video_path"] = ""
        with self.assertRaises(InputContractError) as cm:
            assert_inputs("transcribe", state)
        self.assertIn("video_path", cm.exception.missing)


if __name__ == "__main__":
    unittest.main(verbosity=2)

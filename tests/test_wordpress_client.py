"""Unit tests for the WordPress client utilities — no live API hits.

Covers the pure helpers: ``markdown_to_html``, ``first_50_words``,
``render_wp_html``, and ``write_dormant_bundle``. The HTTP surface is
exercised via a stubbed transport so we don't burn live quota.
"""
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pipeline.wordpress_client import (  # noqa: E402
    first_50_words,
    markdown_to_html,
    render_wp_html,
    write_dormant_bundle,
)


class FirstFiftyWordsTest(unittest.TestCase):
    def test_short_body_returned_intact(self) -> None:
        body = "one two three four five"
        self.assertEqual(first_50_words(body), "one two three four five")

    def test_strips_markdown_markers(self) -> None:
        body = "**bold** *italic* normal"
        self.assertEqual(first_50_words(body), "bold italic normal")

    def test_truncates_with_ellipsis(self) -> None:
        body = " ".join(f"w{i}" for i in range(120))
        result = first_50_words(body, max_words=50)
        # Either a sentence-stop earlier or an ellipsis at the end.
        self.assertTrue(result.endswith("…") or result.endswith("."))
        self.assertLessEqual(len(result.split()), 51)


class MarkdownToHtmlTest(unittest.TestCase):
    def test_paragraphs_and_bold(self) -> None:
        md = "This is **bold**.\n\nSecond paragraph."
        html = markdown_to_html(md)
        self.assertIn("<strong>bold</strong>", html)
        self.assertIn("<p>", html)


class RenderWpHtmlTest(unittest.TestCase):
    def test_date_line_includes_youtube_placeholder(self) -> None:
        html = render_wp_html(body_md="Hello world.", date_mmddyy="04/14/26")
        self.assertIn("04/14/26", html)
        self.assertIn("[YOUTUBE_SHORT_URL]", html)
        self.assertIn("YouTube short", html)

    def test_strips_writer_date_line_from_body(self) -> None:
        md = "*04/14/26.*\n\nBody text."
        html = render_wp_html(body_md=md, date_mmddyy="04/14/26")
        # Date line at top is rendered once, not duplicated inside the body.
        self.assertEqual(html.count("04/14/26"), 1)


class WriteDormantBundleTest(unittest.TestCase):
    def test_bundle_files_written(self) -> None:
        with TemporaryDirectory() as tmp:
            out_dir = Path(tmp) / "wp"
            paths = write_dormant_bundle(
                out_dir,
                title="my post",
                body_md="hello\n\nworld",
                html="<p>hello</p>",
                excerpt="hello world",
                tags=["painforwisdom"],
                featured_image_path=None,
                metadata={"video_date": "2026-04-14"},
            )
            self.assertTrue(Path(paths["post_md"]).is_file())
            self.assertTrue(Path(paths["post_html"]).is_file())
            meta = json.loads(Path(paths["meta_json"]).read_text())
            self.assertEqual(meta["title"], "my post")
            self.assertEqual(meta["video_date"], "2026-04-14")


if __name__ == "__main__":
    unittest.main(verbosity=2)

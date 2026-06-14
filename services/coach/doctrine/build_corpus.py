"""Build the doctrine corpus: walk raw vault source dirs, distill each entry into
de-personalised principles, and write them as clean ``.md`` files.

The output dir is a "clean vault" the existing ``vault_rag.builder`` indexes
unchanged — so the coach retrieves DOCTRINE (transferable lessons), never raw
journal entries. Run:

    PYTHONPATH=services/coach python -m doctrine.build_corpus

Env:
    COACH_VAULT_PATH            raw vault root (source of truth)
    COACH_DOCTRINE_CORPUS_DIR   output dir for the clean principle .md files
    COACH_DOCTRINE_SOURCE_DIRS  comma-separated rel dirs to distill
    COACH_DOCTRINE_MODEL        model for distillation (default sonnet)
Then index COACH_DOCTRINE_CORPUS_DIR into COACH_DOCTRINE_INDEX_DIR with
vault_rag.builder.build_index.
"""
from __future__ import annotations

import os
from pathlib import Path

from doctrine.distill import Principle, extract_principles, extract_with_stats

DEFAULT_SOURCE_DIRS = [
    "gonzalo-book/deep-dive",
    "gonzalo-book/themes",
    "gonzalo-book/frameworks",
    "gonzalo-book/entries",
    "thoughts",
]


def iter_source_files(vault_dir: Path, source_dirs: list[str]) -> list[Path]:
    """All ``.md`` under the given rel dirs, skipping ``_``-prefixed scratch files."""
    out: list[Path] = []
    for rel in source_dirs:
        base = vault_dir / rel
        if not base.is_dir():
            continue
        for p in sorted(base.rglob("*.md")):
            if p.name.startswith("_"):
                continue
            out.append(p)
    return out


def _slug_for(path: Path, vault_dir: Path) -> str:
    """Stable, COLLISION-FREE slug from the path relative to the vault.

    Deep-dives repeat stems (`theory.md`, `application.md`) across theme dirs, so
    a bare stem collapses dozens of distinct entries into one corpus file and
    destroys provenance. Use the full relative path instead.
    """
    try:
        rel = path.relative_to(vault_dir).with_suffix("")
    except ValueError:
        rel = Path(path.stem)
    return str(rel).replace("/", "__").lower()


def distill_file(path: Path, *, llm_fn=None, model: str, vault_dir: Path | None = None) -> list[Principle]:
    text = path.read_text(encoding="utf-8", errors="ignore")
    slug = _slug_for(path, vault_dir) if vault_dir else path.stem.lower()
    return extract_principles(text, llm_fn=llm_fn, model=model, source_slug=slug)


def write_corpus(principles: list[Principle], out_dir: Path) -> int:
    """Write one ``.md`` per source slug; principles as separate paragraphs.

    Provenance (source slug, theme) goes in frontmatter only; the retrievable
    body is principle text alone — no biography, no citation leakage.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    by_slug: dict[str, list[Principle]] = {}
    for p in principles:
        by_slug.setdefault(p.source_slug or "misc", []).append(p)
    for slug, ps in by_slug.items():
        theme = next((p.theme for p in ps if p.theme), "")
        body = "\n\n".join(p.text for p in ps)
        front = f"---\nsource: {slug}\ntheme: {theme}\n---\n\n"
        (out_dir / f"{slug}.md").write_text(front + body + "\n", encoding="utf-8")
    return len(by_slug)


def build(vault_dir: Path, out_dir: Path, source_dirs: list[str], *, llm_fn=None, model: str) -> dict:
    files = iter_source_files(vault_dir, source_dirs)
    kept: list[Principle] = []
    dropped = 0
    for f in files:
        raw_text = f.read_text(encoding="utf-8", errors="ignore")
        slug = _slug_for(f, vault_dir)
        principles, n_dropped = extract_with_stats(
            raw_text, llm_fn=llm_fn, model=model, source_slug=slug
        )  # ONE LLM call per file; QA gate + drop count from the same parse
        dropped += n_dropped
        kept.extend(principles)
    files_written = write_corpus(kept, out_dir) if kept else 0
    return {
        "files_in": len(files),
        "files_written": files_written,
        "principles_kept": len(kept),
        "principles_dropped": dropped,
    }


def main() -> None:
    vault_dir = Path(os.environ["COACH_VAULT_PATH"])
    out_dir = Path(os.environ.get("COACH_DOCTRINE_CORPUS_DIR", "/data/doctrine_corpus"))
    source_dirs = [
        d.strip()
        for d in os.environ.get("COACH_DOCTRINE_SOURCE_DIRS", ",".join(DEFAULT_SOURCE_DIRS)).split(",")
        if d.strip()
    ]
    model = os.environ.get("COACH_DOCTRINE_MODEL", "claude-sonnet-4-6")
    summary = build(vault_dir, out_dir, source_dirs, model=model)
    print(f"[doctrine] {summary} -> {out_dir}")


if __name__ == "__main__":
    main()

"""Classify a free-text snippet against the vault's theme set by cosine similarity.

Theme embeddings cached on disk as .npz (safe; allow_pickle=False on load)."""
from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path
import os
import numpy as np


@dataclass(frozen=True)
class ThemeMatch:
    theme: str
    score: float


def _embed_text(text: str) -> np.ndarray:
    from openai import OpenAI
    r = OpenAI().embeddings.create(model="text-embedding-3-small", input=text)
    return np.asarray(r.data[0].embedding, dtype=np.float32)


def _load_theme_embeddings() -> dict[str, np.ndarray]:
    cache = Path(os.environ.get("COACH_THEME_EMBED_CACHE", "/data/theme_embeds.npz"))
    if cache.exists():
        # allow_pickle=False ensures only plain ndarray data is loaded (no code execution).
        loaded = np.load(cache, allow_pickle=False)
        return {k: loaded[k] for k in loaded.files}
    raise FileNotFoundError(f"theme embedding cache not found at {cache}; rebuild first")


def classify(text: str, top_n: int = 3) -> list[ThemeMatch]:
    query = _embed_text(text)
    themes = _load_theme_embeddings()
    qn = query / (np.linalg.norm(query) + 1e-12)
    scored = []
    for name, vec in themes.items():
        vn = vec / (np.linalg.norm(vec) + 1e-12)
        scored.append(ThemeMatch(theme=name, score=float(np.dot(qn, vn))))
    scored.sort(key=lambda m: m.score, reverse=True)
    return scored[:top_n]

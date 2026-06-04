"""classify_themes returns top-N theme matches by cosine sim against vault theme set."""
from __future__ import annotations
import numpy as np
from sidecar.classify_themes import classify, ThemeMatch


def test_classify_returns_ranked_matches(monkeypatch):
    fake_theme_embeds = {
        "deliberate-discomfort": np.array([1.0, 0.0]),
        "comfort-as-default": np.array([0.0, 1.0]),
    }
    def fake_embed(text: str) -> np.ndarray:
        return np.array([0.9, 0.1])
    monkeypatch.setattr("sidecar.classify_themes._embed_text", fake_embed)
    monkeypatch.setattr("sidecar.classify_themes._load_theme_embeddings", lambda: fake_theme_embeds)

    matches = classify("running through hard rain", top_n=2)
    assert isinstance(matches[0], ThemeMatch)
    assert matches[0].theme == "deliberate-discomfort"
    assert matches[0].score > matches[1].score

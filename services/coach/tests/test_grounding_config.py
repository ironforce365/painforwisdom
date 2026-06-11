from eval.grounding.config import load_config


def test_defaults_when_unset(monkeypatch):
    monkeypatch.delenv("COACH_GROUNDING_TEMPERATURE", raising=False)
    monkeypatch.delenv("COACH_GROUNDING_ABSORPTION", raising=False)
    cfg = load_config()
    assert cfg.temperature == 5 and cfg.absorption == 5  # neutral pre-calibration


def test_env_override(monkeypatch):
    monkeypatch.setenv("COACH_GROUNDING_TEMPERATURE", "8")
    monkeypatch.setenv("COACH_GROUNDING_ABSORPTION", "2")
    cfg = load_config()
    assert cfg.temperature == 8 and cfg.absorption == 2


def test_clamps_out_of_range(monkeypatch):
    monkeypatch.setenv("COACH_GROUNDING_TEMPERATURE", "99")
    monkeypatch.setenv("COACH_GROUNDING_ABSORPTION", "0")
    cfg = load_config()
    assert cfg.temperature == 10 and cfg.absorption == 1


def test_garbage_falls_back_to_default(monkeypatch):
    monkeypatch.setenv("COACH_GROUNDING_TEMPERATURE", "high")
    assert load_config().temperature == 5

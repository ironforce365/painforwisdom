"""Localized user-facing strings. The coach serves a mixed-language pilot, so
welcome + quota messages must not assume English: they key off the Telegram
client's language_code, with a bilingual EN+ES fallback for anything unknown."""
from __future__ import annotations

from telegram_bot import i18n


def test_welcome_spanish():
    msg = i18n.welcome_text("es")
    assert "Hola" in msg
    assert "metas" in msg  # goals, whatever they are


def test_welcome_english():
    msg = i18n.welcome_text("en")
    assert "Hi" in msg or "Welcome" in msg
    assert "goals" in msg


def test_welcome_portuguese():
    msg = i18n.welcome_text("pt")
    assert "Olá" in msg
    assert "objetivos" in msg


def test_welcome_normalizes_region_suffix():
    # Telegram sends codes like "en-US" / "es-ES" / "pt-BR".
    assert i18n.welcome_text("en-US") == i18n.welcome_text("en")
    assert i18n.welcome_text("es-ES") == i18n.welcome_text("es")
    assert i18n.welcome_text("PT-br") == i18n.welcome_text("pt")


def test_welcome_unknown_language_is_bilingual_fallback():
    # Don't assume English: an unknown code gets both English and Spanish.
    msg = i18n.welcome_text("zz")
    assert "goals" in msg  # English
    assert "metas" in msg  # Spanish


def test_welcome_none_language_is_bilingual_fallback():
    msg = i18n.welcome_text(None)
    assert "goals" in msg
    assert "metas" in msg


def test_quota_reached_mentions_limit_and_midnight_per_language():
    en = i18n.quota_reached_text("en", 100)
    assert "100" in en
    assert "midnight" in en.lower()

    es = i18n.quota_reached_text("es", 100)
    assert "100" in es
    assert "medianoche" in es.lower()


def test_quota_reached_unknown_language_is_bilingual_fallback():
    msg = i18n.quota_reached_text("zz", 100)
    assert "100" in msg
    assert "midnight" in msg.lower()
    assert "medianoche" in msg.lower()

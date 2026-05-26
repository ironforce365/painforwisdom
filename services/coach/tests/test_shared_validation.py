"""Telegram user_id validation guard."""
from __future__ import annotations
import pytest

from shared.validation import validate_telegram_user_id


def test_accepts_valid_numeric_ids():
    validate_telegram_user_id("1")
    validate_telegram_user_id("123456789")
    validate_telegram_user_id("9" * 19)


def test_rejects_empty():
    with pytest.raises(ValueError):
        validate_telegram_user_id("")


def test_rejects_non_numeric():
    with pytest.raises(ValueError):
        validate_telegram_user_id("abc")
    with pytest.raises(ValueError):
        validate_telegram_user_id("../escape")
    with pytest.raises(ValueError):
        validate_telegram_user_id("12 34")


def test_rejects_too_long():
    with pytest.raises(ValueError):
        validate_telegram_user_id("9" * 20)

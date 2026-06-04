"""Session map: same user_id → reused session_id; different user → new session."""
from __future__ import annotations
from agent.session_map import SessionMap


def test_session_persists_per_user():
    m = SessionMap()
    sid1 = m.get_or_create_session_id("alice")
    sid2 = m.get_or_create_session_id("alice")
    sid3 = m.get_or_create_session_id("bob")
    assert sid1 == sid2
    assert sid1 != sid3

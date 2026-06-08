"""Tests for chat service."""
import pytest
from services.chat_service import create_session, process_message, get_history


class TestCreateSession:
    def test_creates_session_id(self, test_user_id):
        sid = create_session(test_user_id)
        assert sid.startswith("chat_") or len(sid) > 8  # UUID or prefixed

    def test_creates_unique_sessions(self, test_user_id):
        s1 = create_session(test_user_id)
        s2 = create_session(test_user_id)
        assert s1 != s2


class TestProcessMessage:
    def test_returns_response(self, test_user_id):
        sid = create_session(test_user_id)
        resp = process_message(sid, "What should I wear?")
        assert resp.session_id == sid
        assert resp.response
        assert isinstance(resp.suggestions, list)

    def test_stores_message_history(self, test_user_id):
        sid = create_session(test_user_id)
        process_message(sid, "Hello")
        history = get_history(sid)
        assert len(history["messages"]) == 2  # user + assistant


class TestGetHistory:
    def test_empty_session(self, test_user_id):
        sid = create_session(test_user_id)
        history = get_history(sid)
        assert history["messages"] == []

    def test_returns_full_history(self, test_user_id):
        sid = create_session(test_user_id)
        process_message(sid, "First message")
        process_message(sid, "Second message")
        history = get_history(sid)
        assert len(history["messages"]) == 4  # 2 user + 2 assistant

"""Tests for chat service."""
from services.chat_service import create_session, process_message, get_history


class TestCreateSession:
    def test_creates_session_id(self):
        sid = create_session()
        assert sid.startswith("chat_")

    def test_creates_unique_sessions(self):
        s1 = create_session()
        s2 = create_session()
        assert s1 != s2


class TestProcessMessage:
    def test_returns_response(self):
        sid = create_session()
        resp = process_message(sid, "What should I wear?")
        assert resp.session_id == sid
        assert resp.response
        assert isinstance(resp.suggestions, list)

    def test_stores_message_history(self):
        sid = create_session()
        process_message(sid, "Hello")
        history = get_history(sid)
        assert len(history["messages"]) == 2  # user + assistant


class TestGetHistory:
    def test_empty_session(self):
        sid = create_session()
        history = get_history(sid)
        assert history["messages"] == []

    def test_returns_full_history(self):
        sid = create_session()
        process_message(sid, "First message")
        process_message(sid, "Second message")
        history = get_history(sid)
        assert len(history["messages"]) == 4  # 2 user + 2 assistant

"""Backend test configuration and fixtures."""
import sys
import os
import pytest

# Ensure the backend package is importable
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


@pytest.fixture(autouse=True)
def clear_stores():
    """Reset all in-memory stores between tests."""
    from services.store import (
        users, tokens, email_index, profiles,
        wardrobes, custom_outfits, chat_sessions, consulting_cache,
    )
    users.clear()
    tokens.clear()
    email_index.clear()
    profiles.clear()
    wardrobes.clear()
    custom_outfits.clear()
    chat_sessions.clear()
    consulting_cache.clear()
    yield

"""Backend test configuration and fixtures."""
import sys
import os
import uuid
import pytest

# Ensure the backend package is importable
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


@pytest.fixture(autouse=True)
def clean_db():
    """
    Truncate all test-relevant tables before each test so tests are isolated.
    Uses RESTART IDENTITY CASCADE to also reset sequences and FK-dependent rows.
    """
    from db import get_db_context, engine
    # Ensure database schema matches current models for tests by recreating tables.
    # This drops and recreates all tables in the configured test DB.
    from models.database import Base as _Base
    _Base.metadata.drop_all(bind=engine)
    _Base.metadata.create_all(bind=engine)
    from models.database import (
        User, UserToken, UserProfile, GarmentItem, ChatSession,
        CustomOutfit, WardrobeAnalysisCache, ImageConsultingResult,
    )
    with get_db_context() as db:
        db.query(ImageConsultingResult).delete()
        db.query(WardrobeAnalysisCache).delete()
        db.query(CustomOutfit).delete()
        db.query(GarmentItem).delete()
        db.query(ChatSession).delete()
        db.query(UserProfile).delete()
        db.query(UserToken).delete()
        db.query(User).delete()
        db.commit()
    yield


@pytest.fixture
def test_user_id():
    """
    Create a real User row in the DB and return its ID.
    The clean_db fixture (autouse) will delete it after the test.
    """
    from db import get_db_context
    from models.database import User
    from services.auth_service import hash_password

    uid = f"user_{uuid.uuid4().hex[:10]}"
    with get_db_context() as db:
        user = User(
            id=uid,
            email=f"{uid}@test.com",
            name="Test User",
            password_hash=hash_password("testpass"),
            is_onboarded=False,
        )
        db.add(user)
        db.commit()
    return uid

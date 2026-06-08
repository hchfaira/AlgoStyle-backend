"""
API integration tests — test routes through the FastAPI test client.
"""
import pytest
from fastapi.testclient import TestClient
from main import app

client = TestClient(app)


class TestHealthEndpoint:
    def test_health(self):
        res = client.get("/health")
        assert res.status_code == 200
        assert res.json()["status"] == "healthy"

    def test_root(self):
        res = client.get("/")
        assert res.status_code == 200
        assert "AlgoStyle" in res.json()["message"]


def _auth_header(token: str) -> dict:
    """Helper: build Authorization header from a token."""
    return {"Authorization": f"Bearer {token}"}


class TestAuthRoutes:
    def test_register_and_login(self):
        # Register
        res = client.post("/api/v1/auth/register", json={
            "email": "api@test.com",
            "password": "secret123",
            "name": "API User",
        })
        assert res.status_code == 200
        data = res.json()
        assert data["email"] == "api@test.com"
        token = data["token"]

        # Login
        res = client.post("/api/v1/auth/login", json={
            "email": "api@test.com",
            "password": "secret123",
        })
        assert res.status_code == 200

        # Get current user via Authorization header
        res = client.get("/api/v1/auth/me", headers=_auth_header(token))
        assert res.status_code == 200

    def test_register_duplicate_email(self):
        client.post("/api/v1/auth/register", json={
            "email": "dup@test.com", "password": "pass", "name": "U1",
        })
        res = client.post("/api/v1/auth/register", json={
            "email": "dup@test.com", "password": "pass", "name": "U2",
        })
        assert res.status_code == 400

    def test_guest_login(self):
        res = client.post("/api/v1/auth/guest")
        assert res.status_code == 200
        assert res.json()["name"] == "Guest"

    def test_login_wrong_password(self):
        client.post("/api/v1/auth/register", json={
            "email": "wrong@test.com", "password": "correct", "name": "User",
        })
        res = client.post("/api/v1/auth/login", json={
            "email": "wrong@test.com", "password": "incorrect",
        })
        assert res.status_code == 401

    def test_me_without_token_returns_401(self):
        res = client.get("/api/v1/auth/me")
        assert res.status_code in (401, 403)  # HTTPBearer raises 403 when header missing

    def test_logout(self):
        from services.auth_service import register_user
        result = register_user("logout@test.com", "pass", "User")
        token = result.token
        res = client.post("/api/v1/auth/logout", headers=_auth_header(token))
        assert res.status_code == 200
        # Token should no longer work
        res = client.get("/api/v1/auth/me", headers=_auth_header(token))
        assert res.status_code == 401


class TestRecommendationRoutes:
    def test_get_recommendations(self):
        from services.auth_service import register_user
        result = register_user("rec@test.com", "pass", "Rec User")
        token = result.token
        res = client.post("/api/v1/recommend/outfits", json={
            "occasion": "casual",
            "top_k": 3,
        }, headers=_auth_header(token))
        assert res.status_code == 200
        data = res.json()
        assert len(data["outfits"]) == 3
        assert data["total_combinations"] > 0

    def test_recommendations_with_all_params(self):
        from services.auth_service import register_user
        result = register_user("rec2@test.com", "pass", "Rec User2")
        token = result.token
        res = client.post("/api/v1/recommend/outfits", json={
            "occasion": "business",
            "scoring_profile": "minimalist",
            "top_k": 5,
        }, headers=_auth_header(token))
        assert res.status_code == 200
        assert len(res.json()["outfits"]) == 5


class TestChatRoutes:
    def test_chat_flow(self, test_user_id):
        # Get a real token for the test user
        from services.auth_service import generate_token, _token_expiry
        from db import get_db_context
        from models.database import UserToken
        token = generate_token()
        with get_db_context() as db:
            db.add(UserToken(user_id=test_user_id, token=token, expires_at=_token_expiry()))
            db.commit()

        # Start session
        res = client.post("/api/v1/chat/start-session", headers=_auth_header(token))
        assert res.status_code == 200
        sid = res.json()["session_id"]

        # Send message
        res = client.post("/api/v1/chat/message", json={
            "session_id": sid,
            "message": "What should I wear?",
        }, headers=_auth_header(token))
        assert res.status_code == 200
        assert res.json()["response"]

        # Get history
        res = client.get(f"/api/v1/chat/history/{sid}", headers=_auth_header(token))
        assert res.status_code == 200
        assert len(res.json()["messages"]) == 2


class TestWardrobeRoutes:
    def test_add_and_list_garments(self, test_user_id):
        from services.auth_service import generate_token, _token_expiry
        from db import get_db_context
        from models.database import UserToken
        token = generate_token()
        with get_db_context() as db:
            db.add(UserToken(user_id=test_user_id, token=token, expires_at=_token_expiry()))
            db.commit()

        # Add garment
        res = client.post("/api/v1/wardrobe/items", headers=_auth_header(token))
        assert res.status_code == 200
        garment_id = res.json()["id"]

        # List
        res = client.get("/api/v1/wardrobe/items", headers=_auth_header(token))
        assert res.status_code == 200
        assert len(res.json()) == 1

        # Delete
        res = client.delete(f"/api/v1/wardrobe/items/{garment_id}", headers=_auth_header(token))
        assert res.status_code == 200

    def test_toggle_favorite(self, test_user_id):
        from services.auth_service import generate_token, _token_expiry
        from db import get_db_context
        from models.database import UserToken
        token = generate_token()
        with get_db_context() as db:
            db.add(UserToken(user_id=test_user_id, token=token, expires_at=_token_expiry()))
            db.commit()

        res = client.post("/api/v1/wardrobe/items", headers=_auth_header(token))
        gid = res.json()["id"]

        res = client.post(f"/api/v1/wardrobe/items/{gid}/favorite", headers=_auth_header(token))
        assert res.status_code == 200
        assert res.json()["is_favorite"] is True

    def test_wardrobe_stats(self, test_user_id):
        from services.auth_service import generate_token, _token_expiry
        from db import get_db_context
        from models.database import UserToken
        token = generate_token()
        with get_db_context() as db:
            db.add(UserToken(user_id=test_user_id, token=token, expires_at=_token_expiry()))
            db.commit()

        client.post("/api/v1/wardrobe/items", headers=_auth_header(token))
        res = client.get("/api/v1/wardrobe/stats", headers=_auth_header(token))
        assert res.status_code == 200
        assert res.json()["total_items"] == 1


class TestOnboardingRoutes:
    def test_profile_lifecycle(self, test_user_id):
        from services.auth_service import generate_token, _token_expiry
        from db import get_db_context
        from models.database import UserToken
        token = generate_token()
        with get_db_context() as db:
            db.add(UserToken(user_id=test_user_id, token=token, expires_at=_token_expiry()))
            db.commit()

        # Get blank
        res = client.get(f"/api/v1/onboarding/profile/{test_user_id}", headers=_auth_header(token))
        assert res.status_code == 200
        assert res.json()["is_onboarded"] is False

        # Update
        res = client.put(f"/api/v1/onboarding/profile/{test_user_id}", json={
            "height_cm": 180.0,
            "gender": "homme",
        }, headers=_auth_header(token))
        assert res.status_code == 200
        assert res.json()["height_cm"] == 180.0

        # Complete
        res = client.post(f"/api/v1/onboarding/complete/{test_user_id}", headers=_auth_header(token))
        assert res.status_code == 200

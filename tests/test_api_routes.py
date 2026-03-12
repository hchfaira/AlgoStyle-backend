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

        # Get current user
        res = client.get(f"/api/v1/auth/me?token={token}")
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


class TestRecommendationRoutes:
    def test_get_recommendations(self):
        res = client.post("/api/v1/recommend/outfits", json={
            "occasion": "casual",
            "top_k": 3,
        })
        assert res.status_code == 200
        data = res.json()
        assert len(data["outfits"]) == 3
        assert data["total_combinations"] > 0

    def test_recommendations_with_all_params(self):
        res = client.post("/api/v1/recommend/outfits", json={
            "occasion": "business",
            "scoring_profile": "minimalist",
            "top_k": 5,
        })
        assert res.status_code == 200
        assert len(res.json()["outfits"]) == 5


class TestChatRoutes:
    def test_chat_flow(self):
        # Start session
        res = client.post("/api/v1/chat/start-session?user_id=test_user")
        assert res.status_code == 200
        sid = res.json()["session_id"]

        # Send message
        res = client.post("/api/v1/chat/message", json={
            "session_id": sid,
            "message": "What should I wear?",
        })
        assert res.status_code == 200
        assert res.json()["response"]

        # Get history
        res = client.get(f"/api/v1/chat/history/{sid}")
        assert res.status_code == 200
        assert len(res.json()["messages"]) == 2


class TestWardrobeRoutes:
    def test_add_and_list_garments(self):
        # Add garment
        res = client.post("/api/v1/wardrobe/items?user_id=test_user")
        assert res.status_code == 200
        garment_id = res.json()["id"]

        # List
        res = client.get("/api/v1/wardrobe/items?user_id=test_user")
        assert res.status_code == 200
        assert len(res.json()) == 1

        # Delete
        res = client.delete(f"/api/v1/wardrobe/items/{garment_id}?user_id=test_user")
        assert res.status_code == 200

    def test_toggle_favorite(self):
        res = client.post("/api/v1/wardrobe/items?user_id=test_user")
        gid = res.json()["id"]

        res = client.post(f"/api/v1/wardrobe/items/{gid}/favorite?user_id=test_user")
        assert res.status_code == 200
        assert res.json()["is_favorite"] is True

    def test_wardrobe_stats(self):
        client.post("/api/v1/wardrobe/items?user_id=test_user")
        res = client.get("/api/v1/wardrobe/stats?user_id=test_user")
        assert res.status_code == 200
        assert res.json()["total_items"] == 1


class TestOnboardingRoutes:
    def test_profile_lifecycle(self):
        # Get blank
        res = client.get("/api/v1/onboarding/profile/test_user")
        assert res.status_code == 200
        assert res.json()["is_onboarded"] is False

        # Update
        res = client.put("/api/v1/onboarding/profile/test_user", json={
            "height_cm": 180.0,
            "gender": "homme",
        })
        assert res.status_code == 200
        assert res.json()["height_cm"] == 180.0

        # Complete
        res = client.post("/api/v1/onboarding/complete/test_user")
        assert res.status_code == 200

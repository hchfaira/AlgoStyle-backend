"""Tests for profile service."""
import pytest
from fastapi import HTTPException
from services.profile_service import (
    get_profile, update_profile, analyze_body_photo, complete_onboarding,
)
from models.schemas import OnboardingUpdate


class TestGetProfile:
    def test_returns_blank_scaffold(self):
        profile = get_profile("new_user_xyz")
        assert profile.user_id == "new_user_xyz"
        assert profile.name == ""
        assert profile.is_onboarded is False


class TestUpdateProfile:
    def test_update_basic_info(self, test_user_id):
        update = OnboardingUpdate(
            height_cm=175.0,
            weight_kg=70.0,
            gender="homme",
        )
        profile = update_profile(test_user_id, update)
        assert profile.height_cm == 175.0
        assert profile.weight_kg == 70.0
        assert profile.gender == "homme"
        assert profile.is_onboarded is True

    def test_update_style_preferences(self, test_user_id):
        update = OnboardingUpdate(
            style_preferences=["minimalist", "classic"],
            favorite_colors=["navy", "black"],
        )
        profile = update_profile(test_user_id, update)
        assert "minimalist" in profile.style_preferences
        assert "navy" in profile.favorite_colors

    def test_update_preserves_existing(self, test_user_id):
        update1 = OnboardingUpdate(height_cm=180.0)
        update_profile(test_user_id, update1)
        update2 = OnboardingUpdate(weight_kg=80.0)
        profile = update_profile(test_user_id, update2)
        assert profile.height_cm == 180.0
        assert profile.weight_kg == 80.0


class TestAnalyzeBodyPhoto:
    def test_analyze_returns_success(self, test_user_id):
        result = analyze_body_photo(test_user_id, b"fake_image_data")
        assert result["status"] == "success"
        assert result["analysis"]["body_shape"] == "hourglass"
        assert result["analysis"]["skin_tone"] == "medium"


class TestCompleteOnboarding:
    def test_complete_existing_profile(self, test_user_id):
        update = OnboardingUpdate(height_cm=170.0)
        update_profile(test_user_id, update)
        result = complete_onboarding(test_user_id)
        assert result["status"] == "onboarding_complete"

    def test_complete_nonexistent_profile(self):
        with pytest.raises(HTTPException) as exc_info:
            complete_onboarding("nonexistent")
        assert exc_info.value.status_code == 404

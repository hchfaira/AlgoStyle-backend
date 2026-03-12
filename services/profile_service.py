"""
Profile service — user profile and onboarding logic.
"""
from fastapi import HTTPException
from models.schemas import UserProfile, OnboardingUpdate, BodyAnalysis
from services.store import profiles


def get_profile(user_id: str) -> UserProfile:
    """Get user profile, or return a blank scaffold."""
    profile = profiles.get(user_id)
    if not profile:
        return UserProfile(user_id=user_id, name="", email="")
    return profile


def update_profile(user_id: str, update: OnboardingUpdate) -> UserProfile:
    """Update user profile with onboarding data."""
    profile = profiles.get(user_id)
    if not profile:
        profile = UserProfile(user_id=user_id, name="", email="")

    data = update.model_dump(exclude_none=True)
    for key, value in data.items():
        setattr(profile, key, value)

    profile.is_onboarded = True
    profiles[user_id] = profile
    return profile


def analyze_body_photo(user_id: str, image_bytes: bytes) -> dict:
    """
    Analyze a full-body photo.
    V1: Returns mock data. Production: calls Layer 3 pipeline.
    """
    analysis = BodyAnalysis(
        body_shape="hourglass",
        skin_tone="medium",
        undertone="warm",
        hair_color="brunette",
        contrast_level="medium",
        estimated_top_size="M",
        estimated_bottom_size="M",
    )

    profile = profiles.get(user_id)
    if not profile:
        profile = UserProfile(user_id=user_id, name="", email="")
    profile.body_analysis = analysis
    profiles[user_id] = profile

    return {"status": "success", "analysis": analysis.model_dump()}


def complete_onboarding(user_id: str) -> dict:
    """Mark onboarding as complete."""
    profile = profiles.get(user_id)
    if not profile:
        raise HTTPException(404, "Profile not found")
    profile.is_onboarded = True
    profiles[user_id] = profile
    return {"status": "onboarding_complete", "user_id": user_id}

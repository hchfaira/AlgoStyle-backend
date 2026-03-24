"""
Profile service — user profile and onboarding logic.
Uses PostgreSQL database for persistence.
"""
from fastapi import HTTPException
from models.schemas import UserProfile, OnboardingUpdate, BodyAnalysis
from models.database import User, UserProfile as UserProfileDB
from db import get_db_context


def get_profile(user_id: str) -> UserProfile:
    """Get user profile, or return a blank scaffold."""
    with get_db_context() as db:
        user = db.query(User).filter(User.id == user_id).first()
        if not user:
            return UserProfile(user_id=user_id, name="", email="")
        
        profile = db.query(UserProfileDB).filter(UserProfileDB.user_id == user_id).first()
        if not profile:
            return UserProfile(user_id=user_id, name=user.name, email=user.email)
        
        return UserProfile(
            user_id=profile.user_id,
            name=user.name,
            email=user.email,
            height_cm=profile.height_cm,
            weight_kg=profile.weight_kg,
            gender=profile.gender,
            body_analysis=BodyAnalysis(
                body_shape=profile.body_shape,
                skin_tone=profile.skin_tone,
                undertone=profile.undertone,
                hair_color=profile.hair_color,
                contrast_level=profile.contrast_level,
                estimated_top_size=profile.estimated_top_size,
                estimated_bottom_size=profile.estimated_bottom_size,
            ) if profile.body_shape else None,
            profile_photo_url=profile.profile_photo_url,
            style_preferences=profile.style_preferences,
            favorite_colors=profile.favorite_colors,
            avoid_colors=profile.avoid_colors,
            comfort_vs_style=profile.comfort_vs_style,
            budget=profile.budget,
            location=profile.location,
            timezone=profile.timezone,
            bio=profile.bio,
            is_public=profile.is_public if profile.is_public is not None else True,
            is_onboarded=profile.user.is_onboarded if profile.user else False,
            created_at=profile.created_at,
        )


def update_profile(user_id: str, update: OnboardingUpdate) -> UserProfile:
    """Update user profile with onboarding data."""
    with get_db_context() as db:
        user = db.query(User).filter(User.id == user_id).first()
        if not user:
            raise HTTPException(404, "User not found")
        
        profile = db.query(UserProfileDB).filter(UserProfileDB.user_id == user_id).first()
        if not profile:
            profile = UserProfileDB(user_id=user_id)
            db.add(profile)
        
        data = update.model_dump(exclude_none=True)
        for key, value in data.items():
            if hasattr(profile, key):
                setattr(profile, key, value)
        
        user.is_onboarded = True
        db.commit()
        
        return UserProfile(
            user_id=profile.user_id,
            name=user.name,
            email=user.email,
            height_cm=profile.height_cm,
            weight_kg=profile.weight_kg,
            gender=profile.gender,
            profile_photo_url=profile.profile_photo_url,
            style_preferences=profile.style_preferences,
            favorite_colors=profile.favorite_colors,
            avoid_colors=profile.avoid_colors,
            comfort_vs_style=profile.comfort_vs_style,
            budget=profile.budget,
            location=profile.location,
            timezone=profile.timezone,
            bio=profile.bio,
            is_public=profile.is_public if profile.is_public is not None else True,
            is_onboarded=user.is_onboarded,
            created_at=profile.created_at,
        )


def analyze_body_photo(user_id: str, image_bytes: bytes) -> dict:
    """
    Analyze a full-body photo.
    V1: Returns mock data. Production: calls Layer 3 pipeline.
    """
    with get_db_context() as db:
        user = db.query(User).filter(User.id == user_id).first()
        if not user:
            raise HTTPException(404, "User not found")
        
        profile = db.query(UserProfileDB).filter(UserProfileDB.user_id == user_id).first()
        if not profile:
            profile = UserProfileDB(user_id=user_id)
            db.add(profile)
        
        profile.body_shape = "hourglass"
        profile.skin_tone = "medium"
        profile.undertone = "warm"
        profile.hair_color = "brunette"
        profile.contrast_level = "medium"
        profile.estimated_top_size = "M"
        profile.estimated_bottom_size = "M"
        
        db.commit()
        
        analysis = BodyAnalysis(
            body_shape="hourglass",
            skin_tone="medium",
            undertone="warm",
            hair_color="brunette",
            contrast_level="medium",
            estimated_top_size="M",
            estimated_bottom_size="M",
        )
        
        return {"status": "success", "analysis": analysis.model_dump()}


def complete_onboarding(user_id: str) -> dict:
    """Mark onboarding as complete."""
    with get_db_context() as db:
        user = db.query(User).filter(User.id == user_id).first()
        if not user:
            raise HTTPException(404, "User not found")
        
        user.is_onboarded = True
        db.commit()
        
        return {"status": "onboarding_complete", "user_id": user_id}


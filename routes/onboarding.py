"""
Onboarding routes — Profile setup, body analysis, style quiz.
Delegates to services.profile_service.
"""
from fastapi import APIRouter, UploadFile, File
from models.schemas import UserProfile, OnboardingUpdate
from services import profile_service

router = APIRouter()


@router.get("/profile/{user_id}", response_model=UserProfile)
async def get_profile(user_id: str):
    return profile_service.get_profile(user_id)


@router.put("/profile/{user_id}", response_model=UserProfile)
async def update_profile(user_id: str, update: OnboardingUpdate):
    return profile_service.update_profile(user_id, update)


@router.post("/analyze-photo/{user_id}")
async def analyze_body_photo(user_id: str, image: UploadFile = File(...)):
    """Analyze a full-body photo to extract body shape, skin tone, etc."""
    image_bytes = await image.read()
    return profile_service.analyze_body_photo(user_id, image_bytes)


@router.post("/complete/{user_id}")
async def complete_onboarding(user_id: str):
    return profile_service.complete_onboarding(user_id)

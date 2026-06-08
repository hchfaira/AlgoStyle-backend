"""
Onboarding routes — Profile setup, body analysis, style quiz.
Delegates to services.profile_service.
"""
from fastapi import APIRouter, HTTPException, UploadFile, File
from models.schemas import UserProfile, OnboardingUpdate
from services import profile_service
from deps import CurrentUser

router = APIRouter()


@router.get("/profile/{user_id}", response_model=UserProfile)
async def get_profile(current_user: CurrentUser, user_id: str):
    # Enforce that users can only access their own profile
    if current_user["user_id"] != user_id:
        raise HTTPException(status_code=403, detail="Access forbidden")
    return profile_service.get_profile(user_id)


@router.put("/profile/{user_id}", response_model=UserProfile)
async def update_profile(current_user: CurrentUser, user_id: str, update: OnboardingUpdate):
    if current_user["user_id"] != user_id:
        raise HTTPException(status_code=403, detail="Access forbidden")
    return profile_service.update_profile(user_id, update)


@router.post("/analyze-photo/{user_id}")
async def analyze_body_photo(current_user: CurrentUser, user_id: str, image: UploadFile = File(...)):
    """Analyze a full-body photo to extract body shape, skin tone, etc."""
    if current_user["user_id"] != user_id:
        raise HTTPException(status_code=403, detail="Access forbidden")
    image_bytes = await image.read()
    return profile_service.analyze_body_photo(user_id, image_bytes)


@router.post("/complete/{user_id}")
async def complete_onboarding(current_user: CurrentUser, user_id: str):
    if current_user["user_id"] != user_id:
        raise HTTPException(status_code=403, detail="Access forbidden")
    return profile_service.complete_onboarding(user_id)

from fastapi import APIRouter, Query

from models.schemas import SocialFeedResponse, ToggleLikeResponse, ToggleSaveResponse, UserStats
from services import social_service

router = APIRouter(prefix="/api/v1/social", tags=["social"])


@router.get("/stats/{user_id}", response_model=UserStats)
async def get_user_stats(user_id: str):
    return social_service.get_user_stats(user_id)


@router.get("/feed", response_model=SocialFeedResponse)
async def get_social_feed(
    user_id: str,
    limit: int = Query(20, le=50),
    offset: int = Query(0, ge=0),
):
    """Return paginated public outfits for the community People feed."""
    return social_service.get_feed(user_id, limit=limit, offset=offset)


@router.post("/posts/{outfit_id}/like", response_model=ToggleLikeResponse)
async def toggle_like(user_id: str, outfit_id: str):
    """Toggle a like on a public outfit post."""
    return social_service.toggle_like(user_id, outfit_id)


@router.post("/posts/{outfit_id}/save", response_model=ToggleSaveResponse)
async def toggle_save(user_id: str, outfit_id: str):
    """Toggle saving / bookmarking a public outfit post."""
    return social_service.toggle_save(user_id, outfit_id)

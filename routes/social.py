from fastapi import APIRouter, Query

from models.schemas import (
    FollowCounts,
    FollowListResponse,
    FollowRequestsResponse,
    FollowResponse,
    SocialFeedResponse,
    ToggleLikeResponse,
    ToggleSaveResponse,
    UserSearchResponse,
    UserStats,
)
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


# ─── User Search ──────────────────────────────────────────────

@router.get("/users/search", response_model=UserSearchResponse)
async def search_users(
    q: str = Query(..., min_length=1, max_length=100),
    current_user_id: str = Query(...),
    limit: int = Query(20, le=50),
):
    """Search for users by name or email."""
    return social_service.search_users(q, current_user_id, limit=limit)


# ─── Follow endpoints ────────────────────────────────────────

@router.post("/users/{target_user_id}/follow", response_model=FollowResponse)
async def follow_user(user_id: str, target_user_id: str):
    """Follow another user. If the target is private, creates a pending request."""
    return social_service.follow_user(user_id, target_user_id)


@router.post("/users/{target_user_id}/unfollow", response_model=FollowResponse)
async def unfollow_user(user_id: str, target_user_id: str):
    """Unfollow a user (or cancel a pending follow request)."""
    return social_service.unfollow_user(user_id, target_user_id)


@router.get("/users/{user_id}/followers", response_model=FollowListResponse)
async def get_followers(user_id: str, current_user_id: str = Query(...)):
    """List users who follow the given user."""
    return social_service.get_followers(user_id, current_user_id)


@router.get("/users/{user_id}/following", response_model=FollowListResponse)
async def get_following(user_id: str, current_user_id: str = Query(...)):
    """List users the given user is following."""
    return social_service.get_following(user_id, current_user_id)


@router.get("/users/{user_id}/follow-counts", response_model=FollowCounts)
async def get_follow_counts(user_id: str):
    """Get follower/following counts for a user."""
    return social_service.get_follow_counts(user_id)


@router.get("/follow-requests", response_model=FollowRequestsResponse)
async def get_follow_requests(user_id: str):
    """Get pending follow requests for current user."""
    return social_service.get_follow_requests(user_id)


@router.post("/follow-requests/{request_id}/accept", response_model=FollowResponse)
async def accept_follow_request(user_id: str, request_id: str):
    """Accept a pending follow request."""
    return social_service.accept_follow_request(user_id, request_id)


@router.post("/follow-requests/{request_id}/decline", response_model=FollowResponse)
async def decline_follow_request(user_id: str, request_id: str):
    """Decline a pending follow request."""
    return social_service.decline_follow_request(user_id, request_id)

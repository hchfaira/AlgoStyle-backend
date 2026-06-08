"""Custom outfit builder routes — thin handlers delegating to outfit_service."""
from fastapi import APIRouter, UploadFile, File, Form, Query
from typing import Optional
from models.schemas import (
    CreateCustomOutfitRequest,
    UpdateOutfitPlanRequest,
    CustomOutfitResponse,
    CustomOutfit,
)
from services import outfit_service
from deps import CurrentUser

router = APIRouter(prefix="/api/v1/outfits", tags=["outfits"])


@router.post("/create", response_model=CustomOutfitResponse)
async def create_custom_outfit(current_user: CurrentUser, request: CreateCustomOutfitRequest):
    return outfit_service.create_outfit(current_user["user_id"], request)


@router.get("/list", response_model=dict)
async def list_custom_outfits(
    current_user: CurrentUser,
    upcoming_only: bool = Query(False),
    past_only: bool = Query(False),
):
    return outfit_service.list_outfits(current_user["user_id"], upcoming_only=upcoming_only, past_only=past_only)


@router.get("/week", response_model=dict)
async def get_week_outfits(current_user: CurrentUser):
    """Outfits planned in the next 7 days — used by the Week Planner view."""
    return outfit_service.get_planned_this_week(current_user["user_id"])


@router.get("/{outfit_id}", response_model=CustomOutfit)
async def get_custom_outfit(current_user: CurrentUser, outfit_id: str):
    return outfit_service.get_outfit(current_user["user_id"], outfit_id)


@router.patch("/{outfit_id}/plan", response_model=CustomOutfit)
async def update_outfit_plan(current_user: CurrentUser, outfit_id: str, patch: UpdateOutfitPlanRequest):
    """Update the planned_date / reminder / name on an existing outfit."""
    return outfit_service.update_outfit_plan(current_user["user_id"], outfit_id, patch)


@router.delete("/{outfit_id}", response_model=dict)
async def delete_custom_outfit(current_user: CurrentUser, outfit_id: str):
    return outfit_service.delete_outfit(current_user["user_id"], outfit_id)


@router.post("/{outfit_id}/share", response_model=dict)
async def share_custom_outfit(current_user: CurrentUser, outfit_id: str):
    return outfit_service.share_outfit(current_user["user_id"], outfit_id)


@router.post("/{outfit_id}/publish", response_model=dict)
async def publish_outfit(user_id: str, outfit_id: str):
    """Publish an outfit to the community People feed (sets is_public=True)."""
    return outfit_service.publish_outfit(user_id, outfit_id)


@router.post("/score-photo", response_model=dict)
async def score_outfit_photo(
    current_user: CurrentUser,
    image: UploadFile = File(...),
):
    """Upload a photo of a worn outfit — model scores it and proposes improvements."""
    image_bytes = await image.read()
    return outfit_service.score_outfit_photo(current_user["user_id"], image_bytes)


@router.post("/from-prompt", response_model=dict)
async def outfit_from_prompt(
    current_user: CurrentUser,
    prompt: str = Form(...),
):
    """Generate an outfit from a natural language prompt using the user's wardrobe."""
    return outfit_service.outfit_from_prompt(current_user["user_id"], prompt)

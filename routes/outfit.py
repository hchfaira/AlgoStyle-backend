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

router = APIRouter(prefix="/api/v1/outfits", tags=["outfits"])


@router.post("/create", response_model=CustomOutfitResponse)
async def create_custom_outfit(user_id: str, request: CreateCustomOutfitRequest):
    return outfit_service.create_outfit(user_id, request)


@router.get("/list", response_model=dict)
async def list_custom_outfits(
    user_id: str,
    upcoming_only: bool = Query(False),
    past_only: bool = Query(False),
):
    return outfit_service.list_outfits(user_id, upcoming_only=upcoming_only, past_only=past_only)


@router.get("/week", response_model=dict)
async def get_week_outfits(user_id: str):
    """Outfits planned in the next 7 days — used by the Week Planner view."""
    return outfit_service.get_planned_this_week(user_id)


@router.get("/{outfit_id}", response_model=CustomOutfit)
async def get_custom_outfit(user_id: str, outfit_id: str):
    return outfit_service.get_outfit(user_id, outfit_id)


@router.patch("/{outfit_id}/plan", response_model=CustomOutfit)
async def update_outfit_plan(user_id: str, outfit_id: str, patch: UpdateOutfitPlanRequest):
    """Update the planned_date / reminder / name on an existing outfit."""
    return outfit_service.update_outfit_plan(user_id, outfit_id, patch)


@router.delete("/{outfit_id}", response_model=dict)
async def delete_custom_outfit(user_id: str, outfit_id: str):
    return outfit_service.delete_outfit(user_id, outfit_id)


@router.post("/{outfit_id}/share", response_model=dict)
async def share_custom_outfit(user_id: str, outfit_id: str):
    return outfit_service.share_outfit(user_id, outfit_id)


@router.post("/score-photo", response_model=dict)
async def score_outfit_photo(
    user_id: str,
    image: UploadFile = File(...),
):
    """Upload a photo of a worn outfit — model scores it and proposes improvements."""
    image_bytes = await image.read()
    return outfit_service.score_outfit_photo(user_id, image_bytes)


@router.post("/from-prompt", response_model=dict)
async def outfit_from_prompt(
    user_id: str,
    prompt: str = Form(...),
):
    """Generate an outfit from a natural language prompt using the user's wardrobe."""
    return outfit_service.outfit_from_prompt(user_id, prompt)

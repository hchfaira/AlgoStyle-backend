"""Custom outfit builder routes — thin handlers delegating to outfit_service."""
from fastapi import APIRouter
from models.schemas import (
    CreateCustomOutfitRequest,
    CustomOutfitResponse,
    CustomOutfit,
)
from services import outfit_service

router = APIRouter(prefix="/api/v1/outfits", tags=["outfits"])


@router.post("/create", response_model=CustomOutfitResponse)
async def create_custom_outfit(user_id: str, request: CreateCustomOutfitRequest):
    return outfit_service.create_outfit(user_id, request)


@router.get("/list", response_model=dict)
async def list_custom_outfits(user_id: str):
    return outfit_service.list_outfits(user_id)


@router.get("/{outfit_id}", response_model=CustomOutfit)
async def get_custom_outfit(user_id: str, outfit_id: str):
    return outfit_service.get_outfit(user_id, outfit_id)


@router.delete("/{outfit_id}", response_model=dict)
async def delete_custom_outfit(user_id: str, outfit_id: str):
    return outfit_service.delete_outfit(user_id, outfit_id)


@router.post("/{outfit_id}/share", response_model=dict)
async def share_custom_outfit(user_id: str, outfit_id: str):
    return outfit_service.share_outfit(user_id, outfit_id)

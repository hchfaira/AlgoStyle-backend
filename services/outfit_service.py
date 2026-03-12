"""
Outfit service — custom outfit CRUD.
"""
import uuid
from datetime import datetime
from typing import List, Dict, Optional

from fastapi import HTTPException
from models.schemas import (
    CreateCustomOutfitRequest,
    CustomOutfit,
    CustomOutfitResponse,
    GarmentItem,
)
from services.store import custom_outfits


def create_outfit(
    user_id: str,
    request: CreateCustomOutfitRequest,
    garments: Optional[List[GarmentItem]] = None,
) -> CustomOutfitResponse:
    """Create a custom outfit from selected garments."""
    if not garments:
        garments = [
            GarmentItem(
                id=gid,
                name=f"Garment {gid[:8]}",
                category="top",
                color="blue",
                size="M",
                description="Mock item",
            )
            for gid in request.garment_ids
        ]

    outfit = CustomOutfit(
        id=str(uuid.uuid4()),
        name=request.name,
        description=request.description,
        garments=garments,
        is_public=request.is_public,
        created_at=datetime.now(),
        updated_at=datetime.now(),
    )

    custom_outfits.setdefault(user_id, {})[outfit.id] = outfit

    return CustomOutfitResponse(
        success=True,
        outfit=outfit,
        message=f"Outfit '{outfit.name}' created successfully!",
    )


def list_outfits(user_id: str) -> dict:
    """Get all custom outfits for a user."""
    user_outfits = custom_outfits.get(user_id, {})
    return {
        "outfits": list(user_outfits.values()),
        "total": len(user_outfits),
    }


def get_outfit(user_id: str, outfit_id: str) -> CustomOutfit:
    """Get a specific custom outfit."""
    if user_id not in custom_outfits or outfit_id not in custom_outfits[user_id]:
        raise HTTPException(404, "Outfit not found")
    return custom_outfits[user_id][outfit_id]


def delete_outfit(user_id: str, outfit_id: str) -> dict:
    """Delete a custom outfit."""
    if user_id not in custom_outfits or outfit_id not in custom_outfits[user_id]:
        raise HTTPException(404, "Outfit not found")
    outfit_name = custom_outfits[user_id][outfit_id].name
    del custom_outfits[user_id][outfit_id]
    return {"success": True, "message": f"Outfit '{outfit_name}' deleted successfully!"}


def share_outfit(user_id: str, outfit_id: str) -> dict:
    """Generate a shareable link for a custom outfit."""
    if user_id not in custom_outfits or outfit_id not in custom_outfits[user_id]:
        raise HTTPException(404, "Outfit not found")
    outfit = custom_outfits[user_id][outfit_id]
    share_code = str(uuid.uuid4())[:8]
    return {
        "success": True,
        "share_code": share_code,
        "share_url": f"https://algostyle.app/shared-outfit/{share_code}",
        "outfit_name": outfit.name,
    }

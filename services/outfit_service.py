"""
Outfit service — custom outfit CRUD.
Uses PostgreSQL database for persistence.
"""
from datetime import datetime
from typing import List, Optional

from fastapi import HTTPException
from models.schemas import (
    CreateCustomOutfitRequest,
    CustomOutfit,
    CustomOutfitResponse,
    GarmentItem,
)
from models.database import CustomOutfit as CustomOutfitDB
from db import get_db_context


def create_outfit(
    user_id: str,
    request: CreateCustomOutfitRequest,
    garments: Optional[List[GarmentItem]] = None,
) -> CustomOutfitResponse:
    """Create a custom outfit from selected garments."""
    with get_db_context() as db:
        outfit = CustomOutfitDB(
            user_id=user_id,
            name=request.name,
            description=request.description,
            garment_ids=request.garment_ids,
            is_public=request.is_public,
        )
        db.add(outfit)
        db.commit()
        
        return CustomOutfitResponse(
            success=True,
            outfit=CustomOutfit(
                id=outfit.id,
                name=outfit.name,
                description=outfit.description,
                garments=garments or [],
                is_public=outfit.is_public,
                created_at=outfit.created_at,
                updated_at=outfit.updated_at,
            ),
            message=f"Outfit '{outfit.name}' created successfully!",
        )


def list_outfits(user_id: str) -> dict:
    """Get all custom outfits for a user."""
    with get_db_context() as db:
        outfits = db.query(CustomOutfitDB).filter(CustomOutfitDB.user_id == user_id).all()
        return {
            "outfits": [
                CustomOutfit(
                    id=o.id,
                    name=o.name,
                    description=o.description,
                    garments=[],
                    is_public=o.is_public,
                    created_at=o.created_at,
                    updated_at=o.updated_at,
                ).model_dump()
                for o in outfits
            ],
            "total": len(outfits),
        }


def get_outfit(user_id: str, outfit_id: str) -> CustomOutfit:
    """Get a specific custom outfit."""
    with get_db_context() as db:
        outfit = db.query(CustomOutfitDB).filter(
            CustomOutfitDB.id == outfit_id,
            CustomOutfitDB.user_id == user_id
        ).first()
        if not outfit:
            raise HTTPException(404, "Outfit not found")
        
        return CustomOutfit(
            id=outfit.id,
            name=outfit.name,
            description=outfit.description,
            garments=[],
            is_public=outfit.is_public,
            created_at=outfit.created_at,
            updated_at=outfit.updated_at,
        )


def delete_outfit(user_id: str, outfit_id: str) -> dict:
    """Delete a custom outfit."""
    with get_db_context() as db:
        outfit = db.query(CustomOutfitDB).filter(
            CustomOutfitDB.id == outfit_id,
            CustomOutfitDB.user_id == user_id
        ).first()
        if not outfit:
            raise HTTPException(404, "Outfit not found")
        
        outfit_name = outfit.name
        db.delete(outfit)
        db.commit()
        
        return {"success": True, "message": f"Outfit '{outfit_name}' deleted successfully!"}


def share_outfit(user_id: str, outfit_id: str) -> dict:
    """Generate a shareable link for a custom outfit."""
    with get_db_context() as db:
        outfit = db.query(CustomOutfitDB).filter(
            CustomOutfitDB.id == outfit_id,
            CustomOutfitDB.user_id == user_id
        ).first()
        if not outfit:
            raise HTTPException(404, "Outfit not found")
        
        import uuid
        share_code = str(uuid.uuid4())[:8]
        return {
            "success": True,
            "share_code": share_code,
            "share_url": f"https://algostyle.app/shared-outfit/{share_code}",
            "outfit_name": outfit.name,
        }


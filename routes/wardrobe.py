"""
Wardrobe routes — thin handlers delegating to wardrobe_service.
"""
from fastapi import APIRouter, HTTPException, UploadFile, File, Query
from models.schemas import GarmentItem
from typing import Optional, List
from services import wardrobe_service

router = APIRouter()


@router.post("/items", response_model=GarmentItem)
async def add_garment(
    user_id: str,
    category: Optional[str] = None,
    image: Optional[UploadFile] = File(None),
):
    """Add a garment — upload image and optionally specify category."""
    image_bytes = None
    if image:
        image_bytes = await image.read()
    return wardrobe_service.add_garment(user_id, category, image_bytes)


@router.get("/items", response_model=List[GarmentItem])
async def list_garments(
    user_id: str,
    category: Optional[str] = None,
    color: Optional[str] = None,
    season: Optional[str] = None,
    formality: Optional[str] = None,
    pattern: Optional[str] = None,
    is_favorite: Optional[bool] = None,
    for_sale: Optional[bool] = None,
    search: Optional[str] = None,
):
    return wardrobe_service.list_garments(
        user_id, category, color, season, formality, pattern,
        is_favorite, for_sale, search,
    )


@router.get("/items/{garment_id}", response_model=GarmentItem)
async def get_garment(user_id: str, garment_id: str):
    return wardrobe_service.get_garment(user_id, garment_id)


@router.put("/items/{garment_id}", response_model=GarmentItem)
async def update_garment(user_id: str, garment_id: str, updates: dict):
    return wardrobe_service.update_garment(user_id, garment_id, updates)


@router.delete("/items/{garment_id}")
async def delete_garment(user_id: str, garment_id: str):
    return wardrobe_service.delete_garment(user_id, garment_id)


@router.post("/items/{garment_id}/favorite")
async def toggle_favorite(user_id: str, garment_id: str):
    return wardrobe_service.toggle_favorite(user_id, garment_id)


@router.post("/items/{garment_id}/for-sale")
async def toggle_for_sale(user_id: str, garment_id: str):
    return wardrobe_service.toggle_for_sale(user_id, garment_id)


@router.get("/stats")
async def wardrobe_stats(user_id: str):
    return wardrobe_service.get_wardrobe_stats(user_id)


@router.get("/smart-suggestions")
async def smart_suggestions(user_id: str):
    """AI-powered purchase suggestions that maximise outfit combinations."""
    return wardrobe_service.get_smart_suggestions(user_id)


@router.get("/audit")
async def closet_audit(user_id: str):
    """Detect underused, hard-to-combine or outdated items."""
    return wardrobe_service.closet_audit(user_id)


# ── Capsule Intelligence endpoints ───────────────────────────

@router.get("/capsule-score")
async def capsule_score(user_id: str):
    """Compute capsule cohesion score for the user's wardrobe."""
    return wardrobe_service.get_capsule_score(user_id)


@router.get("/items/{garment_id}/analysis")
async def garment_analysis(user_id: str, garment_id: str):
    """Per-garment analysis: versatility, compatibility, impact score."""
    return wardrobe_service.get_garment_analysis(user_id, garment_id)


@router.get("/missing-pieces")
async def missing_pieces(user_id: str, limit: int = Query(5, ge=1, le=10)):
    """Recommend missing capsule pieces ranked by ROI."""
    return wardrobe_service.get_missing_pieces(user_id, limit)


@router.get("/capsule-evolution")
async def capsule_evolution(user_id: str, days: int = Query(90, ge=7, le=365)):
    """Return capsule score evolution over time."""
    return wardrobe_service.get_capsule_evolution(user_id, days)


@router.get("/smart-removal")
async def smart_removal(user_id: str, profile: str = Query("balanced")):
    """Suggest garments to remove based on a declutter profile (minimalist/balanced/generous)."""
    return wardrobe_service.get_smart_removal(user_id, profile)


@router.get("/sort-scores")
async def sort_scores(user_id: str):
    """Return per-garment scores for Versatility / Redundancy / Seasonal / Impact sort modes."""
    return wardrobe_service.get_sort_scores(user_id)


@router.get("/capsule-generate")
async def capsule_generate(
    user_id: str,
    occasion: Optional[str] = None,
    season: Optional[str] = None,
):
    """Generate an optimised capsule for a given occasion and/or season."""
    return wardrobe_service.generate_capsule(user_id, occasion, season)

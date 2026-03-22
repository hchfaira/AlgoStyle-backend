"""
Wardrobe routes — thin handlers delegating to wardrobe_service.
"""
import json
from fastapi import APIRouter, HTTPException, UploadFile, File, Form, Query
from models.schemas import GarmentItem, GarmentExtractionResult, WardrobeInsightsResponse
from typing import Optional, List
from services import wardrobe_service

router = APIRouter()


@router.post("/analyze-image", response_model=GarmentExtractionResult)
async def analyze_garment_image(
    user_id: str,
    mode: str = Form(default="auto"),
    hint_category: Optional[str] = Form(default=None),
    image: UploadFile = File(...),
):
    """
    Analyze an uploaded garment image.
    Returns extracted attributes + quality warnings.
    Does NOT save the garment — call POST /items to persist after confirmation.
    mode: 'outfit' | 'auto'
    """
    image_bytes = await image.read()
    if not image_bytes:
        raise HTTPException(status_code=400, detail="Empty image file")
    return wardrobe_service.analyze_garment_image(image_bytes, mode, hint_category)


@router.post("/items", response_model=GarmentItem)
async def add_garment(
    user_id: str,
    category: Optional[str] = Query(default=None),
    subcategory: Optional[str] = Query(default=None),
    color_primary: Optional[str] = Query(default=None),
    color_hex: Optional[str] = Query(default=None),
    pattern: Optional[str] = Query(default=None),
    material: Optional[str] = Query(default=None),
    formality: Optional[str] = Query(default=None),
    purchase_price: Optional[float] = Query(default=None, description="Optional purchase price for cost-per-wear tracking"),
    llm_attributes_json: Optional[str] = Query(default=None, description="JSON-encoded LLM-native attributes from analyze-image response"),
    image: Optional[UploadFile] = File(default=None),
):
    """
    Save a confirmed garment to the wardrobe.
    All garment attributes come as query params.
    Image is optional multipart — stored if provided.
    llm_attributes_json: JSON string of the llm_attributes field from the
    analyze-image response — stored verbatim to skip re-conversion on future LLM calls.
    """
    if not user_id:
        raise HTTPException(status_code=400, detail="user_id is required")
    image_bytes = None
    if image:
        image_bytes = await image.read()
    attrs = {
        "category": category,
        "subcategory": subcategory,
        "color_primary": color_primary,
        "color_hex": color_hex,
        "pattern": pattern,
        "material": material,
        "formality": formality,
        "purchase_price": purchase_price,
    }
    llm_attrs: Optional[dict] = None
    if llm_attributes_json:
        try:
            llm_attrs = json.loads(llm_attributes_json)
        except Exception:
            pass  # ignore malformed JSON — fall back to slow path
    result = wardrobe_service.add_garment(user_id, category, image_bytes, extra_attrs=attrs, llm_attributes=llm_attrs)
    wardrobe_service.mark_wardrobe_dirty(user_id)
    return result


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
    result = wardrobe_service.update_garment(user_id, garment_id, updates)
    wardrobe_service.mark_wardrobe_dirty(user_id)
    return result


@router.delete("/items/{garment_id}")
async def delete_garment(user_id: str, garment_id: str):
    result = wardrobe_service.delete_garment(user_id, garment_id)
    wardrobe_service.mark_wardrobe_dirty(user_id)
    return result


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


# ── Wardrobe Insights (5 AI features) ───────────────────────

@router.get("/insights", response_model=WardrobeInsightsResponse)
async def wardrobe_insights(
    user_id: str,
    refresh: bool = Query(False, description="Set true to bypass 15-minute cache"),
):
    """
    Returns all 5 wardrobe intelligence features in one call:
      1. Capsule gap analysis — missing foundation pieces
      2. Cost-per-wear ranking — value of each priced garment
      3. Duplicate detection — near-identical items flagged
      4. Occasion coverage — heatmap of occasion gaps
      5. Versatility ranking — most/least outfit-pairable garments
    Cached 15 minutes per user.
    """
    return wardrobe_service.get_wardrobe_insights(user_id, refresh=refresh)

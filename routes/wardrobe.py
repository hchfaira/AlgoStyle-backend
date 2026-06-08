"""
Wardrobe routes — thin handlers delegating to wardrobe_service.
"""
import json
from fastapi import APIRouter, HTTPException, UploadFile, File, Form, Query
from models.schemas import GarmentItem, GarmentExtractionResult, WardrobeInsightsResponse, SmartPurchaseSuggestionsResponse, TravelCapsuleRequest, TravelCapsuleResponse
from typing import Optional, List
from services import wardrobe_service
from deps import CurrentUser

router = APIRouter()


@router.post("/analyze-image", response_model=GarmentExtractionResult)
async def analyze_garment_image(
    current_user: CurrentUser,
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
    current_user: CurrentUser,
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
    user_id = current_user["user_id"]
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


# ── Batch endpoints — add multiple garments from gallery at once ─────────

@router.post("/batch-analyze")
async def batch_analyze_images(
    current_user: CurrentUser,
    mode: str = Form(default="auto"),
    images: List[UploadFile] = File(...),
):
    """
    Analyze multiple garment images in one request.
    Returns a list of extraction results (one per image, in order).
    Each result includes attributes, warnings, and confidence — identical
    to the single /analyze-image response.
    """
    if not images:
        raise HTTPException(status_code=400, detail="At least one image is required")
    if len(images) > 20:
        raise HTTPException(status_code=400, detail="Maximum 20 images per batch")

    results = []
    for img in images:
        image_bytes = await img.read()
        if not image_bytes:
            results.append({
                "attributes": None,
                "warnings": [{"code": "empty_file", "severity": "error", "message": "Empty image file — skipped."}],
                "auto_confirm": False,
                "garments_detected": 0,
                "confidence": 0.0,
                "cropped_image_b64": None,
                "llm_attributes": None,
                "error": "Empty image file",
            })
            continue
        try:
            extraction = wardrobe_service.analyze_garment_image(image_bytes, mode)
            results.append(extraction.dict() if hasattr(extraction, 'dict') else extraction.model_dump())
        except Exception as exc:
            results.append({
                "attributes": None,
                "warnings": [{"code": "analysis_failed", "severity": "error", "message": str(exc)}],
                "auto_confirm": False,
                "garments_detected": 0,
                "confidence": 0.0,
                "cropped_image_b64": None,
                "llm_attributes": None,
                "error": str(exc),
            })
    return results


@router.post("/batch-items")
async def batch_add_garments(
    current_user: CurrentUser,
    garments_json: str = Form(..., description="JSON array of garment objects with attributes and base64 image data"),
):
    """
    Save multiple confirmed garments to the wardrobe in one request.
    garments_json: JSON array where each element has:
      - attributes: { category, subcategory, color_primary, ... }
      - image_base64: base64-encoded JPEG (optional)
      - llm_attributes: dict (optional)

    Returns { added: [...GarmentItem], errors: [...] }
    """
    user_id = current_user["user_id"]
    try:
        garments_list = json.loads(garments_json)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON in garments_json")
    if not isinstance(garments_list, list):
        raise HTTPException(status_code=400, detail="garments_json must be a JSON array")
    if len(garments_list) > 20:
        raise HTTPException(status_code=400, detail="Maximum 20 garments per batch")

    added = []
    errors = []
    import base64 as b64mod
    for idx, g in enumerate(garments_list):
        try:
            attrs = g.get("attributes", {})
            image_b64 = g.get("image_base64")
            llm_attrs = g.get("llm_attributes")
            image_bytes = b64mod.b64decode(image_b64) if image_b64 else None
            extra_attrs = {
                "category": attrs.get("category"),
                "subcategory": attrs.get("subcategory"),
                "color_primary": attrs.get("color_primary"),
                "color_hex": attrs.get("color_hex"),
                "pattern": attrs.get("pattern"),
                "material": attrs.get("material"),
                "formality": attrs.get("formality"),
                "purchase_price": attrs.get("purchase_price"),
            }
            result = wardrobe_service.add_garment(
                user_id,
                attrs.get("category"),
                image_bytes,
                extra_attrs=extra_attrs,
                llm_attributes=llm_attrs,
            )
            added.append(result.dict() if hasattr(result, 'dict') else result.model_dump())
        except Exception as exc:
            errors.append({"index": idx, "error": str(exc)})

    if added:
        wardrobe_service.mark_wardrobe_dirty(user_id)
    return {"added": added, "errors": errors, "total_added": len(added), "total_errors": len(errors)}


@router.get("/items", response_model=List[GarmentItem])
async def list_garments(
    current_user: CurrentUser,
    category: Optional[str] = None,
    color: Optional[str] = None,
    season: Optional[str] = None,
    formality: Optional[str] = None,
    pattern: Optional[str] = None,
    is_favorite: Optional[bool] = None,
    for_sale: Optional[bool] = None,
    search: Optional[str] = None,
):
    user_id = current_user["user_id"]
    return wardrobe_service.list_garments(
        user_id, category, color, season, formality, pattern,
        is_favorite, for_sale, search,
    )


@router.get("/items/{garment_id}", response_model=GarmentItem)
async def get_garment(current_user: CurrentUser, garment_id: str):
    return wardrobe_service.get_garment(current_user["user_id"], garment_id)


@router.put("/items/{garment_id}", response_model=GarmentItem)
async def update_garment(current_user: CurrentUser, garment_id: str, updates: dict):
    user_id = current_user["user_id"]
    result = wardrobe_service.update_garment(user_id, garment_id, updates)
    wardrobe_service.mark_wardrobe_dirty(user_id)
    return result


@router.delete("/items/{garment_id}")
async def delete_garment(current_user: CurrentUser, garment_id: str):
    user_id = current_user["user_id"]
    result = wardrobe_service.delete_garment(user_id, garment_id)
    wardrobe_service.mark_wardrobe_dirty(user_id)
    return result


@router.post("/items/{garment_id}/favorite")
async def toggle_favorite(current_user: CurrentUser, garment_id: str):
    return wardrobe_service.toggle_favorite(current_user["user_id"], garment_id)


@router.post("/items/{garment_id}/for-sale")
async def toggle_for_sale(current_user: CurrentUser, garment_id: str):
    return wardrobe_service.toggle_for_sale(current_user["user_id"], garment_id)


@router.post("/items/{garment_id}/mark-worn")
async def mark_worn(current_user: CurrentUser, garment_id: str):
    """
    Increment times_worn + worn_count for a garment and set last_worn to now.
    Can be called from the wardrobe tab (manual) or automatically when an outfit
    is scheduled/confirmed as worn.
    """
    return wardrobe_service.mark_garment_worn(current_user["user_id"], garment_id)


@router.post("/outfits/mark-worn")
async def mark_outfit_worn(current_user: CurrentUser, garment_ids: List[str]):
    """
    Mark all garments of an outfit as worn in one call.
    Called when the user confirms an outfit from the planner.
    """
    user_id = current_user["user_id"]
    results = []
    for gid in garment_ids:
        try:
            results.append(wardrobe_service.mark_garment_worn(user_id, gid))
        except Exception:
            pass  # skip missing garments silently
    wardrobe_service.mark_wardrobe_dirty(user_id)
    return {"marked": len(results), "garment_ids": garment_ids}


@router.get("/items/{garment_id}/smart-scores")
async def get_smart_scores(current_user: CurrentUser, garment_id: str):
    """
    Poll for Smart Add enrichment scores for a garment.
    Returns smart_add_scores dict with status: pending | done | failed.
    Called by the mobile app after adding a garment to check enrichment progress.
    """
    garment = wardrobe_service.get_garment(current_user["user_id"], garment_id)
    scores = garment.smart_add_scores or {"status": "pending"}
    return scores


@router.get("/stats")
async def wardrobe_stats(current_user: CurrentUser):
    return wardrobe_service.get_wardrobe_stats(current_user["user_id"])


@router.get("/smart-suggestions")
async def smart_suggestions(current_user: CurrentUser):
    """Legacy endpoint — redirects to smart-purchase-suggestions."""
    return wardrobe_service.get_smart_suggestions(current_user["user_id"])


@router.get("/smart-purchase-suggestions", response_model=SmartPurchaseSuggestionsResponse)
async def smart_purchase_suggestions(current_user: CurrentUser, max_suggestions: int = Query(default=6, ge=1, le=12)):
    """
    AI-powered 'What to Buy Next' — Phase 1.

    Returns prioritised purchase suggestions ranked by wardrobe impact score:
      purchase_impact_score = 0.40 × outfit_unlock
                            + 0.25 × gap_severity
                            + 0.20 × occasion_gap
                            + 0.15 × bottleneck_bonus

    Each suggestion includes: category, description, reason, outfit_increase,
    suggested_colors, target_occasions, and a composite impact score.

    Also returns: wardrobe gaps, occasion coverage, bottleneck, dominant colors.
    source: "llm" | "fallback"
    """
    return wardrobe_service.get_smart_purchase_suggestions(current_user["user_id"], max_suggestions=max_suggestions)


@router.get("/audit")
async def closet_audit(current_user: CurrentUser):
    """Detect underused, hard-to-combine or outdated items."""
    return wardrobe_service.closet_audit(current_user["user_id"])


# ── Capsule Intelligence endpoints ───────────────────────────

@router.get("/capsule-score")
async def capsule_score(current_user: CurrentUser):
    """Compute capsule cohesion score for the user's wardrobe."""
    return wardrobe_service.get_capsule_score(current_user["user_id"])


@router.get("/items/{garment_id}/analysis")
async def garment_analysis(current_user: CurrentUser, garment_id: str):
    """Per-garment analysis: versatility, compatibility, impact score."""
    return wardrobe_service.get_garment_analysis(current_user["user_id"], garment_id)


@router.get("/missing-pieces")
async def missing_pieces(current_user: CurrentUser, limit: int = Query(5, ge=1, le=10)):
    """Recommend missing capsule pieces ranked by ROI."""
    return wardrobe_service.get_missing_pieces(current_user["user_id"], limit)


@router.get("/capsule-evolution")
async def capsule_evolution(current_user: CurrentUser, days: int = Query(90, ge=7, le=365)):
    """Return capsule score evolution over time."""
    return wardrobe_service.get_capsule_evolution(current_user["user_id"], days)


@router.get("/smart-removal")
async def smart_removal(current_user: CurrentUser, profile: str = Query("balanced")):
    """Suggest garments to remove based on a declutter profile (minimalist/balanced/generous)."""
    return wardrobe_service.get_smart_removal(current_user["user_id"], profile)


@router.get("/sort-scores")
async def sort_scores(current_user: CurrentUser):
    """Return per-garment scores for Versatility / Redundancy / Seasonal / Impact sort modes."""
    return wardrobe_service.get_sort_scores(current_user["user_id"])


@router.get("/capsule-generate")
async def capsule_generate(
    current_user: CurrentUser,
    occasion: Optional[str] = None,
    season: Optional[str] = None,
):
    """Generate an optimised capsule for a given occasion and/or season."""
    return wardrobe_service.generate_capsule(current_user["user_id"], occasion, season)


# ── Wardrobe Insights (5 AI features) ───────────────────────

@router.get("/insights", response_model=WardrobeInsightsResponse)
async def wardrobe_insights(
    current_user: CurrentUser,
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
    return wardrobe_service.get_wardrobe_insights(current_user["user_id"], refresh=refresh)


@router.post("/travel-capsule", response_model=TravelCapsuleResponse)
async def travel_capsule(current_user: CurrentUser, request: TravelCapsuleRequest):
    """
    Generate a travel or contextual capsule from the user's wardrobe.
    Occasions: travel, work_trip, weekend, city_break, beach, date_night.
    Climate: warm | cold | mixed.

    Algorithm runs in LLM_project (port 8001) — this endpoint fetches the
    user's garments from the DB, then delegates to the capsule service.
    """
    return await wardrobe_service.get_travel_capsule_proxy(request)

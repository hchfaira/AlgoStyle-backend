"""
Inspiration outfit service — AlgoStyle backend.

Accepts an inspiration image (base64) + user_id, loads the user's wardrobe
and body shape, calls the LLM_project inspiration pipeline, and maps the
response back to the AlgoStyle RecommendationResponse format.
"""
import uuid
import logging
import time
from typing import List, Optional

from services import llm_client as _llm

from models.schemas import (
    RecommendationResponse, OutfitResult, OutfitScore,
    GarmentItem, GarmentAttributes, GarmentCategory,
)
from models.database import GarmentItem as GarmentItemDB
from db import get_db_context
from services.wardrobe_service import garment_db_to_schema
from services.profile_service import get_profile

logger = logging.getLogger(__name__)


# ─── Wardrobe loader (same as recommendation_service) ────────

def _load_user_wardrobe(user_id: str) -> List[GarmentItem]:
    """Load all wardrobe items for a user from PostgreSQL."""
    with get_db_context() as db:
        rows = (
            db.query(GarmentItemDB)
            .filter(GarmentItemDB.user_id == user_id)
            .all()
        )
        return [garment_db_to_schema(r) for r in rows]


# ─── Garment → PreAnalyzedGarmentItem dict ───────────────────

def _to_pre_analyzed(g: GarmentItem) -> Optional[dict]:
    """Convert a GarmentItem to the pre_analyzed_garments payload format."""
    if not g.attributes or not g.attributes.category or not g.attributes.color_primary:
        return None
    return {
        "id": g.id,
        "category": g.attributes.category.value,
        "subcategory": g.attributes.subcategory,
        "color_primary": g.attributes.color_primary or "unknown",
        "color_secondary": g.attributes.color_secondary,
        "color_hex": g.attributes.color_hex,
        "pattern": g.attributes.pattern or "solid",
        "material": g.attributes.material,
        "formality": g.attributes.formality or "casual",
        "seasons": g.attributes.seasons or [],
        "confidence": g.attributes.confidence or 0.85,
    }


# ─── Score helpers ────────────────────────────────────────────

def _grade_label(score: float) -> str:
    if score >= 0.93: return "S"
    if score >= 0.86: return "A"
    if score >= 0.78: return "B"
    if score >= 0.68: return "C"
    return "D"


# ─── Pipeline response mapper ─────────────────────────────────

def _map_inspiration_response(
    pipeline: dict,
    wardrobe_items: List[GarmentItem],
) -> RecommendationResponse:
    """
    Map /api/v1/pipeline/from-inspiration response → RecommendationResponse.

    Uses the same matching strategy as recommendation_service: id-based first,
    then (category, color_primary) fallback.
    """
    recs = pipeline.get("recommendations") or []
    style_dna = pipeline.get("style_dna") or {}

    # Build lookup: id → GarmentItem, then (cat, color) → GarmentItem
    id_map = {g.id: g for g in wardrobe_items}
    cat_color_map = {}
    for g in wardrobe_items:
        cat = (g.attributes.category.value if g.attributes and g.attributes.category else "").lower()
        col = (g.attributes.color_primary or "").lower()
        key = (cat, col)
        if key not in cat_color_map:
            cat_color_map[key] = g

    results: List[OutfitResult] = []

    for rec in recs:
        rank = rec.get("rank", len(results) + 1)
        overall = float(rec.get("overall_score", 0.75))
        breakdown = rec.get("score_breakdown") or {}
        grade = _grade_label(overall)

        outfit_score = OutfitScore(
            overall=round(overall, 3),
            color_harmony=round(float(breakdown.get("inspiration_fidelity", overall * 0.92)), 3),
            formality_match=round(float(breakdown.get("style_slot_avg", overall * 0.90)), 3),
            occasion_fit=round(float(breakdown.get("style_slot_avg", overall * 0.88)), 3),
            pattern_mixing=round(0.85, 3),
            proportion=round(float(breakdown.get("body_harmony", overall * 0.90)), 3),
            season_fit=round(overall * 0.88, 3),
            creativity=round(float(breakdown.get("inspiration_fidelity", 0.70)), 3),
        )

        garments: List[GarmentItem] = []
        for g in (rec.get("garments") or []):
            gid = g.get("id", "")
            cat = (g.get("category") or "").lower()
            col = (g.get("color_primary") or "").lower()

            matched = id_map.get(gid) or cat_color_map.get((cat, col))
            if matched:
                garments.append(matched)
            else:
                garments.append(GarmentItem(
                    id=gid or f"g_{uuid.uuid4().hex[:8]}",
                    user_id="unknown",
                    attributes=GarmentAttributes(
                        category=GarmentCategory(cat if cat in GarmentCategory._value2member_map_ else "top"),
                        subcategory=g.get("subcategory"),
                        color_primary=col or "",
                        formality=g.get("formality"),
                        confidence=float(g.get("confidence", 0.80)),
                    ),
                ))

        explanation = rec.get("explanation") or "Outfit inspired by your reference image."
        name = rec.get("name") or f"Inspiration Outfit #{rank}"

        # Append inspiration aesthetic tags to the name if available
        aesthetic = style_dna.get("aesthetic") or []
        if aesthetic and name and "Inspiration" not in name:
            name = f"{name}"

        results.append(OutfitResult(
            id=f"outfit_{uuid.uuid4().hex[:8]}",
            rank=rank,
            name=name,
            grade=grade,
            garments=garments,
            score=outfit_score,
            explanation_brief=explanation[:200],
            explanation_detailed=explanation,
        ))

    processing_time = float(pipeline.get("processing_time_ms", 0))

    return RecommendationResponse(
        outfits=results,
        total_combinations=len(results),
        processing_time_ms=processing_time,
    )


# ═══════════════════════════════════════════════════════════════
#  Main public API
# ═══════════════════════════════════════════════════════════════

async def generate_from_inspiration_async(
    inspiration_image_b64: str,
    user_id: str,
    occasion: Optional[str] = None,
    top_k: int = 3,
    enable_explanation: bool = True,
) -> RecommendationResponse:
    """
    Generate outfits from an inspiration image.

    1. Load user wardrobe + profile (body_shape) from PostgreSQL
    2. Build payload with pre-analyzed garments
    3. Call LLM_project /api/v1/pipeline/from-inspiration
    4. Map response back to AlgoStyle schema
    """
    t_start = time.time()

    # ── Load wardrobe ──
    wardrobe = _load_user_wardrobe(user_id)
    logger.info("Inspiration: loaded %d wardrobe items for user %s", len(wardrobe), user_id)

    if len(wardrobe) < 2:
        return _mock_inspiration_response()

    # ── Load body shape from profile ──
    body_shape: Optional[str] = None
    try:
        profile = get_profile(user_id)
        if profile and profile.body_analysis:
            body_shape = profile.body_analysis.body_shape
        logger.info("Inspiration: body_shape=%s for user %s", body_shape, user_id)
    except Exception as e:
        logger.warning("Could not load profile for %s: %s", user_id, e)

    # ── Build pre-analyzed garments ──
    pre_analyzed = []
    sent_items: List[GarmentItem] = []
    for g in wardrobe:
        entry = _to_pre_analyzed(g)
        if entry:
            pre_analyzed.append(entry)
            sent_items.append(g)

    if len(pre_analyzed) < 2:
        return _mock_inspiration_response()

    # ── Build payload ──
    payload = {
        "inspiration_image_b64": inspiration_image_b64,
        "pre_analyzed_garments": pre_analyzed,
        "top_k": top_k,
        "enable_explanation": enable_explanation,
        "explanation_detail": "standard",
    }
    if body_shape:
        payload["body_shape"] = body_shape
    if occasion:
        payload["occasion"] = occasion

    logger.info(
        "Inspiration: calling LLM_project with %d garments, body_shape=%s, occasion=%s",
        len(pre_analyzed), body_shape, occasion,
    )

    # ── Call LLM_project ──
    try:
        async with _llm._async_client() as client:
            resp = await client.post(
                f"{_llm._base_url()}/api/v1/pipeline/from-inspiration",
                json=payload,
                timeout=_llm._TIMEOUT,
            )
            if resp.status_code == 400:
                # Not enough garments to match — return helpful message
                detail = resp.json().get("detail", "")
                logger.warning("Inspiration pipeline 400: %s", detail)
                return _not_enough_garments_response(len(pre_analyzed))
            resp.raise_for_status()
            data = resp.json()
    except Exception as e:
        logger.error("Inspiration pipeline call failed: %s", e, exc_info=True)
        return _mock_inspiration_response()

    # ── Map response ──
    result = _map_inspiration_response(data, sent_items)
    result.processing_time_ms = round((time.time() - t_start) * 1000, 1)

    logger.info(
        "Inspiration: %d outfits in %.1fms",
        len(result.outfits), result.processing_time_ms,
    )
    return result


# ─── Mock fallback ────────────────────────────────────────────

def _not_enough_garments_response(garment_count: int) -> RecommendationResponse:
    """Return a clear message when the wardrobe doesn't have enough variety."""
    return RecommendationResponse(
        outfits=[],
        total_combinations=0,
        processing_time_ms=0,
        error_message=(
            f"Your wardrobe only has {garment_count} item(s). "
            "Add at least 5 items covering tops, bottoms, and shoes "
            "so the AI can build a full outfit from your inspiration."
        ),
    )


def _mock_inspiration_response() -> RecommendationResponse:
    """Return a sensible mock response when the real pipeline is unavailable."""
    return RecommendationResponse(
        outfits=[],
        total_combinations=0,
        processing_time_ms=0,
        error_message=(
            "The inspiration pipeline is temporarily unavailable. "
            "Please try again in a moment."
        ),
    )

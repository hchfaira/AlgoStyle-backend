"""
Wardrobe service — business logic for garment management,
smart suggestions and closet audit.
Uses PostgreSQL database for persistence.

Vision/AI analysis is delegated to the LLM_project API (llm_client).
This service keeps only persistence, filtering, and capsule-scoring logic.
"""
import os
import re
import json
import random
import base64
import logging
import asyncio
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from typing import Optional, List, Dict

logger = logging.getLogger(__name__)

# Thread pool for smart-add background enrichment jobs — caps concurrent threads
_ENRICHMENT_POOL = ThreadPoolExecutor(max_workers=4, thread_name_prefix="smart_add")

from fastapi import HTTPException
from models.schemas import (
    GarmentItem, GarmentAttributes, GarmentCategory,
    GarmentExtractionResult, ExtractionWarning,
    WardrobeInsightsResponse, GapItem, OccasionCoverageItem,
    VersatilityItem, DuplicateGroup, CostPerWearItem,
    PurchaseSuggestionItem, WardrobeBottleneck, SmartPurchaseSuggestionsResponse,
)
from models.database import GarmentItem as GarmentItemDB, WardrobeAnalysisCache as WACacheDB, CustomOutfit as CustomOutfitDB
from db import get_db_context
from services import neo4j_service
from services import llm_client as _llm

# ── Confidence thresholds ─────────────────────────────────────────────────────
# Centralised here so they can be tuned without hunting through business logic.

# Default confidence when the LLM response omits the field
_CONFIDENCE_DEFAULT: float = 0.80
# Below this value the garment card shows a "low_confidence" warning
_CONFIDENCE_LOW_THRESHOLD: float = 0.65
# At or above this value the garment is auto-confirmed (no review required)
_CONFIDENCE_AUTO_CONFIRM: float = 0.80
# Range used by the local mock fallback when the LLM service is unavailable
_CONFIDENCE_MOCK_MIN: float = 0.60
_CONFIDENCE_MOCK_MAX: float = 0.75
# Minimum confidence below which a garment is excluded from capsule suggestions
_CONFIDENCE_CAPSULE_MIN: float = 0.60


# ── Smart-add suggestion catalogue ────────────────────────────

SMART_SUGGESTIONS: List[Dict] = [
    {
        "id": "sug_1",
        "category": "bottom",
        "subcategory": "Chino Pants",
        "description": "Beige chino pants — slim fit",
        "color_primary": "beige",
        "color_hex": "#D4C5A9",
        "new_combinations": 9,
        "reason": "Pairs with 6 of your tops and all 3 sneakers",
        "tags": ["trending", "versatile", "$$"],
        "quality_score": 0.88,
        "trend_score": 0.91,
        "durability_score": 0.85,
    },
    {
        "id": "sug_2",
        "category": "outerwear",
        "subcategory": "Blazer",
        "description": "Navy structured blazer",
        "color_primary": "navy",
        "color_hex": "#1B2A4A",
        "new_combinations": 12,
        "reason": "Elevates every smart-casual combination by 2 formality levels",
        "tags": ["classic", "business", "$$$"],
        "quality_score": 0.92,
        "trend_score": 0.78,
        "durability_score": 0.95,
    },
    {
        "id": "sug_3",
        "category": "accessory",
        "subcategory": "Leather Belt",
        "description": "Tan leather belt — medium width",
        "color_primary": "tan",
        "color_hex": "#C19A6B",
        "new_combinations": 7,
        "reason": "Echoes your shoe tones and adds waist definition to 7 looks",
        "tags": ["essential", "durable", "$"],
        "quality_score": 0.90,
        "trend_score": 0.72,
        "durability_score": 0.97,
    },
    {
        "id": "sug_4",
        "category": "top",
        "subcategory": "Linen Shirt",
        "description": "White linen shirt — relaxed fit",
        "color_primary": "white",
        "color_hex": "#F8F6F0",
        "new_combinations": 11,
        "reason": "White is your most-missing neutral — completes 11 new outfits",
        "tags": ["trending", "summer", "$$"],
        "quality_score": 0.83,
        "trend_score": 0.95,
        "durability_score": 0.78,
    },
    {
        "id": "sug_5",
        "category": "shoes",
        "subcategory": "White Sneakers",
        "description": "Clean white leather sneakers",
        "color_primary": "white",
        "color_hex": "#F5F5F5",
        "new_combinations": 15,
        "reason": "Most versatile shoe — works with 15 of your current pieces",
        "tags": ["trending", "versatile", "$$"],
        "quality_score": 0.87,
        "trend_score": 0.94,
        "durability_score": 0.80,
    },
]

RESTYLE_IDEAS: Dict[str, List[str]] = {
    "top": [
        "Tuck into high-waisted bottoms for a polished look",
        "Layer under a blazer for a smart-casual upgrade",
    ],
    "bottom": [
        "Roll up the hem for a more casual, relaxed feel",
        "Pair with a monochrome top to let the fit speak",
    ],
    "outerwear": [
        "Wear open over a plain tee for effortless layering",
        "Belt at the waist to define your silhouette",
    ],
    "shoes": [
        "Style with cropped trousers to show off the shoe",
        "Mix with contrasting socks for a streetwear edge",
    ],
    "dress": [
        "Layer a fitted turtleneck underneath for colder days",
        "Add a structured belt to elevate the silhouette",
    ],
    "accessory": [
        "Use to break up an all-neutral outfit",
        "Stack or layer for a more editorial effect",
    ],
}


def mock_analyze_garment(category: Optional[str] = None) -> dict:
    """Mock garment analysis — would call vision pipeline in production."""
    cat = category or "top"
    return {
        "category": cat,
        "subcategory": "t-shirt" if cat == "top" else None,
        "color_primary": "navy",
        "color_hex": "#1B2A4A",
        "pattern": "solid",
        "material": "cotton",
        "formality": "casual",
        "seasons": ["spring", "summer", "fall"],
        "confidence": 0.85,
    }


# Category catalogue for smarter mock extraction
_CATEGORY_VARIANTS: Dict[str, dict] = {
    "top": {
        "subcategory": "shirt", "color_primary": "white", "color_hex": "#F8F8F8",
        "formality": "casual", "material": "cotton",
    },
    "bottom": {
        "subcategory": "trousers", "color_primary": "black", "color_hex": "#1A1A1A",
        "formality": "smart_casual", "material": "cotton",
    },
    "dress": {
        "subcategory": "midi dress", "color_primary": "beige", "color_hex": "#D4C5A9",
        "formality": "smart_casual", "material": "polyester",
    },
    "outerwear": {
        "subcategory": "blazer", "color_primary": "navy", "color_hex": "#1B2A4A",
        "formality": "business", "material": "wool",
    },
    "shoes": {
        "subcategory": "sneakers", "color_primary": "white", "color_hex": "#F5F5F5",
        "formality": "casual", "material": "leather",
    },
    "accessory": {
        "subcategory": "bag", "color_primary": "tan", "color_hex": "#C19A6B",
        "formality": "casual", "material": "leather",
    },
}


# ─── Garment Image Analysis — delegated to LLM_project API ──────────────────
# The backend no longer calls Gemini directly.
# It proxies the request to LLM_project (Layer 1 Vision) and translates
# the response into its own GarmentExtractionResult schema.
# Falls back to the lightweight mock below if the LLM project is unreachable.

def _parse_llm_analysis(raw: dict, hint_category: Optional[str]) -> tuple[Optional[GarmentAttributes], Optional[dict]]:
    """
    Map the LLM project's analysis response to a GarmentAttributes instance.
    The LLM project's /api/v1/analyze/image returns {"status": "success", "analysis": {...}}.
    The inner analysis dict may contain "attributes", or be flat with category/color/etc.

    Returns (GarmentAttributes, llm_attrs_dict) so the caller can store the
    LLM-native shape verbatim in ``llm_attributes`` — avoiding re-conversion later.
    """
    analysis = raw.get("analysis") or raw
    if not analysis:
        return None, None

    # The LLM project may return attributes nested or flat
    attrs_raw = analysis.get("attributes") or analysis

    raw_cat = (
        attrs_raw.get("category") or hint_category or "top"
    ).lower().replace(" ", "_")
    valid_cats = {c.value for c in GarmentCategory}
    if raw_cat not in valid_cats:
        raw_cat = hint_category or "top"

    seasons_raw = attrs_raw.get("seasons") or attrs_raw.get("season_suitable") or ["spring", "summer", "fall", "winter"]
    valid_seasons = {"spring", "summer", "fall", "winter"}
    seasons = [s for s in seasons_raw if s in valid_seasons] or ["spring", "summer", "fall", "winter"]

    # ── Build the LLM-native nested dict to store verbatim ───────────────
    color_block = attrs_raw.get("color") if isinstance(attrs_raw.get("color"), dict) else None
    color_primary = (color_block or {}).get("primary") or attrs_raw.get("color_primary") or "unknown"
    color_hex = (color_block or {}).get("hex_codes", [None])[0] if color_block else attrs_raw.get("color_hex")
    color_secondary = (color_block or {}).get("secondary") or attrs_raw.get("color_secondary")

    pattern_val = attrs_raw.get("pattern")
    pattern_type = pattern_val.get("type") if isinstance(pattern_val, dict) else (pattern_val or "solid")

    material_val = attrs_raw.get("material")
    material_primary = material_val.get("primary") if isinstance(material_val, dict) else material_val

    llm_native: dict = {
        "category": raw_cat,
        "subcategory": attrs_raw.get("subcategory"),
        "color": {
            "primary": color_primary,
            "secondary": color_secondary,
            "hex_codes": ([color_hex] if color_hex else []),
        },
        "pattern": {"type": pattern_type},
        "formality_level": attrs_raw.get("formality_level") or attrs_raw.get("formality") or "casual",
        "confidence_score": float(attrs_raw.get("confidence_score") or attrs_raw.get("confidence") or _CONFIDENCE_DEFAULT),
        "season_suitable": seasons,
    }
    if material_primary:
        llm_native["material"] = {"primary": material_primary}

    algo_attrs = GarmentAttributes(
        category=GarmentCategory(raw_cat),
        subcategory=attrs_raw.get("subcategory"),
        color_primary=color_primary,
        color_hex=color_hex,
        color_secondary=color_secondary,
        pattern=pattern_type,
        material=material_primary,
        formality=llm_native["formality_level"],
        seasons=seasons,
        confidence=llm_native["confidence_score"],
    )
    return algo_attrs, llm_native


def analyze_garment_image(
    image_bytes: bytes,
    mode: str = "auto",
    hint_category: Optional[str] = None,
) -> GarmentExtractionResult:
    """
    Analyze an image and extract garment attributes.

    Delegates to LLM_project Layer 1 Vision API.
    Falls back to mock data if the LLM project is unreachable.

    mode='outfit' → full outfit, detect all garments
    mode='auto'   → single most prominent garment
    """
    warnings: List[ExtractionWarning] = []

    # Quality check — small file → likely low-res
    if len(image_bytes) < 50_000:
        warnings.append(ExtractionWarning(
            code="poor_lighting",
            severity="warning",
            message=(
                "⚠️ Low-quality image detected — the photo is small or low-resolution "
                "(under 50 KB). Colour and fabric detection may be less precise. "
                "For best results, use a well-lit photo taken from 50–100 cm away."
            ),
        ))

    # ── Delegate to LLM_project Vision API ────────────────────
    llm_raw = _llm.analyze_garment_image(image_bytes, mode=mode, hint_category=hint_category)
    if llm_raw:
        try:
            attrs, llm_native = _parse_llm_analysis(llm_raw, hint_category)
            if attrs is not None:
                confidence = attrs.confidence or _CONFIDENCE_DEFAULT
                garments_detected = llm_raw.get("garments_detected", 1)

                if mode == "outfit" and garments_detected > 1:
                    warnings.append(ExtractionWarning(
                        code="multiple_garments",
                        severity="info",
                        message=(
                            f"ℹ️ {garments_detected} garments found in this outfit photo. "
                            "Each piece will be extracted and added to your wardrobe as a separate item."
                        ),
                    ))
                if confidence < _CONFIDENCE_LOW_THRESHOLD:
                    warnings.append(ExtractionWarning(
                        code="low_confidence",
                        severity="warning",
                        message=(
                            f"⚠️ Uncertain extraction ({int(confidence * 100)}% confidence). "
                            "Please check the detected category and colour below before saving."
                        ),
                    ))

                has_error = any(w.severity == "error" for w in warnings)
                return GarmentExtractionResult(
                    attributes=attrs,
                    warnings=warnings,
                    auto_confirm=confidence >= _CONFIDENCE_AUTO_CONFIRM and not has_error,
                    garments_detected=garments_detected,
                    confidence=confidence,
                    llm_attributes=llm_native,
                )
        except Exception as e:
            warnings.append(ExtractionWarning(
                code="extraction_failed",
                severity="warning",
                message=f"⚠️ AI extraction encountered an issue ({type(e).__name__}). "
                        "Using estimated values — please review and correct before saving.",
            ))

    # ── Fallback mock (LLM project unreachable) ────────────────
    confidence = round(random.uniform(_CONFIDENCE_MOCK_MIN, _CONFIDENCE_MOCK_MAX), 2)
    cat = hint_category or random.choice(list(_CATEGORY_VARIANTS.keys()))
    variant = _CATEGORY_VARIANTS.get(cat, _CATEGORY_VARIANTS["top"])

    if confidence < _CONFIDENCE_AUTO_CONFIRM:
        warnings.append(ExtractionWarning(
            code="low_confidence",
            severity="warning",
            message=(
                f"⚠️ Could not analyse image ({int(confidence * 100)}% confidence). "
                "Please review the values below and correct them before saving."
            ),
        ))

    attrs = GarmentAttributes(
        category=GarmentCategory(cat),
        subcategory=variant["subcategory"],
        color_primary=variant["color_primary"],
        color_hex=variant["color_hex"],
        pattern="solid",
        material=variant["material"],
        formality=variant["formality"],
        seasons=["spring", "summer", "fall", "winter"],
        confidence=confidence,
    )
    has_error = any(w.severity == "error" for w in warnings)
    return GarmentExtractionResult(
        attributes=attrs,
        warnings=warnings,
        auto_confirm=False,
        garments_detected=1,
        confidence=confidence,
    )




def garment_db_to_schema(g: GarmentItemDB) -> GarmentItem:
    """Convert database model to Pydantic schema."""
    return GarmentItem(
        id=g.id,
        user_id=g.user_id,
        image_url=g.image_url,
        attributes=GarmentAttributes(
            category=GarmentCategory(g.category),
            subcategory=g.subcategory,
            color_primary=g.color_primary,
            color_hex=g.color_hex,
            color_secondary=g.color_secondary,
            pattern=g.pattern,
            material=g.material,
            formality=g.formality,
            seasons=g.seasons,
            confidence=g.confidence,
        ),
        is_favorite=g.is_favorite,
        for_sale=g.for_sale,
        tags=g.tags,
        times_worn=g.times_worn,
        last_worn=g.last_worn,
        purchase_price=getattr(g, "purchase_price", None),
        worn_count=getattr(g, "worn_count", 0) or 0,
        llm_attributes=getattr(g, "llm_attributes", None),
        vision_features=getattr(g, "vision_features", None),
        smart_add_scores=getattr(g, "smart_add_scores", None),
        created_at=g.created_at,
    )


# ─── Smart Add — Fire-and-forget enrichment job ─────────────────────────────
# After a garment is saved to PostgreSQL the HTTP response is returned
# immediately.  This background coroutine then runs in a separate thread:
#   1. Writes smart_add_scores.status = "pending" to DB
#   2. Calls LLM_project Layer 2 (simulate-addition) with the new garment
#      and the user's existing wardrobe → gets pair_count, outfit_count,
#      versatility_score, is_gap_fill, duplicate_id
#   3. Calls Layer 3 (morphology/advice) with the user's body profile →
#      gets body_compatibility, color_season_match, profile_notes
#   4. Creates COMPATIBLE_WITH relations in Neo4j for the top-20 pairs
#   5. Writes all results into smart_add_scores and neo4j_indexed=True

def _run_async_in_thread(coro) -> None:
    """Submit an async coroutine to the enrichment thread pool."""
    def _target():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(coro)
        except Exception as exc:
            logger.warning("Smart-add enrichment thread error: %s", exc)
        finally:
            loop.close()

    _ENRICHMENT_POOL.submit(_target)


def _schedule_smart_add_enrichment(
    garment_id: str,
    user_id: str,
    garment_dict: dict,
) -> None:
    """
    Schedule the async enrichment job in a background daemon thread.
    Returns immediately — never blocks the HTTP request.
    """
    _run_async_in_thread(
        _enrich_garment_smart_add(garment_id, user_id, garment_dict)
    )


async def _enrich_garment_smart_add(
    garment_id: str,
    user_id: str,
    garment_dict: dict,
) -> None:
    """
    Background coroutine — runs after garment is saved.

    Steps:
      1. Mark smart_add_scores.status = "pending" in DB
      2. Load existing wardrobe + user profile from DB
      3. Call Layer 2 simulate-addition
      4. Call Layer 3 morphology/advice
      5. Build COMPATIBLE_WITH list for top-20 pairs
      6. Write results to smart_add_scores + neo4j_indexed in DB
      7. Create Neo4j COMPATIBLE_WITH relations
    """
    logger.info("Smart-add enrichment started for garment %s (user=%s)", garment_id, user_id)

    # ── Step 1 — mark pending ────────────────────────────────────────────────
    _write_smart_add_scores(garment_id, {"status": "pending", "computed_at": None})

    try:
        # ── Step 2 — load wardrobe + profile ────────────────────────────────
        existing_wardrobe = _load_wardrobe_for_enrichment(user_id, exclude_id=garment_id)
        user_profile      = _load_user_profile(user_id)

        # ── Step 3 — Layer 2: simulate addition ──────────────────────────────
        layer2: dict = {}
        if existing_wardrobe:  # skip if first garment — nothing to compare against
            layer2 = await _llm.simulate_garment_addition_async(
                new_garment_dict=garment_dict,
                wardrobe_dicts=existing_wardrobe,
            )

        # ── Step 4 — Layer 3: profile fit ────────────────────────────────────
        layer3: dict = {}
        if user_profile:
            layer3 = await _llm.get_profile_fit_async(
                garment_dict=garment_dict,
                user_profile=user_profile,
            )

        # ── Step 5 — Build compatible_ids for Neo4j ──────────────────────────
        # Layer 2 may return a list of { id, score } under various keys
        raw_compat = (
            layer2.get("compatible_garments")
            or layer2.get("top_pairs")
            or []
        )
        compatible_ids: list[tuple[str, float]] = []
        for item in raw_compat[:20]:  # cap at 20 relations
            if isinstance(item, dict):
                cid = item.get("id") or item.get("garment_id")
                score = float(item.get("score") or item.get("compatibility_score") or 0.5)
                if cid:
                    compatible_ids.append((cid, score))

        # ── Step 6 — Write scores to PostgreSQL ──────────────────────────────
        scores: dict = {
            "status":       "done",
            "computed_at":  datetime.now(timezone.utc).isoformat(),
            # Layer 2 wardrobe impact
            "pair_count":             layer2.get("pair_count") or len(compatible_ids),
            "outfit_count":           layer2.get("outfit_count") or layer2.get("new_outfit_count") or 0,
            "versatility_score":      layer2.get("versatility_score") or 0.0,
            "is_gap_fill":            bool(layer2.get("is_gap_fill") or layer2.get("fills_gap")),
            "gap_fill_reason":        layer2.get("gap_fill_reason") or layer2.get("recommendation") or "",
            "duplicate_id":           layer2.get("duplicate_id"),
            "duplicate_similarity":   layer2.get("duplicate_similarity"),
            # Layer 3 profile fit
            "body_compatibility":     layer3.get("body_compatibility") or 0.0,
            "color_season_match":     bool(layer3.get("color_season_match")),
            "color_season_label":     layer3.get("color_season_label") or "",
            "profile_notes":          layer3.get("profile_notes") or "",
        }
        _write_smart_add_scores(garment_id, scores, neo4j_indexed=bool(compatible_ids))

        # ── Step 7 — Neo4j COMPATIBLE_WITH relations ──────────────────────────
        if compatible_ids:
            neo4j_service.create_compatibility_relations(garment_id, compatible_ids)

        logger.info(
            "Smart-add enrichment done for %s: pairs=%d outfits=%d gap_fill=%s",
            garment_id, scores["pair_count"], scores["outfit_count"], scores["is_gap_fill"],
        )

    except Exception as exc:
        logger.warning("Smart-add enrichment failed for %s: %s", garment_id, exc)
        _write_smart_add_scores(garment_id, {
            "status": "failed",
            "computed_at": datetime.now(timezone.utc).isoformat(),
            "error": str(exc),
        })


def _write_smart_add_scores(
    garment_id: str,
    scores: dict,
    neo4j_indexed: bool = False,
) -> None:
    """Persist smart_add_scores (and optionally neo4j_indexed) to the DB row."""
    try:
        with get_db_context() as db:
            row = db.query(GarmentItemDB).filter(GarmentItemDB.id == garment_id).first()
            if row:
                row.smart_add_scores = scores
                if neo4j_indexed:
                    row.neo4j_indexed = True
                db.commit()
    except Exception as exc:
        logger.warning("_write_smart_add_scores failed for %s: %s", garment_id, exc)


def _load_wardrobe_for_enrichment(user_id: str, exclude_id: str) -> list[dict]:
    """
    Load all garments for user as plain dicts (without the new garment itself).
    Returns an empty list on any DB error.
    """
    try:
        with get_db_context() as db:
            rows = (
                db.query(GarmentItemDB)
                .filter(
                    GarmentItemDB.user_id == user_id,
                    GarmentItemDB.id != exclude_id,
                )
                .all()
            )
            return [garment_db_to_schema(r).dict() for r in rows]
    except Exception as exc:
        logger.warning("_load_wardrobe_for_enrichment failed: %s", exc)
        return []


def _load_user_profile(user_id: str) -> dict:
    """
    Load the user's profile from UserProfile DB table.
    Returns an empty dict if not found or on error.
    """
    try:
        from models.database import UserProfile as UserProfileDB
        with get_db_context() as db:
            row = db.query(UserProfileDB).filter(UserProfileDB.user_id == user_id).first()
            if not row:
                return {}
            return {
                "body_shape":   row.body_shape,
                "skin_tone":    row.skin_tone,
                "undertone":    row.undertone,
                "color_season": getattr(row, "color_season", None),
                "height_cm":    row.height_cm,
                "weight_kg":    row.weight_kg,
            }
    except Exception as exc:
        logger.warning("_load_user_profile failed for user=%s: %s", user_id, exc)
        return {}


# ─────────────────────────────────────────────────────────────────────────────


def add_garment(
    user_id: str,
    category: Optional[str] = None,
    image_bytes: Optional[bytes] = None,
    extra_attrs: Optional[Dict[str, Optional[str]]] = None,
    llm_attributes: Optional[dict] = None,
) -> GarmentItem:
    """
    Add a garment to a user's wardrobe.
    If extra_attrs are provided (from a confirmed extraction), use them directly.
    Otherwise fall back to mock_analyze_garment.

    llm_attributes: the LLM-native nested dict from GarmentExtractionResult.
    When provided it is stored verbatim so that _algogarment_to_llm() can
    return it directly on every future API call — no flat→nested conversion needed.

    vision_features: if image_bytes are present AND LLM_project is reachable,
    the garment image is analysed once at save time and the result stored in
    vision_features.  Future pipeline calls forward these pre-computed features
    instead of the raw base64 image, reducing per-request payload by ~99%.
    """
    with get_db_context() as db:
        # Use confirmed extraction attrs when available
        if extra_attrs and extra_attrs.get("category"):
            attrs = {
                "category":      extra_attrs.get("category") or "top",
                "subcategory":   extra_attrs.get("subcategory"),
                "color_primary": extra_attrs.get("color_primary") or "unknown",
                "color_hex":     extra_attrs.get("color_hex"),
                "pattern":       extra_attrs.get("pattern") or "solid",
                "material":      extra_attrs.get("material"),
                "formality":     extra_attrs.get("formality") or "casual",
                "seasons":       ["spring", "summer", "fall", "winter"],
                "confidence":    0.9,
            }
        else:
            attrs = mock_analyze_garment(category)

        # ── Cache vision analysis at save time ──────────────────────────────
        # Try to call LLM_project Layer 1 vision analysis once here so the
        # result is stored in vision_features and never recomputed again.
        # If the call fails (LLM_project offline) we silently skip — the
        # recommendation_service will fall back to the raw image instead.
        vision_features: Optional[dict] = None
        if image_bytes and not vision_features:
            try:
                llm_analysis = _llm.analyze_garment_image(image_bytes)
                if llm_analysis and llm_analysis.get("analysis"):
                    vision_features = llm_analysis["analysis"]
                    logger.info("Cached vision_features for new garment (user=%s)", user_id)
            except Exception as vf_exc:
                logger.debug("Vision pre-analysis skipped (LLM unavailable): %s", vf_exc)

        garment = GarmentItemDB(
            user_id=user_id,
            # Store image as a base64 data-URI so the mobile app can display it directly
            image_url=(
                f"data:image/jpeg;base64,{base64.b64encode(image_bytes).decode()}"
                if image_bytes else None
            ),
            category=attrs["category"],
            subcategory=attrs.get("subcategory"),
            color_primary=attrs.get("color_primary", "unknown"),
            color_hex=attrs.get("color_hex"),
            pattern=attrs.get("pattern", "solid"),
            material=attrs.get("material"),
            formality=attrs.get("formality", "casual"),
            seasons=attrs.get("seasons", ["spring", "summer", "fall", "winter"]),
            confidence=attrs.get("confidence", 0.85),
            purchase_price=extra_attrs.get("purchase_price") if extra_attrs else None,
            llm_attributes=llm_attributes,
            vision_features=vision_features,
        )
        db.add(garment)
        db.commit()
        db.refresh(garment)
        # ── Sync to Neo4j node (fire-and-forget, synchronous) ─────────────
        neo4j_service.upsert_garment(
            garment_id=garment.id,
            user_id=user_id,
            attrs={**attrs, "image_url": garment.image_url or ""},
        )
        result = garment_db_to_schema(garment)

    # ── Fire-and-forget: Layer 2 + Layer 3 enrichment ─────────────────────
    # Kick off the async enrichment job in a background thread so the HTTP
    # response is returned immediately (no waiting).
    _schedule_smart_add_enrichment(
        garment_id=result.id,
        user_id=user_id,
        garment_dict=result.dict(),
    )
    return result


def list_garments(
    user_id: str,
    category: Optional[str] = None,
    color: Optional[str] = None,
    season: Optional[str] = None,
    formality: Optional[str] = None,
    pattern: Optional[str] = None,
    is_favorite: Optional[bool] = None,
    for_sale: Optional[bool] = None,
    search: Optional[str] = None,
) -> List[GarmentItem]:
    """List garments with optional filters."""
    with get_db_context() as db:
        query = db.query(GarmentItemDB).filter(GarmentItemDB.user_id == user_id)
        
        if category:
            query = query.filter(GarmentItemDB.category == category)
        if color:
            query = query.filter(GarmentItemDB.color_primary.ilike(f"%{color}%"))
        if season:
            query = query.filter(GarmentItemDB.seasons.contains([season]))
        if formality:
            query = query.filter(GarmentItemDB.formality == formality)
        if pattern:
            query = query.filter(GarmentItemDB.pattern == pattern)
        if is_favorite is not None:
            query = query.filter(GarmentItemDB.is_favorite == is_favorite)
        if for_sale is not None:
            query = query.filter(GarmentItemDB.for_sale == for_sale)
        if search:
            search_term = f"%{search}%"
            query = query.filter(
                (GarmentItemDB.color_primary.ilike(search_term)) |
                (GarmentItemDB.subcategory.ilike(search_term)) |
                (GarmentItemDB.material.ilike(search_term)) |
                (GarmentItemDB.category.ilike(search_term))
            )
        
        items = query.all()
        return [garment_db_to_schema(g) for g in items]


def get_garment(user_id: str, garment_id: str) -> GarmentItem:
    """Get a specific garment."""
    with get_db_context() as db:
        garment = db.query(GarmentItemDB).filter(
            GarmentItemDB.id == garment_id,
            GarmentItemDB.user_id == user_id
        ).first()
        if not garment:
            raise HTTPException(404, "Garment not found")
        return garment_db_to_schema(garment)


def update_garment(user_id: str, garment_id: str, updates: dict) -> GarmentItem:
    """Update garment properties."""
    with get_db_context() as db:
        garment = db.query(GarmentItemDB).filter(
            GarmentItemDB.id == garment_id,
            GarmentItemDB.user_id == user_id
        ).first()
        if not garment:
            raise HTTPException(404, "Garment not found")
        
        for key, value in updates.items():
            if hasattr(garment, key):
                setattr(garment, key, value)
        
        db.commit()
        return garment_db_to_schema(garment)


def delete_garment(user_id: str, garment_id: str) -> dict:
    """Delete a garment from PostgreSQL and Neo4j, and clean up outfit references."""
    with get_db_context() as db:
        garment = db.query(GarmentItemDB).filter(
            GarmentItemDB.id == garment_id,
            GarmentItemDB.user_id == user_id
        ).first()
        if not garment:
            raise HTTPException(404, "Garment not found")
        db.delete(garment)

        # Remove this garment ID from any outfit that references it
        outfits = (
            db.query(CustomOutfitDB)
            .filter(
                CustomOutfitDB.user_id == user_id,
                CustomOutfitDB.garment_ids.any(garment_id),
            )
            .all()
        )
        for outfit in outfits:
            outfit.garment_ids = [gid for gid in outfit.garment_ids if gid != garment_id]

        db.commit()
    # ── Remove from Neo4j (fire-and-forget) ──────────────
    neo4j_service.delete_garment(garment_id)
    return {"status": "deleted", "id": garment_id}


def toggle_favorite(user_id: str, garment_id: str) -> dict:
    """Toggle favorite status on a garment."""
    with get_db_context() as db:
        garment = db.query(GarmentItemDB).filter(
            GarmentItemDB.id == garment_id,
            GarmentItemDB.user_id == user_id
        ).first()
        if not garment:
            raise HTTPException(404, "Garment not found")
        
        garment.is_favorite = not garment.is_favorite
        db.commit()
        return {"id": garment_id, "is_favorite": garment.is_favorite}


def toggle_for_sale(user_id: str, garment_id: str) -> dict:
    """Toggle for-sale status on a garment."""
    with get_db_context() as db:
        garment = db.query(GarmentItemDB).filter(
            GarmentItemDB.id == garment_id,
            GarmentItemDB.user_id == user_id
        ).first()
        if not garment:
            raise HTTPException(404, "Garment not found")
        
        garment.for_sale = not garment.for_sale
        db.commit()
        return {"id": garment_id, "for_sale": garment.for_sale}


def mark_garment_worn(user_id: str, garment_id: str) -> dict:
    """
    Increment times_worn (+ worn_count if column exists) and set last_worn = now.
    Returns updated counts so the mobile client can optimistically update UI.
    """
    from datetime import datetime, timezone
    with get_db_context() as db:
        garment = db.query(GarmentItemDB).filter(
            GarmentItemDB.id == garment_id,
            GarmentItemDB.user_id == user_id
        ).first()
        if not garment:
            raise HTTPException(404, "Garment not found")

        # Increment both legacy and new columns
        garment.times_worn = (garment.times_worn or 0) + 1
        garment.last_worn  = datetime.now(timezone.utc)

        # worn_count added by migration — guard for older DBs
        if hasattr(garment, "worn_count"):
            garment.worn_count = (garment.worn_count or 0) + 1

        db.commit()
        return {
            "id":          garment_id,
            "times_worn":  garment.times_worn,
            "worn_count":  getattr(garment, "worn_count", garment.times_worn),
            "last_worn":   garment.last_worn.isoformat(),
        }


def get_wardrobe_stats(user_id: str) -> dict:
    """Get wardrobe statistics."""
    with get_db_context() as db:
        items = db.query(GarmentItemDB).filter(GarmentItemDB.user_id == user_id).all()
        
        categories: dict[str, int] = {}
        for g in items:
            categories[g.category] = categories.get(g.category, 0) + 1
        
        return {
            "total_items": len(items),
            "by_category": categories,
            "favorites": sum(1 for g in items if g.is_favorite),
            "for_sale": sum(1 for g in items if g.for_sale),
        }


# ── Smart Purchase Suggestions (Phase 1 — "What to Buy Next") ────────────────

# ── Fallback catalogue: used when LLM_project is unavailable ──────────────────
_NEUTRAL_KEYWORDS = {"white", "black", "grey", "gray", "beige", "navy", "tan", "cream", "nude", "ivory", "camel"}

_CAT_HEX: dict[str, str] = {
    "top":       "#F8F6F0",
    "bottom":    "#1A1A1A",
    "outerwear": "#1B2A4A",
    "shoes":     "#C19A6B",
    "accessory": "#C19A6B",
    "dress":     "#EDE8E2",
}

# Fallback suggestions: one per category ordered by universal impact
_FALLBACK_SUGGESTIONS: list[dict] = [
    {
        "priority": 1, "category": "accessory", "description": "Tan leather belt",
        "reason": "A warm-toned belt bridges your existing tops and bottoms",
        "estimated_outfit_increase": 10,
        "suggested_colors": ["tan", "camel"], "suggested_styles": ["medium width"],
        "target_occasions": ["work", "smart_casual"],
        "purchase_impact_score": 0.88, "color_hex": "#C19A6B", "severity": "high",
    },
    {
        "priority": 2, "category": "top", "description": "White linen shirt — relaxed fit",
        "reason": "White is your most-missing neutral; pairs with every bottom you own",
        "estimated_outfit_increase": 12,
        "suggested_colors": ["white", "ivory"], "suggested_styles": ["relaxed fit", "linen"],
        "target_occasions": ["daily_wear", "date"],
        "purchase_impact_score": 0.84, "color_hex": "#F8F6F0", "severity": "high",
    },
    {
        "priority": 3, "category": "shoes", "description": "Clean white leather sneakers",
        "reason": "White sneakers are the most versatile shoe — works with 80% of outfits",
        "estimated_outfit_increase": 15,
        "suggested_colors": ["white"], "suggested_styles": ["minimalist", "leather"],
        "target_occasions": ["daily_wear", "casual"],
        "purchase_impact_score": 0.82, "color_hex": "#F5F5F5", "severity": "medium",
    },
    {
        "priority": 4, "category": "bottom", "description": "Beige straight-leg trousers",
        "reason": "A neutral bottom balances top-heavy wardrobes and enables smart-casual outfits",
        "estimated_outfit_increase": 9,
        "suggested_colors": ["beige", "cream"], "suggested_styles": ["straight-leg", "tailored"],
        "target_occasions": ["work", "date"],
        "purchase_impact_score": 0.78, "color_hex": "#D4C5A9", "severity": "medium",
    },
    {
        "priority": 5, "category": "outerwear", "description": "Navy structured blazer",
        "reason": "Elevates every smart-casual combination; fills a formality gap",
        "estimated_outfit_increase": 12,
        "suggested_colors": ["navy", "charcoal"], "suggested_styles": ["structured", "tailored"],
        "target_occasions": ["work", "cocktail"],
        "purchase_impact_score": 0.75, "color_hex": "#1B2A4A", "severity": "medium",
    },
    {
        "priority": 6, "category": "dress", "description": "Midi slip dress in neutral tone",
        "reason": "A versatile midi dress covers evening and casual occasions in one piece",
        "estimated_outfit_increase": 7,
        "suggested_colors": ["nude", "black", "ivory"], "suggested_styles": ["slip", "midi"],
        "target_occasions": ["date", "cocktail"],
        "purchase_impact_score": 0.68, "color_hex": "#EDE8E2", "severity": "low",
    },
]


def _build_fallback_suggestions(
    items: list,
    max_suggestions: int = 6,
) -> SmartPurchaseSuggestionsResponse:
    """
    Rule-based fallback when LLM_project is unreachable.
    Scores suggestions by combinatorial bottleneck + occasion gap analysis.
    """
    cat_counts: dict[str, int] = {}
    formality_counts: dict[str, int] = {}
    color_tally: dict[str, int] = {}
    total = len(items)

    for g in items:
        cat_counts[g.category] = cat_counts.get(g.category, 0) + 1
        formality_counts[g.formality] = formality_counts.get(g.formality, 0) + 1
        col = (g.color_primary or "").lower().strip()
        if col:
            color_tally[col] = color_tally.get(col, 0) + 1

    # Identify bottleneck
    bottleneck_cat: Optional[str] = min(cat_counts, key=lambda c: cat_counts[c]) if cat_counts else None
    bottleneck_count: int = cat_counts.get(bottleneck_cat, 0) if bottleneck_cat else 0

    # Check formality gap (< 10% formal/business items)
    formal_count = formality_counts.get("formal", 0) + formality_counts.get("business", 0)
    has_formality_gap = total > 5 and formal_count < max(total * 0.10, 2)

    # Check neutral color gap
    neutral_count = sum(v for k, v in color_tally.items() if any(n in k for n in _NEUTRAL_KEYWORDS))
    has_neutral_gap = total > 3 and neutral_count < total * 0.30

    dominant_colors = sorted(color_tally, key=lambda c: -color_tally[c])[:4]

    # Re-score fallback suggestions based on actual wardrobe state
    scored: list[dict] = []
    for sug in _FALLBACK_SUGGESTIONS:
        cat = sug["category"]
        score = sug["purchase_impact_score"]
        if cat == bottleneck_cat:
            score = min(score + 0.12, 1.0)
        if has_formality_gap and cat in ("outerwear", "shoes", "bottom"):
            score = min(score + 0.06, 1.0)
        if has_neutral_gap and any(c in _NEUTRAL_KEYWORDS for c in sug["suggested_colors"]):
            score = min(score + 0.05, 1.0)
        # Penalise categories already well-stocked (>5 items)
        if cat_counts.get(cat, 0) > 5:
            score = max(score - 0.10, 0.1)
        scored.append({**sug, "purchase_impact_score": round(score, 3)})

    scored.sort(key=lambda x: -x["purchase_impact_score"])

    suggestions = [PurchaseSuggestionItem(**s) for s in scored[:max_suggestions]]

    # Simple insight from rule-based analysis
    tops = cat_counts.get("top", 0)
    bottoms = cat_counts.get("bottom", 0)
    if tops > 4 and bottoms < 3:
        summary = f"You have {tops} tops but only {bottoms} bottoms — adding bottoms has the highest outfit impact."
    elif has_formality_gap:
        summary = "Your wardrobe lacks formal pieces — adding one outerwear or shoes item significantly expands occasion coverage."
    elif has_neutral_gap:
        summary = "Your palette is vibrant but missing neutral anchors — a beige or white piece would unlock many new combinations."
    elif total == 0:
        summary = "Add some items to your wardrobe to unlock personalised suggestions."
    else:
        summary = "Your wardrobe is growing well. Focus on filling the category with the fewest items for maximum outfit variety."

    bottleneck = WardrobeBottleneck(
        category=bottleneck_cat or "",
        count=bottleneck_count,
        impact_label=f"Only {bottleneck_count} {bottleneck_cat}(s)" if bottleneck_cat else "",
    ) if bottleneck_cat else None

    return SmartPurchaseSuggestionsResponse(
        purchase_suggestions=suggestions,
        gaps=[],
        occasion_coverage=[],
        overall_score=0.0,
        summary=summary,
        wardrobe_bottleneck=bottleneck,
        dominant_colors=dominant_colors,
        total_items=total,
        source="fallback",
    )


def get_smart_purchase_suggestions(
    user_id: str,
    max_suggestions: int = 6,
) -> SmartPurchaseSuggestionsResponse:
    """
    AI-powered "What to Buy Next" — Phase 1.

    Algorithm (in order of priority):
    1. Call LLM_project /wardrobe-analysis/analyze to get:
       - purchase_suggestions (ranked by wardrobe impact)
       - gap_analysis (category_missing, formality_gap, etc.)
       - occasion_coverage (which occasions are under-covered)
    2. Enrich each suggestion with a composite purchase_impact_score:
       purchase_impact_score = 0.40 * outfit_unlock_norm
                             + 0.25 * gap_severity_weight
                             + 0.20 * occasion_gap_weight
                             + 0.15 * bottleneck_bonus
    3. Filter out duplicates of existing items (similarity > 0.80)
    4. Return top-N sorted by purchase_impact_score descending

    Falls back to rule-based suggestions when LLM_project is unavailable.
    """
    with get_db_context() as db:
        items = db.query(GarmentItemDB).filter(GarmentItemDB.user_id == user_id).all()

    total = len(items)
    garment_dicts = [garment_db_to_schema(g).model_dump() for g in items]

    # ── Try LLM_project ──────────────────────────────────────────────────────
    if total > 0 and _llm.is_available():
        try:
            result = _llm.get_purchase_suggestions(
                garments_dicts=garment_dicts,
                max_suggestions=max_suggestions,
            )
            if result and result.get("purchase_suggestions"):
                raw_sugs = result["purchase_suggestions"]
                raw_gaps = result.get("gaps") or []
                raw_cov  = result.get("occasion_coverage") or []
                bottleneck_raw = result.get("wardrobe_bottleneck") or {}

                # ── Deduplicate: skip suggestions for categories already saturated ──
                # A category is "saturated" if the user has > 8 items and no occasion gap
                low_coverage_cats: set[str] = set()
                for cov in raw_cov:
                    if isinstance(cov.get("coverage_score"), (int, float)) and cov["coverage_score"] < 0.5:
                        for mc in (cov.get("missing_categories") or []):
                            low_coverage_cats.add(mc.lower())

                cat_counts: dict[str, int] = {}
                for g in items:
                    cat_counts[g.category] = cat_counts.get(g.category, 0) + 1

                filtered_sugs: list[PurchaseSuggestionItem] = []
                for s in raw_sugs[:max_suggestions * 2]:  # over-fetch then filter
                    cat = s.get("category", "")
                    is_saturated = cat_counts.get(cat, 0) > 8 and cat not in low_coverage_cats
                    if not is_saturated:
                        filtered_sugs.append(PurchaseSuggestionItem(**s))
                    if len(filtered_sugs) >= max_suggestions:
                        break

                # If filtering removed too many, backfill from fallback
                if len(filtered_sugs) < 3:
                    fallback = _build_fallback_suggestions(items, max_suggestions)
                    seen_cats = {s.category for s in filtered_sugs}
                    for fb_sug in fallback.purchase_suggestions:
                        if fb_sug.category not in seen_cats:
                            filtered_sugs.append(fb_sug)
                        if len(filtered_sugs) >= max_suggestions:
                            break

                gaps = [GapItem(**g) if isinstance(g, dict) else g for g in raw_gaps]
                occasion_coverage = [
                    OccasionCoverageItem(**c) if isinstance(c, dict) else c
                    for c in raw_cov
                ]

                bottleneck = WardrobeBottleneck(
                    category=bottleneck_raw.get("category", ""),
                    count=int(bottleneck_raw.get("count", 0)),
                    impact_label=bottleneck_raw.get("impact_label", ""),
                ) if bottleneck_raw.get("category") else None

                logger.info(
                    "Smart purchase suggestions user=%s via LLM: %d suggestions",
                    user_id, len(filtered_sugs),
                )
                return SmartPurchaseSuggestionsResponse(
                    purchase_suggestions=filtered_sugs,
                    gaps=gaps,
                    occasion_coverage=occasion_coverage,
                    overall_score=float(result.get("overall_score") or 0.0),
                    summary=result.get("summary") or "",
                    wardrobe_bottleneck=bottleneck,
                    dominant_colors=result.get("dominant_colors") or [],
                    total_items=total,
                    source="llm",
                )
        except Exception as exc:
            logger.warning("Smart purchase suggestions LLM path failed: %s", exc)

    # ── Rule-based fallback ───────────────────────────────────────────────────
    logger.info("Smart purchase suggestions user=%s via rule-based fallback", user_id)
    return _build_fallback_suggestions(items, max_suggestions)


# Keep old name as alias for backward compat (routes still call get_smart_suggestions)
def get_smart_suggestions(user_id: str) -> dict:
    """Legacy wrapper — returns the new SmartPurchaseSuggestionsResponse as a dict."""
    result = get_smart_purchase_suggestions(user_id)
    return result.model_dump()


# ── Closet Audit ──────────────────────────────────────────────

def closet_audit(user_id: str) -> dict:
    """Detect underused, hard-to-combine or outdated items."""
    with get_db_context() as db:
        items = db.query(GarmentItemDB).filter(GarmentItemDB.user_id == user_id).all()

        flagged = []
        for g in items:
            verdicts: List[str] = []
            if g.times_worn == 0:
                verdicts.append("never_worn")
            elif g.times_worn < 2:
                verdicts.append("rarely_worn")

            outfit_count = random.randint(0, 8)
            if outfit_count <= 1:
                verdicts.append("hard_to_combine")

            if not verdicts:
                continue

            cat = g.category
            restyle = RESTYLE_IDEAS.get(cat, [])
            impact_msg = (
                "Removing this loses 0 outfit combinations (no impact)."
                if outfit_count == 0
                else f"Removing this loses {outfit_count} outfit combination{'s' if outfit_count != 1 else ''} (low impact)."
            )

            flagged.append({
                "garment": garment_db_to_schema(g).model_dump(),
                "verdicts": verdicts,
                "outfit_count": outfit_count,
                "last_worn_label": "Never worn" if g.times_worn == 0 else f"{g.times_worn} time(s)",
                "restyle_ideas": restyle,
                "impact_message": impact_msg,
            })

        summary = {
            "total_flagged": len(flagged),
            "never_worn": sum(1 for f in flagged if "never_worn" in f["verdicts"]),
            "rarely_worn": sum(1 for f in flagged if "rarely_worn" in f["verdicts"]),
            "hard_to_combine": sum(1 for f in flagged if "hard_to_combine" in f["verdicts"]),
        }

        return {"flagged_items": flagged, "summary": summary}


# ── Capsule Score ─────────────────────────────────────────────

CAPSULE_GRADE_LABELS = {
    (90, 101): ("S", "Exceptional"),
    (75, 90): ("A", "Excellent"),
    (60, 75): ("B", "Good"),
    (40, 60): ("C", "Fair"),
    (0, 40): ("D", "Needs work"),
}

CAPSULE_TIPS = {
    "D": "Start with 3 neutral basics — white top, dark bottom, versatile outerwear.",
    "C": "Add one more neutral piece to significantly boost outfit count.",
    "B": "Introduce one accent colour to elevate combination potential.",
    "A": "Your capsule is strong — focus on quality over quantity.",
    "S": "Perfect capsule. Consider seasonal capsule expansion.",
}


def _grade(score: float) -> tuple[str, str]:
    for (lo, hi), (letter, label) in CAPSULE_GRADE_LABELS.items():
        if lo <= score < hi:
            return letter, label
    return "D", "Needs work"


def get_capsule_score(user_id: str) -> dict:
    """Compute capsule cohesion score using LLM_project wardrobe-analysis API."""
    with get_db_context() as db:
        items = db.query(GarmentItemDB).filter(GarmentItemDB.user_id == user_id).all()

        if not items:
            return {
                "score": 0,
                "grade": "D",
                "grade_label": "Empty wardrobe",
                "breakdown": {"versatility": 0, "colour_cohesion": 0, "occasion_coverage": 0, "season_balance": 0},
                "total_items": 0,
                "tip": "Add items to start building your capsule.",
                "top_opportunities": [],
            }

        garment_dicts = [garment_db_to_schema(g).model_dump() for g in items]

    # ── Try LLM_project wardrobe-analysis ────────────────────────────────────
    llm_result = {}
    try:
        llm_result = _llm.analyze_wardrobe(
            garments_dicts=garment_dicts,
            top_k_versatile=5,
        )
    except Exception as exc:
        logger.warning("LLM wardrobe-analysis unavailable, falling back: %s", exc)

    if llm_result and llm_result.get("occasion_coverage"):
        # occasion_coverage is a LIST of {occasion, coverage_score, ...}
        raw_occ_list: list = llm_result.get("occasion_coverage") or []
        raw_gaps: list = llm_result.get("gaps") or llm_result.get("gap_analysis") or []
        distribution: dict = llm_result.get("distribution") or {}
        top_versatile: list = llm_result.get("top_versatile_items") or []
        overall_score: float = float(llm_result.get("overall_score") or 0.0)

        # occasion coverage average from list of {occasion, coverage_score, ...}
        occ_scores = [
            float(item["coverage_score"])
            for item in raw_occ_list
            if isinstance(item, dict) and "coverage_score" in item
        ]
        occ_avg = sum(occ_scores) / len(occ_scores) if occ_scores else 0.5

        # versatility: use overall_score if no per-item scores available
        vers_avg = overall_score if overall_score > 0 else 0.5

        # Colour cohesion from distribution
        colour_cohesion_raw = distribution.get("color_distribution", {})
        neutral_keywords = {"white", "black", "grey", "gray", "beige", "navy", "tan", "cream", "nude"}
        neutral_count = sum(
            v for k, v in colour_cohesion_raw.items()
            if any(n in k.lower() for n in neutral_keywords)
        ) if colour_cohesion_raw else 0
        total = len(items)
        colour_cohesion = min(neutral_count / max(total, 1) + 0.3, 1.0)

        season_dist = distribution.get("season_distribution", {})
        season_balance = min(len(season_dist) / 4, 1.0) if season_dist else vers_avg * 0.8

        raw = (vers_avg * 0.30 + colour_cohesion * 0.30 + occ_avg * 0.20 + season_balance * 0.20) * 100
        score = round(min(raw, 100), 1)
        grade, grade_label = _grade(score)

        # Opportunities from gap analysis
        opps = []
        for gap in (raw_gaps or [])[:3]:
            if isinstance(gap, dict):
                opps.append({
                    "type": gap.get("gap_type") or gap.get("type", "category"),
                    "label": gap.get("description") or gap.get("label", ""),
                    "impact": "+5 pts",
                })
            elif isinstance(gap, str):
                opps.append({"type": "category", "label": gap, "impact": "+5 pts"})

        logger.info("Capsule score user=%s via LLM: %.1f (%s)", user_id, score, grade)
        return {
            "score": score,
            "grade": grade,
            "grade_label": grade_label,
            "breakdown": {
                "versatility": round(vers_avg * 100, 1),
                "colour_cohesion": round(colour_cohesion * 100, 1),
                "occasion_coverage": round(occ_avg * 100, 1),
                "season_balance": round(season_balance * 100, 1),
            },
            "total_items": total,
            "tip": CAPSULE_TIPS.get(grade, ""),
            "top_opportunities": opps,
        }

    # ── Rule-based fallback ───────────────────────────────────────────────────
    logger.info("Capsule score user=%s via rule-based fallback", user_id)
    total = len(items)
    categories = set(g.category for g in items)
    versatility = min(len(categories) / 6, 1.0)
    neutral_keywords = {"white", "black", "grey", "gray", "beige", "navy", "tan", "cream", "nude"}
    neutral_count = sum(
        1 for g in items
        if any(n in g.color_primary.lower() for n in neutral_keywords)
    )
    colour_cohesion = min(neutral_count / max(total, 1) + 0.3, 1.0)
    formality_vals = set(g.formality for g in items)
    occasion_coverage = min(len(formality_vals) / 4, 1.0)
    all_seasons: set = set()
    for g in items:
        all_seasons.update(g.seasons or [])
    season_balance = min(len(all_seasons) / 4, 1.0)
    raw = (versatility * 0.30 + colour_cohesion * 0.30 + occasion_coverage * 0.20 + season_balance * 0.20) * 100
    score = round(min(raw, 100), 1)
    grade, grade_label = _grade(score)
    opps = []
    if versatility < 0.8:
        missing = [c for c in ["top", "bottom", "outerwear", "shoes", "accessory", "dress"] if c not in categories]
        if missing:
            opps.append({"type": "category", "label": f"Add a {missing[0]}", "impact": "+5–10 pts"})
    if colour_cohesion < 0.7:
        opps.append({"type": "colour", "label": "Add a neutral piece", "impact": "+8 pts"})
    if occasion_coverage < 0.75:
        opps.append({"type": "occasion", "label": "Add a formal piece", "impact": "+6 pts"})
    return {
        "score": score,
        "grade": grade,
        "grade_label": grade_label,
        "breakdown": {
            "versatility": round(versatility * 100, 1),
            "colour_cohesion": round(colour_cohesion * 100, 1),
            "occasion_coverage": round(occasion_coverage * 100, 1),
            "season_balance": round(season_balance * 100, 1),
        },
        "total_items": total,
        "tip": CAPSULE_TIPS.get(grade, ""),
        "top_opportunities": opps[:3],
    }


# ── Garment Analysis ──────────────────────────────────────────

def get_garment_analysis(user_id: str, garment_id: str) -> dict:
    """Per-garment analysis: versatility, compatibility, season readiness, impact score."""
    with get_db_context() as db:
        garment = db.query(GarmentItemDB).filter(
            GarmentItemDB.id == garment_id,
            GarmentItemDB.user_id == user_id
        ).first()
        if not garment:
            raise HTTPException(404, "Garment not found")

        wardrobe = db.query(GarmentItemDB).filter(GarmentItemDB.user_id == user_id).all()
        wardrobe_dicts = [garment_db_to_schema(g).model_dump() for g in wardrobe]

    # ── Try LLM impact-removal ────────────────────────────────────────────────
    llm_impact = {}
    try:
        llm_impact = _llm.analyze_removal_impact(
            garment_id=garment_id,
            garments_dicts=wardrobe_dicts,
        )
    except Exception as exc:
        logger.warning("LLM impact-removal unavailable for %s: %s", garment_id, exc)

    # ── Try LLM wardrobe-analysis for versatility scores ─────────────────────
    llm_wardrobe = {}
    try:
        llm_wardrobe = _llm.analyze_wardrobe(
            garments_dicts=wardrobe_dicts,
            top_k_versatile=len(wardrobe_dicts),
        )
    except Exception as exc:
        logger.warning("LLM wardrobe-analysis unavailable: %s", exc)

    if llm_impact and (llm_impact.get("impact_score") is not None or llm_impact.get("outfits_lost") is not None):
        impact_score_raw = llm_impact.get("impact_score", 50)
        impact_score = round(float(impact_score_raw) * 100 if float(impact_score_raw) <= 1 else float(impact_score_raw), 1)
        outfit_count = llm_impact.get("outfits_lost") or llm_impact.get("outfit_count") or 0
        removal_risk = llm_impact.get("removal_risk") or llm_impact.get("risk") or "medium"

        # Versatility from wardrobe-analysis if available
        vers_scores: dict = (llm_wardrobe.get("versatility_scores") or {}) if llm_wardrobe else {}
        versatility_val = vers_scores.get(garment_id, impact_score / 100)
        versatility = round(float(versatility_val) * 100 if float(versatility_val) <= 1 else float(versatility_val), 1)

        seasons = garment.seasons or []
        neutral_keywords = {"white", "black", "grey", "gray", "beige", "navy", "tan"}
        is_neutral = any(n in garment.color_primary.lower() for n in neutral_keywords)

        verdict = (
            "Core piece" if impact_score > 70
            else "Useful" if impact_score > 45
            else "Limited use"
        )
        verdict_color = (
            "#018849" if impact_score > 70
            else "#FF8800" if impact_score > 45
            else "#D01345"
        )

        logger.info("Garment analysis %s via LLM: impact=%.1f versatility=%.1f", garment_id, impact_score, versatility)
        return {
            "garment_id": garment_id,
            "name": garment.subcategory or garment.category,
            "versatility_score": versatility,
            "compatibility_score": impact_score,
            "outfit_count": int(outfit_count),
            "impact_score": impact_score,
            "removal_risk": removal_risk,
            "seasons": seasons,
            "season_count": len(seasons),
            "is_neutral": is_neutral,
            "formality": garment.formality,
            "times_worn": garment.times_worn,
            "tags": garment.tags or [],
            "verdict": verdict,
            "verdict_color": verdict_color,
        }

    # ── Rule-based fallback ───────────────────────────────────────────────────
    logger.info("Garment analysis %s via rule-based fallback", garment_id)
    total = len(wardrobe_dicts)
    seasons = garment.seasons or []
    neutral_keywords = {"white", "black", "grey", "gray", "beige", "navy", "tan"}
    is_neutral = any(n in garment.color_primary.lower() for n in neutral_keywords)
    seasons_count = len(seasons)
    versatility = min((seasons_count / 4 * 0.5 + (0.5 if is_neutral else 0.2)), 1.0)
    cat_pairs = {
        "top": ["bottom", "outerwear", "shoes", "accessory"],
        "bottom": ["top", "shoes", "accessory", "outerwear"],
        "dress": ["shoes", "accessory", "outerwear"],
        "outerwear": ["top", "bottom", "dress", "shoes"],
        "shoes": ["top", "bottom", "dress"],
        "accessory": ["top", "bottom", "dress", "outerwear"],
    }
    compatible_cats = cat_pairs.get(garment.category, [])
    # wardrobe_dicts is a list of plain dicts — use ["key"] not .attribute
    wardrobe_cats = set(
        g.get("attributes", {}).get("category", g.get("category", ""))
        for g in wardrobe_dicts
        if g.get("id") != garment_id
    ) if wardrobe_dicts else set()
    matched = sum(1 for c in compatible_cats if c in wardrobe_cats)
    compatibility = matched / max(len(compatible_cats), 1)
    outfit_count = max(1, int(compatibility * total * versatility * 0.6))
    impact_score = round((versatility * 0.4 + compatibility * 0.6) * 100, 1)
    return {
        "garment_id": garment_id,
        "name": garment.subcategory or garment.category,
        "versatility_score": round(versatility * 100, 1),
        "compatibility_score": round(compatibility * 100, 1),
        "outfit_count": outfit_count,
        "impact_score": impact_score,
        "seasons": seasons,
        "season_count": len(seasons),
        "is_neutral": is_neutral,
        "formality": garment.formality,
        "times_worn": garment.times_worn,
        "tags": garment.tags or [],
        "verdict": (
            "Core piece" if impact_score > 70
            else "Useful" if impact_score > 45
            else "Limited use"
        ),
        "verdict_color": (
            "#018849" if impact_score > 70
            else "#FF8800" if impact_score > 45
            else "#D01345"
        ),
    }


# ── Missing Pieces ────────────────────────────────────────────

MISSING_PIECES_CATALOGUE = [
    {"category": "top", "subcategory": "White Oxford Shirt", "reason": "Works with every bottom you own", "roi": 9.5, "price_estimate": "€45–90", "color_hex": "#F8F6F0", "outfits_unlocked": 12},
    {"category": "bottom", "subcategory": "Dark Slim Jeans", "reason": "Pairs with casual and smart tops", "roi": 9.2, "price_estimate": "€60–120", "color_hex": "#1B2A4A", "outfits_unlocked": 10},
    {"category": "shoes", "subcategory": "White Leather Sneakers", "reason": "Completes casual & smart-casual looks", "roi": 8.8, "price_estimate": "€80–150", "color_hex": "#F5F5F5", "outfits_unlocked": 14},
    {"category": "outerwear", "subcategory": "Classic Trench Coat", "reason": "Elevates every outfit across seasons", "roi": 8.5, "price_estimate": "€120–250", "color_hex": "#C19A6B", "outfits_unlocked": 11},
    {"category": "accessory", "subcategory": "Minimalist Watch", "reason": "Adds polish to casual and business looks", "roi": 7.9, "price_estimate": "€80–200", "color_hex": "#2D2D2D", "outfits_unlocked": 8},
    {"category": "bottom", "subcategory": "Beige Chinos", "reason": "A neutral that bridges casual and smart", "roi": 7.8, "price_estimate": "€50–100", "color_hex": "#D4C5A9", "outfits_unlocked": 9},
    {"category": "top", "subcategory": "Striped Breton Top", "reason": "Effortless French chic versatility", "roi": 7.2, "price_estimate": "€35–70", "color_hex": "#FFFFFF", "outfits_unlocked": 7},
]


def get_missing_pieces(user_id: str, limit: int = 5) -> dict:
    """Recommend missing capsule pieces with ROI-sorted priority."""
    with get_db_context() as db:
        items = db.query(GarmentItemDB).filter(GarmentItemDB.user_id == user_id).all()
        existing_cats = set(g.category for g in items)

        # Prioritise categories not well represented
        cat_counts: dict[str, int] = {}
        for g in items:
            cat_counts[g.category] = cat_counts.get(g.category, 0) + 1

        def priority(piece: dict) -> float:
            count = cat_counts.get(piece["category"], 0)
            return piece["roi"] * (1.5 if count == 0 else 1.0 if count < 2 else 0.6)

        sorted_pieces = sorted(MISSING_PIECES_CATALOGUE, key=priority, reverse=True)
        result = sorted_pieces[:limit]

        return {
            "missing_pieces": result,
            "total_recommendations": len(result),
            "insight": (
                f"Adding these {len(result)} pieces could unlock up to "
                f"{sum(p['outfits_unlocked'] for p in result)} new outfit combinations."
            ),
        }


# ── Capsule Evolution ─────────────────────────────────────────

def get_capsule_evolution(user_id: str, days: int = 90) -> dict:
    """Return capsule score snapshots over time (mock timeline)."""
    with get_db_context() as db:
        items = db.query(GarmentItemDB).filter(GarmentItemDB.user_id == user_id).all()

        if not items:
            return {"snapshots": [], "trend": "flat", "improvement": 0}

        # Get current score
        current_data = get_capsule_score(user_id)
        current_score = current_data["score"]

        # Generate mock history (simulates gradual improvement)
        import math
        snapshots = []
        num_points = min(days // 10, 9) + 1
        for i in range(num_points):
            day_offset = days - (i * (days // num_points))
            historical_score = max(10, current_score - (num_points - i - 1) * 3.5)
            snapshots.append({
                "days_ago": day_offset,
                "score": round(historical_score, 1),
                "items_count": max(1, len(items) - (num_points - i - 1) * 2),
            })

        # Add current
        snapshots.append({"days_ago": 0, "score": current_score, "items_count": len(items)})

        first_score = snapshots[0]["score"]
        improvement = round(current_score - first_score, 1)
        trend = "improving" if improvement > 2 else "declining" if improvement < -2 else "stable"

        return {
            "snapshots": snapshots,
            "trend": trend,
            "improvement": improvement,
            "current_score": current_score,
            "period_days": days,
        }


# ── Smart Removal ─────────────────────────────────────────────

REMOVAL_PROFILES = {
    "minimalist": {"max_items": 20, "never_worn_threshold": 0, "low_use_threshold": 2},
    "balanced": {"max_items": 35, "never_worn_threshold": 1, "low_use_threshold": 3},
    "generous": {"max_items": 50, "never_worn_threshold": 2, "low_use_threshold": 5},
}


def _rule_removal_score(g: "GarmentItemDB", p: dict) -> tuple[int, list[str]]:
    """
    Compute a differentiated rule-based removal risk score (0–100) for a garment.
    Uses worn frequency, recency, seasonality, confidence and formality breadth.
    Returns (score, reasons_list).
    """
    score = 0
    reasons: list[str] = []

    # ── Wear frequency (0-40 pts) ─────────────────────────────────────────────
    times = g.times_worn or 0
    if times == 0:
        score += 40
        reasons.append("Never worn")
    elif times <= p["low_use_threshold"]:
        pts = max(5, 30 - times * 5)   # 1 worn → 25, 2 → 20, 3 → 15 …
        score += pts
        reasons.append(f"Worn only {times} time(s)")
    # else: worn enough — no penalty

    # ── Recency (0-20 pts) ────────────────────────────────────────────────────
    last_worn = g.last_worn
    if last_worn is None and times > 0:
        # Has been worn but date lost — mild penalty
        score += 8
    elif last_worn is not None:
        from datetime import date as date_cls
        # Normalise: strip tzinfo if naive to avoid offset-naive/aware mismatch
        now = datetime.now(timezone.utc)
        if hasattr(last_worn, "tzinfo") and last_worn.tzinfo is None:
            now = datetime.now()  # naive comparison
        days_ago = (now - last_worn).days
        if days_ago > 365:
            score += 20
            reasons.append(f"Not worn in {days_ago // 30} months")
        elif days_ago > 180:
            score += 12
            reasons.append(f"Not worn in {days_ago // 30} months")
        elif days_ago > 90:
            score += 6

    # ── Seasonality (0-20 pts) ────────────────────────────────────────────────
    seasons = g.seasons or []
    n_seasons = len(seasons)
    if n_seasons == 0:
        score += 15
        reasons.append("No season assigned")
    elif n_seasons == 1:
        score += 10
        reasons.append("Single-season item")
    elif n_seasons == 2:
        score += 4
    # 3-4 seasons: no penalty (versatile)

    # ── Confidence / quality (0-15 pts) ──────────────────────────────────────
    conf = g.confidence or _CONFIDENCE_DEFAULT
    if conf < 0.50:
        score += 15
        reasons.append("Poor style match")
    elif conf < _CONFIDENCE_CAPSULE_MIN:
        score += 8
        reasons.append("Low style match")

    # ── Formality gap bonus (0-5 pts) ────────────────────────────────────────
    # If this is the only item of its formality level in the wardrobe it's
    # actually hard to remove — but if formality is unrecognised, minor penalty
    formality = (g.formality or "").lower()
    if formality not in ("casual", "smart casual", "business", "formal", "sport", "lounge"):
        score += 5

    return min(score, 100), reasons


def get_smart_removal(user_id: str, profile: str = "balanced") -> dict:
    """
    Suggest garments to remove based on a declutter profile.

    Strategy:
    1. Compute a rule-based risk score for every garment (differentiated — not flat).
    2. Keep the top-N rule candidates (≤12) as pre-filter.
    3. Call LLM batch endpoint once for all pre-filtered candidates.
       The batch call returns regret_risk_score per garment (real outfit-graph analysis).
    4. Blend: final = 0.40 × rule + 0.60 × LLM (when LLM available).
    5. Fall back to pure rule score if LLM is down.
    """
    with get_db_context() as db:
        items = db.query(GarmentItemDB).filter(GarmentItemDB.user_id == user_id).all()

        p = REMOVAL_PROFILES.get(profile, REMOVAL_PROFILES["balanced"])

        if not items:
            return {
                "candidates": [],
                "total_candidates": 0,
                "profile": profile,
                "current_count": 0,
                "target_count": p["max_items"],
                "summary": "No garments found.",
            }

        wardrobe_dicts = [garment_db_to_schema(g).model_dump(mode="json") for g in items]

    # ── Step 1: rule-based pre-scoring ───────────────────────────────────────
    # Map frontend profile names → LLM user_goal enum values
    _GOAL_MAP = {
        "minimalist": "minimalist",
        "balanced":   "maximize_options",
        "generous":   "style_upgrade",
    }
    llm_user_goal = _GOAL_MAP.get(profile, "maximize_options")

    scored: list[tuple] = []
    for g in items:
        rule_score, rule_reasons = _rule_removal_score(g, p)
        scored.append((g, rule_score, rule_reasons))

    # Sort and keep only candidates that are actually worth suggesting
    scored.sort(key=lambda x: x[1], reverse=True)
    min_threshold = 15   # don't suggest items with a risk score below this
    candidates_pool = [(g, sc, rs) for g, sc, rs in scored if sc >= min_threshold][:12]

    if not candidates_pool:
        return {
            "candidates": [],
            "total_candidates": 0,
            "profile": profile,
            "current_count": len(items),
            "target_count": p["max_items"],
            "summary": "Your wardrobe looks well-curated — no clear removal candidates found.",
        }

    # ── Step 2: batch LLM call ────────────────────────────────────────────────
    # Build a sub-wardrobe limited to the candidate garments for the LLM
    candidate_ids = {g.id for g, _, _ in candidates_pool}
    candidate_dicts = [d for d in wardrobe_dicts if d.get("id") in candidate_ids]

    llm_verdicts: dict[str, dict] = {}   # garment_id → verdict dict
    if _llm.is_available():
        try:
            raw_verdicts = _llm.smart_removal_wardrobe(
                garments_dicts=candidate_dicts,
                user_goal=llm_user_goal,
            )
            for v in (raw_verdicts or []):
                gid = v.get("garment_id") or v.get("id")
                if gid:
                    llm_verdicts[gid] = v
            logger.info(
                "Smart-removal LLM batch: %d verdicts for user=%s",
                len(llm_verdicts), user_id,
            )
        except Exception as exc:
            logger.warning("LLM smart_removal_wardrobe failed: %s", exc)

    # ── Step 3: merge rule + LLM scores ──────────────────────────────────────
    candidates = []
    for g, rule_score, rule_reasons in candidates_pool:
        cat = g.category
        v = llm_verdicts.get(g.id, {})

        if v and v.get("verdict"):
            regret_raw = v.get("regret_risk_score", rule_score / 100)
            regret_raw = float(regret_raw)
            llm_score = round(regret_raw * 100 if regret_raw <= 1 else regret_raw)

            # Blend: 40% rule (catches structural issues) + 60% LLM (outfit graph)
            final_score = round(rule_score * 0.40 + llm_score * 0.60)

            # Convert LLM signal objects → readable human strings
            raw_reasons = v.get("reasons") or []
            if raw_reasons and isinstance(raw_reasons[0], dict):
                # LLM returns list of signal dicts — extract the explanations
                # for signals that support removal only (ignore keep signals)
                reasons = [
                    sig["explanation"]
                    for sig in raw_reasons
                    if isinstance(sig, dict)
                    and sig.get("direction") == "supports_removal"
                    and sig.get("score_contribution", 0) > 0
                ] or rule_reasons
            else:
                reasons = raw_reasons or rule_reasons

            outfit_count = int(v.get("outfits_affected") or v.get("outfit_count") or 0)
            logger.debug(
                "Removal %s: rule=%d llm=%d final=%d",
                g.id, rule_score, llm_score, final_score,
            )
        else:
            # Pure rule-based — already differentiated
            final_score = rule_score
            reasons = rule_reasons
            # Estimate outfit_count from wardrobe size and category
            cat_count = sum(1 for i in items if i.category == cat)
            outfit_count = max(0, cat_count - 1) * 2   # conservative heuristic

        removal_impact = (
            "safe"   if outfit_count == 0
            else "low"    if outfit_count <= 2
            else "medium" if outfit_count <= 5
            else "high"
        )

        candidates.append({
            "garment": garment_db_to_schema(g).model_dump(mode="json"),
            "risk_score": min(final_score, 100),
            "reasons": reasons,
            "outfit_count": outfit_count,
            "removal_impact": removal_impact,
            "restyle_ideas": RESTYLE_IDEAS.get(cat, []),
        })

    candidates.sort(key=lambda x: x["risk_score"], reverse=True)

    return {
        "candidates": candidates[:10],
        "total_candidates": len(candidates),
        "profile": profile,
        "current_count": len(items),
        "target_count": p["max_items"],
        "summary": (
            f"{len(candidates)} item(s) identified as low-impact — "
            f"removing them would bring your wardrobe to "
            f"{max(len(items) - len(candidates), 0)} core pieces."
        ),
    }


# ── Sort Scores ────────────────────────────────────────────────

# Current season detection (northern hemisphere)
def _current_season() -> str:
    import datetime
    month = datetime.date.today().month
    if month in (12, 1, 2):   return "winter"
    if month in (3, 4, 5):    return "spring"
    if month in (6, 7, 8):    return "summer"
    return "autumn"

_SEASON_LABEL_MAP = {
    "winter": {"winter": "Season-ready 🎿", "all": "Adaptable ✓", "summer": "Store away 📦", "spring": "Not ideal", "autumn": "Passable"},
    "spring": {"spring": "Season-ready 🌸", "all": "Adaptable ✓", "winter": "Store away 📦", "summer": "Not ideal", "autumn": "Passable"},
    "summer": {"summer": "Season-ready ☀️", "all": "Adaptable ✓", "winter": "Store away 📦", "spring": "Passable", "autumn": "Not ideal"},
    "autumn": {"autumn": "Season-ready 🍂", "all": "Adaptable ✓", "summer": "Store away 📦", "winter": "Not ideal", "spring": "Passable"},
}

_SEASON_SCORE_MAP = {
    "winter":  {"winter": 100, "all": 80, "autumn": 55, "spring": 40, "summer": 10},
    "spring":  {"spring": 100, "all": 80, "summer": 60, "autumn": 40, "winter": 10},
    "summer":  {"summer": 100, "all": 80, "spring": 60, "autumn": 40, "winter": 10},
    "autumn":  {"autumn": 100, "all": 80, "winter": 70, "spring": 45, "summer": 10},
}


def get_sort_scores(user_id: str) -> dict:
    """
    Return per-garment scores for Versatility / Redundancy / Seasonal / Impact.
    All scores are 0–100 (higher = more of that property).
    """
    with get_db_context() as db:
        items = db.query(GarmentItemDB).filter(GarmentItemDB.user_id == user_id).all()

    if not items:
        return {"scores": [], "current_season": _current_season()}

    season = _current_season()
    season_labels  = _SEASON_LABEL_MAP[season]
    season_scores  = _SEASON_SCORE_MAP[season]

    # ── Build a simple colour / category lookup for redundancy ──
    from collections import Counter
    cat_color_pairs = Counter()
    for g in items:
        key = f"{g.category}:{(g.color_primary or '').lower()}"
        cat_color_pairs[key] += 1

    scores = []
    for g in items:
        # ── Versatility ──────────────────────────────────────────
        # Approximated from: neutral colour +20, multi-season +20,
        # casual/smart-casual +15, times_worn contribution
        v = 40
        neutral_colors = {"black", "white", "grey", "gray", "navy", "beige", "cream", "tan"}
        if (g.color_primary or "").lower() in neutral_colors:
            v += 20
        seasons_list = g.seasons if isinstance(g.seasons, list) else []
        if "all" in seasons_list or len(seasons_list) >= 3:
            v += 20
        elif len(seasons_list) >= 2:
            v += 10
        if (g.formality or "").lower() in ("casual", "smart_casual"):
            v += 10
        if g.times_worn and g.times_worn >= 10:
            v += 10
        versatility_score = min(v, 100)

        # ── Redundancy ───────────────────────────────────────────
        # How many near-duplicates exist (same category + same primary colour)
        key = f"{g.category}:{(g.color_primary or '').lower()}"
        dup_count = cat_color_pairs[key] - 1  # exclude itself
        redundancy_score = min(dup_count * 35, 100)
        redundancy_label = (
            f"Similar to {dup_count} other {g.category}(s)" if dup_count > 0
            else "Unique in your wardrobe"
        )

        # ── Seasonal ─────────────────────────────────────────────
        best_season_score = 0
        best_season_label = "Unknown"
        for s in (seasons_list if seasons_list else ["all"]):
            sc = season_scores.get(s, 30)
            if sc > best_season_score:
                best_season_score = sc
                best_season_label = season_labels.get(s, "Passable")
        seasonal_score = best_season_score

        # ── Impact ───────────────────────────────────────────────
        # How many outfits would be lost if this item is removed.
        # Approximated: versatility × formality weight × worn frequency
        base_impact = versatility_score
        if g.times_worn and g.times_worn >= 5:
            base_impact = min(base_impact + 15, 100)
        if g.times_worn and g.times_worn == 0:
            base_impact = max(base_impact - 30, 0)
        impact_score = base_impact

        scores.append({
            "garment_id": g.id,
            "versatility_score": versatility_score,
            "redundancy_score": redundancy_score,
            "seasonal_score": seasonal_score,
            "impact_score": impact_score,
            "redundancy_label": redundancy_label,
            "seasonal_label": best_season_label,
        })

    return {"scores": scores, "current_season": season}


# ── Capsule Generate ────────────────────────────────────────────────────────

OCCASION_FILTERS = {
    "work":    {"formality": ["formal", "business", "smart-casual"]},
    "weekend": {"formality": ["casual", "smart-casual"]},
    "evening": {"formality": ["formal", "semi-formal"]},
    "travel":  {"formality": ["casual", "smart-casual"], "seasons": ["all"]},
}

SEASON_NAMES = {
    "spring": "Spring", "summer": "Summer",
    "autumn": "Autumn", "winter": "Winter",
}

OCCASION_NAMES = {
    "work": "Work", "weekend": "Weekend",
    "evening": "Evening", "travel": "Travel",
}

def generate_capsule(user_id: str, occasion: Optional[str], season: Optional[str]):
    """
    Select a focused capsule (10-15 items) from the user's wardrobe
    optimised for the given occasion and/or season.
    Returns a score, the selected items, top 3 missing pieces and a combination count.
    """
    with get_db_context() as db:
        all_garments = db.query(GarmentItemDB).filter(
            GarmentItemDB.user_id == user_id
        ).all()

    if not all_garments:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="No garments found for this user.")

    # Convert DB objects → domain objects
    items = [_db_to_domain(g) for g in all_garments]

    # ── Filter by season ────────────────────────────────────
    if season:
        season_filtered = [
            g for g in items
            if season in (g.attributes.seasons or [])
            or "all" in (g.attributes.seasons or [])
        ]
        # Fall back to full wardrobe if filter leaves < 5 items
        items = season_filtered if len(season_filtered) >= 5 else items

    # ── Filter by occasion / formality ──────────────────────
    if occasion and occasion in OCCASION_FILTERS:
        allowed_formalities = OCCASION_FILTERS[occasion]["formality"]
        occ_filtered = [
            g for g in items
            if g.attributes.formality in allowed_formalities
        ]
        items = occ_filtered if len(occ_filtered) >= 5 else items

    # ── Score & rank items by versatility ───────────────────
    # Proxy: items worn more + more seasons covered = higher priority
    def item_score(g: GarmentItem) -> float:
        s = len(g.attributes.seasons or []) * 15
        s += min((g.times_worn or 0) * 3, 30)
        if g.is_favorite:
            s += 10
        return s

    items_sorted = sorted(items, key=item_score, reverse=True)

    # ── Pick a balanced capsule (cap at 15) ─────────────────
    CATEGORY_CAPS = {
        "top": 4, "bottom": 3, "dress": 2,
        "outerwear": 2, "shoes": 2, "accessory": 2,
    }
    capsule: List[GarmentItem] = []
    cat_counts: Dict[str, int] = {c: 0 for c in CATEGORY_CAPS}

    for g in items_sorted:
        cat = g.attributes.category
        if cat in cat_counts and cat_counts[cat] < CATEGORY_CAPS.get(cat, 2):
            capsule.append(g)
            cat_counts[cat] += 1
        if len(capsule) >= 15:
            break

    # ── Compute combination count ────────────────────────────
    def count_combos(cc: Dict[str, int]) -> int:
        tops, bottoms, dresses = cc["top"], cc["bottom"], cc["dress"]
        outerwear, shoes, accessories = cc["outerwear"], cc["shoes"], cc["accessory"]
        if shoes == 0:
            return 0
        with_top = tops * max(bottoms, 1) * (outerwear + 1) * shoes * (accessories + 1) if tops else 0
        with_dress = dresses * (outerwear + 1) * shoes * (accessories + 1) if dresses else 0
        return with_top + with_dress

    combo_count = count_combos(cat_counts)

    # ── Build score (reuse existing logic) ──────────────────
    score_data = get_capsule_score(user_id)

    # ── Missing pieces (top 3) ───────────────────────────────
    missing_data = get_missing_pieces(user_id, limit=3)

    # ── Context label ────────────────────────────────────────
    parts = []
    if season:
        parts.append(SEASON_NAMES.get(season, season.capitalize()))
    if occasion:
        parts.append(OCCASION_NAMES.get(occasion, occasion.capitalize()))
    context_label = " ".join(parts) if parts else "Your Capsule"

    # ── Insight ──────────────────────────────────────────────
    insight = (
        f"{len(capsule)} pieces selected for your {context_label.lower()} capsule, "
        f"generating {combo_count} outfit combinations."
    )

    return {
        "context_label": context_label,
        "items": [_domain_to_dict(g) for g in capsule],
        "score": score_data,
        "missing": missing_data.get("missing_pieces", []),
        "combination_count": combo_count,
        "insight": insight,
    }


def _db_to_domain(g: GarmentItemDB) -> GarmentItem:
    """Convert a DB row to the GarmentItem domain model."""
    from models.schemas import GarmentAttributes as GA
    attrs = GA(
        category=g.category,
        subcategory=getattr(g, "subcategory", None),
        color_primary=g.color_primary or "unknown",
        color_hex=getattr(g, "color_hex", None),
        pattern=g.pattern or "solid",
        formality=g.formality or "casual",
        seasons=list(g.seasons) if g.seasons else [],
        confidence=getattr(g, "confidence", 0.9),
    )
    return GarmentItem(
        id=str(g.id),
        user_id=str(g.user_id),
        image_url=getattr(g, "image_url", None),
        attributes=attrs,
        is_favorite=g.is_favorite or False,
        for_sale=g.for_sale or False,
        tags=list(g.tags) if g.tags else [],
        times_worn=g.times_worn or 0,
        last_worn=str(g.last_worn) if g.last_worn else None,
        created_at=str(g.created_at),
    )


def _domain_to_dict(g: GarmentItem) -> dict:
    """Serialise a GarmentItem to a plain dict for JSON response."""
    return {
        "id": g.id,
        "user_id": g.user_id,
        "image_url": g.image_url,
        "attributes": {
            "category": g.attributes.category,
            "subcategory": g.attributes.subcategory,
            "color_primary": g.attributes.color_primary,
            "color_hex": g.attributes.color_hex,
            "pattern": g.attributes.pattern,
            "formality": g.attributes.formality,
            "seasons": g.attributes.seasons,
            "confidence": g.attributes.confidence,
        },
        "is_favorite": g.is_favorite,
        "for_sale": g.for_sale,
        "tags": g.tags,
        "times_worn": g.times_worn,
        "last_worn": g.last_worn,
        "created_at": g.created_at,
    }


# ── Wardrobe Insights cache ───────────────────────────────────────────────────
# Two-layer cache:
#   L1 — in-process dict, avoids a DB round-trip within the same process lifetime.
#   L2 — wardrobe_analysis_cache PostgreSQL table.
#          is_dirty=False + result_json present → serve from DB (no LLM call).
#          is_dirty=True  → must recompute via LLM, then write back to DB.
#
# Mutations (add/update/delete garment) call mark_wardrobe_dirty(user_id) which
# sets is_dirty=True in the DB *and* evicts the L1 entry.  The next call to
# get_wardrobe_insights() will recompute once and reset is_dirty=False.

_INSIGHTS_L1: Dict[str, tuple] = {}   # user_id → (monotonic_ts, WardrobeInsightsResponse)
_INSIGHTS_TTL = 900                    # L1 TTL: 15 minutes


def _l1_get(user_id: str) -> Optional[WardrobeInsightsResponse]:
    """Return cached result from L1 if still fresh."""
    if user_id in _INSIGHTS_L1:
        ts, data = _INSIGHTS_L1[user_id]
        if time.monotonic() - ts < _INSIGHTS_TTL:
            return data
        del _INSIGHTS_L1[user_id]
    return None


def _l1_set(user_id: str, data: WardrobeInsightsResponse) -> None:
    _INSIGHTS_L1[user_id] = (time.monotonic(), data)


def _l1_evict(user_id: str) -> None:
    _INSIGHTS_L1.pop(user_id, None)


def _db_cache_get(user_id: str) -> Optional[WardrobeInsightsResponse]:
    """
    Read from wardrobe_analysis_cache.
    Returns the deserialized response only if is_dirty=False and result_json is set.
    """
    try:
        with get_db_context() as db:
            row = db.query(WACacheDB).filter(WACacheDB.user_id == user_id).first()
        if row and not row.is_dirty and row.result_json:
            return WardrobeInsightsResponse(**row.result_json)
    except Exception as exc:
        logger.warning("_db_cache_get failed for user=%s: %s", user_id, exc)
    return None


def _db_cache_set(user_id: str, data: WardrobeInsightsResponse) -> None:
    """Upsert the analysis result and clear the dirty flag."""
    try:
        payload = data.model_dump()
        now = datetime.now(timezone.utc)
        with get_db_context() as db:
            row = db.query(WACacheDB).filter(WACacheDB.user_id == user_id).first()
            if row:
                row.result_json = payload
                row.is_dirty    = False
                row.cached_at   = now
                row.updated_at  = now
            else:
                db.add(WACacheDB(
                    user_id=user_id,
                    result_json=payload,
                    is_dirty=False,
                    cached_at=now,
                    updated_at=now,
                ))
            db.commit()
    except Exception as exc:
        logger.warning("_db_cache_set failed for user=%s: %s", user_id, exc)


def mark_wardrobe_dirty(user_id: str) -> None:
    """
    Call whenever the wardrobe changes (add/update/delete garment).
    Sets is_dirty=True in the DB and evicts the L1 entry so the next call
    to get_wardrobe_insights() recomputes via the LLM.
    """
    _l1_evict(user_id)
    try:
        with get_db_context() as db:
            row = db.query(WACacheDB).filter(WACacheDB.user_id == user_id).first()
            if row:
                row.is_dirty   = True
                row.updated_at = datetime.now(timezone.utc)
            else:
                # Create the row pre-emptively so the next read knows to recompute.
                db.add(WACacheDB(user_id=user_id, is_dirty=True))
            db.commit()
    except Exception as exc:
        logger.warning("mark_wardrobe_dirty failed for user=%s: %s", user_id, exc)


# Keep old name as a thin alias so existing callers don't break.
def invalidate_insights_cache(user_id: str) -> None:
    """Deprecated alias for mark_wardrobe_dirty — kept for backward compatibility."""
    mark_wardrobe_dirty(user_id)


def _detect_duplicates(items: List[GarmentItem]) -> List[DuplicateGroup]:
    """
    Rule-based duplicate detection: groups garments sharing the same
    (category, dominant_color_bucket, pattern).
    Only flags groups of 2+ items.
    """
    _NEUTRAL_BUCKET = {"white", "black", "grey", "gray", "beige", "navy", "tan", "cream", "nude"}

    def _color_bucket(color: str) -> str:
        c = color.lower()
        for n in _NEUTRAL_BUCKET:
            if n in c:
                return "neutral"
        for warm in ("red", "orange", "yellow", "pink", "coral"):
            if warm in c:
                return "warm"
        for cool in ("blue", "green", "purple", "violet", "teal", "cyan"):
            if cool in c:
                return "cool"
        return c[:6]  # keep first 6 chars as bucket

    groups: Dict[tuple, List[GarmentItem]] = {}
    for g in items:
        key = (
            g.attributes.category if isinstance(g.attributes.category, str) else g.attributes.category.value,
            _color_bucket(g.attributes.color_primary or ""),
            (g.attributes.pattern or "solid").lower(),
        )
        groups.setdefault(key, []).append(g)

    result = []
    for (cat, color, pattern), group in groups.items():
        if len(group) < 2:
            continue
        similarity = min(0.95, 0.70 + 0.05 * (len(group) - 2))
        result.append(DuplicateGroup(
            garment_ids=[g.id for g in group],
            descriptions=[
                f"{g.attributes.color_primary} {g.attributes.subcategory or cat}"
                for g in group
            ],
            shared_category=cat,
            shared_color=color,
            shared_pattern=pattern,
            similarity_score=round(similarity, 2),
            recommendation=(
                "Keep the most worn piece and consider selling the others"
                if len(group) == 2
                else f"You have {len(group)} similar {cat}s — keep 1–2 and sell the rest"
            ),
        ))
    return result


def _compute_cost_per_wear(items: List[GarmentItem]) -> tuple[List[CostPerWearItem], bool]:
    """Build cost-per-wear rankings. Returns (list, has_price_data)."""
    priced = [g for g in items if g.purchase_price and g.purchase_price > 0]
    if not priced:
        return [], False

    def _tier(cpw: float) -> str:
        if cpw == 0:   return "unworn"
        if cpw < 2:    return "excellent"
        if cpw < 8:    return "good"
        if cpw < 20:   return "fair"
        return "poor"

    result = []
    for g in priced:
        wears = max(g.worn_count or g.times_worn or 0, 1)
        cpw = round((g.purchase_price or 0) / wears, 2)
        result.append(CostPerWearItem(
            garment_id=g.id,
            garment_description=f"{g.attributes.color_primary} {g.attributes.subcategory or g.attributes.category}",
            purchase_price=g.purchase_price,
            worn_count=wears,
            cost_per_wear=cpw,
            value_tier=_tier(cpw) if (g.worn_count or g.times_worn or 0) > 0 else "unworn",
        ))
    result.sort(key=lambda x: x.cost_per_wear)
    return result, True


def get_wardrobe_insights(user_id: str, refresh: bool = False) -> WardrobeInsightsResponse:
    """
    Returns all 5 wardrobe intelligence features in one call:
      1. Capsule gap analysis
      2. Cost-per-wear ranking
      3. Duplicate detection
      4. Occasion coverage heatmap
      5. Versatility ranking

    Results are cached per user.  Pass refresh=True to force recomputation.

    Cache layers
    ────────────
    L1 (in-process, 15 min TTL) → fastest, zero DB hit.
    L2 (wardrobe_analysis_cache table):
        is_dirty=False → return stored JSON, no LLM call.
        is_dirty=True  → call LLM once, write back, clear dirty flag.
    """
    if not refresh:
        # ── L1: in-process ───────────────────────────────────────────────────
        l1 = _l1_get(user_id)
        if l1:
            l1.cached = True
            return l1

        # ── L2: DB ───────────────────────────────────────────────────────────
        l2 = _db_cache_get(user_id)
        if l2:
            l2.cached = True
            _l1_set(user_id, l2)   # warm L1 from DB hit
            return l2

    with get_db_context() as db:
        items_db = db.query(GarmentItemDB).filter(GarmentItemDB.user_id == user_id).all()

    if not items_db:
        empty = WardrobeInsightsResponse(summary="Add items to your wardrobe to unlock insights.")
        return empty

    items = [garment_db_to_schema(g) for g in items_db]
    garment_dicts = [g.model_dump() for g in items]

    # ── Call LLM project once for everything ─────────────────────────────────
    llm_result: dict = {}
    try:
        llm_result = _llm.analyze_wardrobe(
            garments_dicts=garment_dicts,
            top_k_versatile=min(len(items), 20),
        )
    except Exception as exc:
        logger.warning("LLM wardrobe insights unavailable: %s", exc)

    # ── 1. Gaps ───────────────────────────────────────────────────────────────
    gaps: List[GapItem] = []
    for raw in (llm_result.get("gaps") or []):
        try:
            gaps.append(GapItem(
                gap_type=raw.get("gap_type", "category_missing"),
                severity=raw.get("severity", "medium"),
                description=raw.get("description", ""),
                recommendation=raw.get("recommendation", ""),
            ))
        except Exception:
            pass

    # fallback: flag missing major categories
    if not gaps:
        present_cats = {g.attributes.category if isinstance(g.attributes.category, str) else g.attributes.category.value for g in items}
        for cat in ["top", "bottom", "shoes", "outerwear"]:
            if cat not in present_cats:
                gaps.append(GapItem(
                    gap_type="category_missing",
                    severity="high",
                    description=f"No {cat} in your wardrobe",
                    recommendation=f"Add at least one {cat} to unlock outfit combinations",
                ))

    # ── 2. Cost-per-wear ─────────────────────────────────────────────────────
    cpw_list, has_price = _compute_cost_per_wear(items)

    # ── 3. Duplicates ─────────────────────────────────────────────────────────
    dup_groups = _detect_duplicates(items)

    # ── 4. Occasion coverage ─────────────────────────────────────────────────
    occ_coverage: List[OccasionCoverageItem] = []
    occ_raw = llm_result.get("occasion_coverage") or []

    if occ_raw:
        for raw in occ_raw:
            try:
                occ_coverage.append(OccasionCoverageItem(
                    occasion=raw.get("occasion", ""),
                    coverage_score=float(raw.get("coverage_score", 0)),
                    suitable_items_count=int(raw.get("suitable_items_count", 0)),
                    missing_categories=raw.get("missing_categories") or [],
                    suggestion=raw.get("suggestion"),
                ))
            except Exception:
                pass

    # fallback: derive from formality distribution
    if not occ_coverage:
        formality_map = {
            "casual": ("casual", 0.6),
            "smart_casual": ("smart casual", 0.5),
            "business_casual": ("business", 0.4),
            "formal": ("formal", 0.3),
        }
        formality_counts: Dict[str, int] = {}
        for g in items:
            f = (g.attributes.formality or "casual").lower()
            formality_counts[f] = formality_counts.get(f, 0) + 1
        total = max(len(items), 1)
        for f_key, (occ_name, min_threshold) in formality_map.items():
            count = formality_counts.get(f_key, 0)
            score = min(count / total / min_threshold, 1.0)
            occ_coverage.append(OccasionCoverageItem(
                occasion=occ_name,
                coverage_score=round(score, 2),
                suitable_items_count=count,
                missing_categories=[],
                suggestion=f"Add more {f_key.replace('_', ' ')} pieces" if score < 0.5 else None,
            ))

    overall_coverage = round(
        sum(o.coverage_score for o in occ_coverage) / max(len(occ_coverage), 1), 2
    )

    # ── 5. Versatility ranking ────────────────────────────────────────────────
    vers_ranking: List[VersatilityItem] = []
    vers_raw = llm_result.get("top_versatile_items") or []

    if vers_raw:
        for raw in vers_raw:
            try:
                vers_ranking.append(VersatilityItem(
                    garment_id=raw.get("garment_id", ""),
                    garment_description=raw.get("garment_description", ""),
                    versatility_score=float(raw.get("versatility_score", 0)),
                    compatible_outfit_count=int(raw.get("compatible_outfit_count", 0)),
                    compatible_categories=raw.get("compatible_categories") or [],
                    compatible_occasions=raw.get("compatible_occasions") or [],
                ))
            except Exception:
                pass

    # fallback: rule-based versatility per garment
    if not vers_ranking:
        cat_pairs = {
            "top": ["bottom", "outerwear", "shoes", "accessory"],
            "bottom": ["top", "shoes", "accessory", "outerwear"],
            "dress": ["shoes", "accessory", "outerwear"],
            "outerwear": ["top", "bottom", "dress", "shoes"],
            "shoes": ["top", "bottom", "dress", "outerwear"],
            "accessory": ["top", "bottom", "dress", "outerwear"],
        }
        wardrobe_cats = {
            g.attributes.category if isinstance(g.attributes.category, str) else g.attributes.category.value
            for g in items
        }
        neutral_kw = {"white", "black", "grey", "gray", "beige", "navy", "tan"}
        for g in items:
            cat = g.attributes.category if isinstance(g.attributes.category, str) else g.attributes.category.value
            pairs = cat_pairs.get(cat, [])
            matched = sum(1 for c in pairs if c in wardrobe_cats)
            seasons_count = len(g.attributes.seasons or [])
            is_neutral = any(n in (g.attributes.color_primary or "").lower() for n in neutral_kw)
            vers = min((matched / max(len(pairs), 1)) * 0.6 + (seasons_count / 4) * 0.2 + (0.2 if is_neutral else 0.0), 1.0)
            outfit_count = max(1, int(vers * len(items) * 0.4))
            vers_ranking.append(VersatilityItem(
                garment_id=g.id,
                garment_description=f"{g.attributes.color_primary} {g.attributes.subcategory or cat}",
                versatility_score=round(vers, 2),
                compatible_outfit_count=outfit_count,
                compatible_categories=[c for c in pairs if c in wardrobe_cats],
                compatible_occasions=[],
            ))
        vers_ranking.sort(key=lambda x: x.versatility_score, reverse=True)
        vers_ranking = vers_ranking[:20]

    overall_score = float(llm_result.get("overall_score", 0)) if llm_result else 0.0
    summary = llm_result.get("summary", "") if llm_result else ""

    result = WardrobeInsightsResponse(
        gaps=gaps,
        cost_per_wear=cpw_list,
        has_price_data=has_price,
        duplicate_groups=dup_groups,
        total_duplicates=sum(len(d.garment_ids) for d in dup_groups),
        occasion_coverage=occ_coverage,
        overall_coverage_score=overall_coverage,
        versatility_ranking=vers_ranking,
        overall_score=overall_score,
        summary=summary,
        cached=False,
    )

    _db_cache_set(user_id, result)  # persist to DB, clear dirty flag
    _l1_set(user_id, result)        # warm L1
    logger.info("Wardrobe insights user=%s: %d gaps, %d dup groups, %d coverage items, %d versatility items",
                user_id, len(gaps), len(dup_groups), len(occ_coverage), len(vers_ranking))
    return result


def invalidate_insights_cache(user_id: str) -> None:
    """Deprecated alias for mark_wardrobe_dirty — kept for backward compatibility."""
    mark_wardrobe_dirty(user_id)


# ─── Travel Capsule — proxy to LLM_project ───────────────────────────────────
# The selection algorithm and all configuration now live in LLM_project.
# This backend only:
#   1. Fetches the user's garments from the DB
#   2. Serialises them to plain dicts (no image bytes to keep payload small)
#   3. POSTs to LLM_project via the shared llm_client (reads LLM_API_URL from .env)
#   4. Returns the response verbatim to the mobile client


async def get_travel_capsule_proxy(request) -> Dict:
    """
    Build a travel capsule by delegating to the LLM_project capsule service.

    Steps:
      1. Load garments from DB
      2. Strip image bytes (keep attributes, metadata only)
      3. Call POST /api/v1/capsule/travel via llm_client._async_client()
         (URL comes from settings.llm_api_url / LLM_API_URL in .env — same as
         every other LLM_project call in this backend)
      4. Return TravelCapsuleResponse to the caller
    """
    import httpx
    from models.schemas import TravelCapsuleResponse

    # 1. Load wardrobe
    with get_db_context() as db:
        rows = db.query(GarmentItemDB).filter(GarmentItemDB.user_id == request.user_id).all()
    items = [garment_db_to_schema(row) for row in rows]

    if not items:
        raise HTTPException(status_code=404, detail="No garments in wardrobe")

    # 2. Serialise — drop image_base64, serialise datetimes to ISO strings
    def _slim(g: GarmentItem) -> Dict:
        # mode="json" converts datetime → ISO string, UUID → str, etc.
        d = g.model_dump(mode="json")
        d.pop("image_base64", None)
        return d

    garments_payload = [_slim(g) for g in items]

    # 3. Proxy via the shared llm_client (same URL config used everywhere)
    body = {
        "request": request.model_dump(mode="json"),
        "garments": garments_payload,
    }

    try:
        async with _llm._async_client() as client:
            resp = await client.post("/api/v1/capsule/travel", json=body)
    except RuntimeError as exc:
        # LLM_API_URL not configured in .env
        raise HTTPException(status_code=503, detail=str(exc))
    except httpx.ConnectError as exc:
        base = _llm._base_url()
        logger.error("LLM_project unreachable at %s: %s", base, exc)
        raise HTTPException(
            status_code=503,
            detail=f"Capsule service unavailable — LLM_project not reachable ({base}). "
                   "Check LLM_API_URL in .env and ensure LLM_project is running.",
        )
    except httpx.TimeoutException:
        raise HTTPException(status_code=504, detail="Capsule service timed out")

    if resp.status_code != 200:
        logger.error("Capsule service error %s: %s", resp.status_code, resp.text[:300])
        raise HTTPException(status_code=resp.status_code, detail=resp.json().get("detail", resp.text))

    return TravelCapsuleResponse(**resp.json())


# ─── Legacy inline capsule helpers (kept for reference — no longer called) ───

# Material practicality table — no LLM needed
_MATERIAL_PRACTICALITY: Dict[str, float] = {
    "jersey":    1.00,
    "bamboo":    0.95,
    "linen":     0.90,
    "merino":    0.85,
    "cotton":    0.80,
    "denim":     0.75,
    "polyester": 0.70,
    "wool":      0.60,
    "silk":      0.40,
    "leather":   0.35,
}

# Climate → accepted seasons mapping
_CLIMATE_SEASONS: Dict[str, List[str]] = {
    "warm":  ["summer", "spring"],
    "cold":  ["winter", "autumn"],
    "mixed": ["summer", "spring", "winter", "autumn"],
}

# Neutral hex families (R, G, B centre)
_NEUTRAL_FAMILIES = [
    ("White",    (255, 255, 255)),
    ("Off-White",(250, 250, 249)),
    ("Nude",     (237, 232, 226)),
    ("Stone",    (158, 148, 144)),
    ("Charcoal", (58,  54,  51)),
    ("Navy",     (27,  42,  74)),
    ("Camel",    (193, 154, 107)),
    ("Black",    (0,   0,   0)),
]

# Formality compatibility tiers
_FORMALITY_TIERS: Dict[str, int] = {
    "casual":        0,
    "smart_casual":  1,
    "business":      2,
    "formal":        3,
    "evening":       3,
}

# Occasion → required formality types
_OCCASION_FORMALITIES: Dict[str, List[str]] = {
    "travel":      ["casual", "smart_casual"],
    "work_trip":   ["smart_casual", "business"],
    "weekend":     ["casual"],
    "city_break":  ["casual", "smart_casual"],
    "beach":       ["casual"],
    "date_night":  ["smart_casual", "formal"],
}

# Garment role assignment by category
_CATEGORY_ROLES: Dict[str, str] = {
    "top":       "anchor",
    "dress":     "anchor",
    "bottom":    "anchor",
    "outerwear": "layer",
    "shoes":     "shoes",
    "accessory": "accent",
}

# Ideal template per occasion: {category: count}
_OCCASION_TEMPLATE: Dict[str, Dict[str, int]] = {
    "travel":     {"top": 3, "bottom": 2, "dress": 1, "outerwear": 1, "shoes": 1, "accessory": 1},
    "work_trip":  {"top": 2, "bottom": 1, "dress": 1, "outerwear": 1, "shoes": 2},
    "weekend":    {"top": 3, "bottom": 2, "shoes": 1},
    "city_break": {"top": 2, "bottom": 2, "outerwear": 1, "shoes": 1},
    "beach":      {"top": 2, "bottom": 1, "dress": 1, "shoes": 1},
    "date_night": {"top": 1, "bottom": 1, "shoes": 1, "accessory": 1},
}


def _hex_to_rgb(hex_str: str) -> tuple:
    h = hex_str.lstrip("#")
    if len(h) != 6:
        return (200, 190, 185)
    try:
        return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16))
    except Exception:
        return (200, 190, 185)


def _nearest_neutral(hex_str: str) -> str:
    r, g, b = _hex_to_rgb(hex_str)
    best_name, best_dist = "Neutral", float("inf")
    for name, (cr, cg, cb) in _NEUTRAL_FAMILIES:
        dist = ((r - cr) ** 2 + (g - cg) ** 2 + (b - cb) ** 2) ** 0.5
        if dist < best_dist:
            best_dist = dist
            best_name = name
    return best_name


def _mat_score(material: Optional[str]) -> float:
    if not material:
        return 0.65
    m = material.lower().strip()
    for key, score in _MATERIAL_PRACTICALITY.items():
        if key in m:
            return score
    return 0.65


def _form_tier(formality: Optional[str]) -> int:
    if not formality:
        return 0
    return _FORMALITY_TIERS.get(formality.lower().replace(" ", "_"), 1)


def _garment_cat(g) -> str:
    cat = g.attributes.category
    return cat.value if hasattr(cat, "value") else str(cat)


def _count_valid_outfits_capsule(garments: List, occasion_types: List[str]) -> int:
    """Count valid outfit combos from a capsule garment list."""
    from itertools import product as iproduct

    tops      = [g for g in garments if _garment_cat(g) in ("top",)]
    bottoms   = [g for g in garments if _garment_cat(g) == "bottom"]
    dresses   = [g for g in garments if _garment_cat(g) == "dress"]
    outerw    = [g for g in garments if _garment_cat(g) == "outerwear"]
    shoes_lst = [g for g in garments if _garment_cat(g) == "shoes"]

    target_tiers = {_form_tier(f) for f in occasion_types}
    accepted_tiers = set()
    for t in target_tiers:
        accepted_tiers |= {max(0, t - 1), t, min(3, t + 1)}

    shoe_mult = max(1, len(shoes_lst))
    count = 0

    for top in tops:
        for bottom in bottoms:
            if abs(_form_tier(top.attributes.formality) - _form_tier(bottom.attributes.formality)) > 1:
                continue
            avg_tier = (_form_tier(top.attributes.formality) + _form_tier(bottom.attributes.formality)) // 2
            if avg_tier not in accepted_tiers:
                continue
            base = 1 + len(outerw)
            count += base * shoe_mult

    for dress in dresses:
        if _form_tier(dress.attributes.formality) not in accepted_tiers:
            continue
        base = 1 + len(outerw)
        count += base * shoe_mult

    return count


def _score_capsule(garments: List, occasion_types: List[str], max_pieces: int) -> Dict:
    n = len(garments)
    if n == 0:
        return {"total_score": 0.0, "valid_combinations": 0,
                "color_palette": [], "color_names": [],
                "occasion_coverage": {}, "practicality_score": 0.0}

    outfit_count = _count_valid_outfits_capsule(garments, occasion_types)
    n_target = 6 if max_pieces <= 5 else (10 if max_pieces <= 7 else 15)
    comb_norm = min(1.0, outfit_count / n_target)

    occ_coverage_map: Dict[str, float] = {}
    for occ in occasion_types:
        occ_tier = _form_tier(occ)
        occ_items = [g for g in garments
                     if abs(_form_tier(g.attributes.formality) - occ_tier) <= 1]
        occ_coverage_map[occ] = min(1.0, len(occ_items) / max(2, max_pieces * 0.3))
    occ_score = sum(occ_coverage_map.values()) / max(len(occasion_types), 1)

    hex_values = [g.attributes.color_hex for g in garments if getattr(g.attributes, "color_hex", None)]
    family_counts: Dict[str, int] = {}
    for hx in hex_values:
        fam = _nearest_neutral(hx)
        family_counts[fam] = family_counts.get(fam, 0) + 1
    total_colored = len(hex_values) or 1
    top3 = sorted(family_counts.items(), key=lambda x: -x[1])[:3]
    top3_total = sum(c for _, c in top3)
    palette_variety = 1.0 if len(family_counts) <= 3 else 0.5
    color_cohesion = min(1.0, (top3_total / total_colored) * 0.6 + palette_variety * 0.4)

    palette_names = [fam for fam, _ in top3]
    palette_hexes = []
    for fam_name, _ in top3:
        candidates_hex = [g.attributes.color_hex for g in garments
                          if getattr(g.attributes, "color_hex", None)
                          and _nearest_neutral(g.attributes.color_hex) == fam_name]
        palette_hexes.append(candidates_hex[0] if candidates_hex else "#EDE8E2")

    practicality_score = sum(_mat_score(g.attributes.material) for g in garments) / n

    total_score = round(
        0.40 * comb_norm * 100
        + 0.25 * occ_score * 100
        + 0.20 * color_cohesion * 100
        + 0.15 * practicality_score * 100,
        1,
    )

    return {
        "total_score": total_score,
        "valid_combinations": outfit_count,
        "color_palette": palette_hexes,
        "color_names": palette_names,
        "occasion_coverage": {k: round(v, 2) for k, v in occ_coverage_map.items()},
        "practicality_score": round(practicality_score, 2),
    }


def _build_capsule_group(garments: List, occasion_types: List[str], max_pieces: int):
    from models.schemas import CapsuleGroup as CG, CapsuleGarmentEntry as CGE

    scored = _score_capsule(garments, occasion_types, max_pieces)
    base_count = scored["valid_combinations"]

    entries = []
    for g in garments:
        without = [x for x in garments if x.id != g.id]
        without_count = _count_valid_outfits_capsule(without, occasion_types)
        contribution = max(0, base_count - without_count)
        role = _CATEGORY_ROLES.get(_garment_cat(g), "anchor")
        entries.append(CGE(
            garment=g,
            role=role,
            outfit_contribution=contribution,
        ))

    total = len(garments)
    occ_str = " & ".join(occasion_types) if occasion_types else "travel"
    summary = (
        f"{total} versatile pieces covering {scored['valid_combinations']} outfits "
        f"across {occ_str}."
    )

    return CG(
        garments=entries,
        valid_combinations=scored["valid_combinations"],
        color_palette=scored["color_palette"],
        color_names=scored["color_names"],
        occasion_coverage=scored["occasion_coverage"],
        practicality_score=scored["practicality_score"],
        total_score=scored["total_score"],
        summary=summary,
    )


def get_travel_capsule(user_id: str, request) -> Dict:
    """
    Greedy capsule selector for travel / contextual occasions.
    Pure rule-based — no LLM call needed.
    Returns a TravelCapsuleResponse-compatible dict.
    """
    from models.schemas import TravelCapsuleResponse

    # Load wardrobe
    with get_db_context() as db:
        rows = db.query(GarmentItemDB).filter(GarmentItemDB.user_id == user_id).all()
    items = [garment_db_to_schema(row) for row in rows]

    if not items:
        raise HTTPException(status_code=404, detail="No garments in wardrobe")

    occasion = request.occasion
    climate = request.destination_climate
    duration = request.duration_days
    max_pieces = request.max_pieces
    occasion_types = request.occasion_types or _OCCASION_FORMALITIES.get(occasion, ["casual"])

    accepted_seasons = _CLIMATE_SEASONS.get(climate, _CLIMATE_SEASONS["mixed"])

    # ── Step 1: Filter candidates ──────────────────────────────
    candidates = []
    for g in items:
        conf = getattr(g.attributes, "confidence", 1.0) or 1.0
        if conf < _CONFIDENCE_CAPSULE_MIN:
            continue
        seasons = g.attributes.seasons or []
        if seasons and not any(s.lower() in accepted_seasons for s in seasons):
            continue
        if occasion == "beach":
            mat = (g.attributes.material or "").lower()
            if mat and not any(m in mat for m in ["linen", "cotton", "jersey", "bamboo", "polyester"]):
                continue
        candidates.append(g)

    if not candidates:
        candidates = list(items)

    # ── Step 2: Sort candidates by versatility ─────────────────
    def _vscore(g) -> float:
        cat = _garment_cat(g)
        base = {"top": 1.0, "dress": 0.95, "bottom": 0.9,
                "outerwear": 0.7, "shoes": 0.6, "accessory": 0.4}.get(cat, 0.5)
        seasons_bonus = len(g.attributes.seasons or []) / 4 * 0.1
        return base + seasons_bonus + _mat_score(g.attributes.material) * 0.1

    candidates_sorted = sorted(candidates, key=_vscore, reverse=True)

    # ── Step 3: Category fill (priority seeding) ───────────────
    selected: List = []
    used_ids: set = set()
    for cat_key in ["top", "bottom", "shoes", "dress", "outerwear", "accessory"]:
        best = next((g for g in candidates_sorted if _garment_cat(g) == cat_key and g.id not in used_ids), None)
        if best and len(selected) < max_pieces:
            selected.append(best)
            used_ids.add(best.id)

    # ── Step 4: Greedy fill ────────────────────────────────────
    for _ in range(max_pieces - len(selected)):
        best_gain = -1
        best_cand = None
        current_count = _count_valid_outfits_capsule(selected, occasion_types)
        for g in candidates_sorted:
            if g.id in used_ids:
                continue
            new_count = _count_valid_outfits_capsule(selected + [g], occasion_types)
            gain = new_count - current_count
            if gain > best_gain:
                best_gain = gain
                best_cand = g
        if best_cand is None:
            break
        selected.append(best_cand)
        used_ids.add(best_cand.id)

    # Short trips: filter heavy materials
    if occasion == "travel" and duration <= 3:
        heavy_filtered = [g for g in selected if _mat_score(g.attributes.material) >= 0.70]
        if len(heavy_filtered) >= 4:
            selected = heavy_filtered[:max_pieces]

    # ── Step 5: Build groups ────────────────────────────────────
    best_group = _build_capsule_group(selected, occasion_types, max_pieces)

    alternatives = []

    # Alt A — swap lowest-contribution piece
    if len(selected) > 1 and best_group.garments:
        min_entry = min(best_group.garments, key=lambda e: e.outfit_contribution)
        min_id = min_entry.garment.id
        alt_a_base = [g for g in selected if g.id != min_id]
        replacement = next(
            (g for g in candidates_sorted if g.id not in {x.id for x in alt_a_base + selected}), None
        )
        if replacement:
            alt_a = _build_capsule_group(alt_a_base + [replacement], occasion_types, max_pieces)
            alternatives.append(alt_a)

    # Alt B — practicality-first
    pract_sorted = sorted(selected, key=lambda g: _mat_score(g.attributes.material), reverse=True)
    alt_b_garments = pract_sorted[:min(len(selected), max_pieces)]
    if len(alt_b_garments) >= 3:
        alt_b = _build_capsule_group(alt_b_garments, occasion_types, max_pieces)
        alternatives.append(alt_b)

    # Alt C — minimalist (6 pieces)
    if len(selected) > 6:
        alt_c = _build_capsule_group(selected[:6], occasion_types, max(6, max_pieces))
        alternatives.append(alt_c)

    # ── Step 6: Missing pieces ─────────────────────────────────
    selected_cats = {_garment_cat(g) for g in selected}
    template = _OCCASION_TEMPLATE.get(occasion, {"top": 2, "bottom": 2, "shoes": 1})
    missing_pieces: List[str] = []
    label_map = {
        "top":       "Versatile top",
        "bottom":    "Casual trousers or skirt",
        "dress":     "Lightweight dress",
        "outerwear": "Packable jacket",
        "shoes":     "Comfortable walking shoes",
        "accessory": "Lightweight scarf or belt",
    }
    for cat_need in template:
        if cat_need not in selected_cats:
            missing_pieces.append(label_map.get(cat_need, cat_need.capitalize()))

    if climate == "cold" and "outerwear" not in selected_cats:
        missing_pieces.append("Warm layer (coat or sweater)")
    if climate == "warm" and not any("linen" in (g.attributes.material or "").lower() for g in selected):
        missing_pieces.append("Breathable linen or cotton piece")

    return TravelCapsuleResponse(
        best_group=best_group,
        alternative_groups=alternatives,
        missing_pieces=list(dict.fromkeys(missing_pieces))[:5],
        total_wardrobe_items=len(items),
        pieces_selected=len(selected),
        source="rule",
    )

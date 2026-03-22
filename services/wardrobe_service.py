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
import time
from datetime import datetime, timezone
from typing import Optional, List, Dict

logger = logging.getLogger(__name__)

from fastapi import HTTPException
from models.schemas import (
    GarmentItem, GarmentAttributes, GarmentCategory,
    GarmentExtractionResult, ExtractionWarning,
    WardrobeInsightsResponse, GapItem, OccasionCoverageItem,
    VersatilityItem, DuplicateGroup, CostPerWearItem,
)
from models.database import GarmentItem as GarmentItemDB, WardrobeAnalysisCache as WACacheDB
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
        created_at=g.created_at,
    )


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
        )
        db.add(garment)
        db.commit()
        db.refresh(garment)
        # ── Sync to Neo4j (fire-and-forget) ──────────────
        neo4j_service.upsert_garment(
            garment_id=garment.id,
            user_id=user_id,
            attrs={**attrs, "image_url": garment.image_url or ""},
        )
        return garment_db_to_schema(garment)


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
    """Delete a garment from PostgreSQL and Neo4j."""
    with get_db_context() as db:
        garment = db.query(GarmentItemDB).filter(
            GarmentItemDB.id == garment_id,
            GarmentItemDB.user_id == user_id
        ).first()
        if not garment:
            raise HTTPException(404, "Garment not found")
        db.delete(garment)
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


# ── Smart Suggestions ─────────────────────────────────────────

def get_smart_suggestions(user_id: str) -> dict:
    """AI-powered purchase suggestions that maximise outfit combinations."""
    with get_db_context() as db:
        items = db.query(GarmentItemDB).filter(GarmentItemDB.user_id == user_id).all()
        existing_cats = [g.category for g in items]

        suggestions = sorted(
            SMART_SUGGESTIONS,
            key=lambda s: (s["category"] not in existing_cats, s["new_combinations"]),
            reverse=True,
        )

        cat_counts: dict[str, int] = {}
        for g in items:
            c = g.category
            cat_counts[c] = cat_counts.get(c, 0) + 1

        tops = cat_counts.get("top", 0)
        bottoms = cat_counts.get("bottom", 0)
        if tops > 4 and bottoms < 3:
            insight = f"You have {tops} tops but only {bottoms} bottoms — adding bottoms has the highest impact."
        elif len(items) == 0:
            insight = "Add some items to your wardrobe to unlock personalised suggestions."
        else:
            insight = "Adding a warm-toned accessory would unlock 5+ new outfit combinations."

        return {"suggestions": suggestions, "insight": insight}


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

    if llm_result and (llm_result.get("versatility_scores") or llm_result.get("occasion_coverage")):
        versatility_scores: dict = llm_result.get("versatility_scores") or {}
        gap_analysis: list = llm_result.get("gap_analysis") or []
        occasion_coverage: dict = llm_result.get("occasion_coverage") or {}
        distribution: dict = llm_result.get("distribution") or {}

        # Derive a 0–100 score from occasion coverage and versatility
        occ_vals = [v for v in occasion_coverage.values() if isinstance(v, (int, float))]
        occ_avg = sum(occ_vals) / len(occ_vals) if occ_vals else 0.5

        vers_vals = [v for v in versatility_scores.values() if isinstance(v, (int, float))]
        vers_avg = sum(vers_vals) / len(vers_vals) if vers_vals else 0.5

        # Colour cohesion & season balance from distribution if available
        colour_cohesion_raw = distribution.get("color_distribution", {})
        neutral_keywords = {"white", "black", "grey", "gray", "beige", "navy", "tan", "cream", "nude"}
        neutral_count = sum(v for k, v in colour_cohesion_raw.items() if any(n in k.lower() for n in neutral_keywords)) if colour_cohesion_raw else 0
        total = len(items)
        colour_cohesion = min(neutral_count / max(total, 1) + 0.3, 1.0)

        season_dist = distribution.get("season_distribution", {})
        season_balance = min(len(season_dist) / 4, 1.0) if season_dist else vers_avg * 0.8

        raw = (vers_avg * 0.30 + colour_cohesion * 0.30 + occ_avg * 0.20 + season_balance * 0.20) * 100
        score = round(min(raw, 100), 1)
        grade, grade_label = _grade(score)

        # Opportunities from gap analysis
        opps = []
        for gap in (gap_analysis or [])[:3]:
            if isinstance(gap, dict):
                opps.append({"type": gap.get("type", "category"), "label": gap.get("label") or gap.get("description", ""), "impact": gap.get("impact", "+5 pts")})
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
    wardrobe_cats = set(g.category for g in wardrobe_dicts if g.get("id") != garment_id) if wardrobe_dicts else set()
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


def get_smart_removal(user_id: str, profile: str = "balanced") -> dict:
    """Suggest garments to remove based on a declutter profile, using LLM smart-removal verdicts."""
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

        wardrobe_dicts = [garment_db_to_schema(g).model_dump() for g in items]

    # ── Pre-filter by rule-based risk score ───────────────────────────────────
    rule_candidates = []
    for g in items:
        risk_score = 0
        if g.times_worn == 0:
            risk_score += 40
        elif g.times_worn <= p["low_use_threshold"]:
            risk_score += 20
        if len(g.seasons or []) <= 1:
            risk_score += 15
        if g.confidence < _CONFIDENCE_CAPSULE_MIN:
            risk_score += 10
        if risk_score >= 20:
            rule_candidates.append((g, risk_score))

    rule_candidates.sort(key=lambda x: x[1], reverse=True)
    # Only call LLM for top 10 rule-flagged items to keep latency reasonable
    top_candidates = rule_candidates[:10]

    candidates = []
    for g, base_risk in top_candidates:
        cat = g.category
        llm_verdict = {}
        try:
            llm_verdict = _llm.smart_removal_verdict(
                garment_id=g.id,
                garments_dicts=wardrobe_dicts,
                user_goal=profile,
            )
        except Exception as exc:
            logger.warning("LLM smart-removal unavailable for %s: %s", g.id, exc)

        if llm_verdict and llm_verdict.get("verdict"):
            verdict_label = llm_verdict.get("verdict", "")
            regret_risk  = float(llm_verdict.get("regret_risk_score", base_risk / 100))
            reasons      = llm_verdict.get("reasons") or []
            outfit_count = int(llm_verdict.get("outfits_affected") or llm_verdict.get("outfit_count") or 0)
            removal_impact = (
                "safe" if outfit_count == 0
                else "low" if outfit_count <= 2
                else "medium" if outfit_count <= 5
                else "high"
            )
            risk_final = round(regret_risk * 100 if regret_risk <= 1 else regret_risk)
            logger.debug("LLM smart-removal %s: verdict=%s regret=%.2f", g.id, verdict_label, regret_risk)
        else:
            # Rule-based fallback for this garment
            reasons = []
            if g.times_worn == 0:
                reasons.append("Never worn")
            elif g.times_worn <= p["low_use_threshold"]:
                reasons.append(f"Worn only {g.times_worn} time(s)")
            if len(g.seasons or []) <= 1:
                reasons.append("Single season only")
            if g.confidence < _CONFIDENCE_CAPSULE_MIN:
                reasons.append("Low style match")
            outfit_count = random.randint(0, 6)
            removal_impact = "safe" if outfit_count == 0 else "low" if outfit_count <= 2 else "medium"
            risk_final = min(base_risk, 100)

        candidates.append({
            "garment": garment_db_to_schema(g).model_dump(),
            "risk_score": risk_final,
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
            f"removing them would bring your wardrobe to {max(len(items) - len(candidates), 0)} core pieces."
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

"""
Wardrobe service — business logic for garment management,
smart suggestions and closet audit.
Uses PostgreSQL database for persistence.
"""
import os
import re
import json
import random
import base64
import asyncio
from io import BytesIO
from typing import Optional, List, Dict

from fastapi import HTTPException
from models.schemas import (
    GarmentItem, GarmentAttributes, GarmentCategory,
    GarmentExtractionResult, ExtractionWarning,
)
from models.database import GarmentItem as GarmentItemDB
from db import get_db_context
from services import neo4j_service

# ── Gemini Vision setup ───────────────────────────────────────
try:
    # Load .env explicitly so GOOGLE_API_KEY is available at module init time
    # (pydantic-settings loads it later; os.getenv alone isn't enough here)
    from dotenv import load_dotenv as _load_dotenv
    _load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))

    from google import genai as _genai
    from PIL import Image as _PILImage
    _GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY", "")
    if _GOOGLE_API_KEY:
        _GEMINI_CLIENT = _genai.Client(api_key=_GOOGLE_API_KEY)
        _GEMINI_MODEL_NAME = "gemini-2.5-flash"
        _VISION_AVAILABLE = True
        print(f"✅ Gemini Vision ready (model: {_GEMINI_MODEL_NAME})")
    else:
        _VISION_AVAILABLE = False
        print("⚠️  GOOGLE_API_KEY not set — falling back to mock extraction")
except ImportError as _e:
    _VISION_AVAILABLE = False
    print(f"⚠️  Gemini Vision unavailable ({_e}) — using mock extraction")


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


# ─── Gemini Vision prompt ────────────────────────────────────

_GARMENT_EXTRACTION_PROMPT = """You are a professional fashion analyst. Analyze this clothing image and extract garment attributes.

Return ONLY a valid JSON object with EXACTLY this structure — no markdown, no extra text:

{
  "category": "<one of: top, bottom, dress, outerwear, shoes, accessory, swimwear, sportswear>",
  "subcategory": "<specific item type, e.g. 'white t-shirt', 'slim jeans', 'midi dress', 'leather sneakers'>",
  "color_primary": "<main color name in plain English, e.g. 'white', 'navy blue', 'olive green'>",
  "color_hex": "<best matching hex code, e.g. '#FFFFFF'>",
  "color_secondary": "<second color if present, else null>",
  "pattern": "<one of: solid, striped, checked, floral, geometric, animal_print, abstract, graphic, plain>",
  "material": "<fabric/material, e.g. 'cotton', 'denim', 'wool', 'leather', 'silk', 'linen', 'polyester'>",
  "formality": "<one of: casual, smart_casual, business, formal, athletic, loungewear>",
  "seasons": ["<list from: spring, summer, fall, winter — all that apply>"],
  "confidence": <0.0 to 1.0 — your confidence in this extraction>,
  "garments_detected": <integer — how many separate garments are visible in the image>
}

Be precise. If you cannot identify something with confidence, use your best guess and lower the confidence score."""


def _call_gemini_vision(image_bytes: bytes) -> dict:
    """Call Gemini Vision synchronously and return parsed JSON dict."""
    img = _PILImage.open(BytesIO(image_bytes))
    response = _GEMINI_CLIENT.models.generate_content(
        model=_GEMINI_MODEL_NAME,
        contents=[_GARMENT_EXTRACTION_PROMPT, img],
    )
    text = response.text.strip()
    # Strip markdown code fences if present
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    return json.loads(text)


def analyze_garment_image(
    image_bytes: bytes,
    mode: str = "auto",
    hint_category: Optional[str] = None,
) -> GarmentExtractionResult:
    """
    Analyze an image and extract garment attributes using Gemini Vision.
    Falls back to mock data if Gemini is unavailable.
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

    # ── Real Gemini extraction ──────────────────────────────
    if _VISION_AVAILABLE:
        try:
            raw = _call_gemini_vision(image_bytes)

            confidence = float(raw.get("confidence", 0.80))
            garments_detected = int(raw.get("garments_detected", 1))

            # Validate category
            raw_cat = raw.get("category", hint_category or "top").lower().replace(" ", "_")
            valid_cats = {c.value for c in GarmentCategory}
            if raw_cat not in valid_cats:
                raw_cat = hint_category or "top"

            # Multiple garments info
            if mode == "outfit" and garments_detected > 1:
                warnings.append(ExtractionWarning(
                    code="multiple_garments",
                    severity="info",
                    message=(
                        f"ℹ️ {garments_detected} garments found in this outfit photo. "
                        "Each piece will be extracted and added to your wardrobe as a separate item."
                    ),
                ))

            if confidence < 0.65:
                warnings.append(ExtractionWarning(
                    code="low_confidence",
                    severity="warning",
                    message=(
                        f"⚠️ Uncertain extraction ({int(confidence * 100)}% confidence). "
                        "Please check the detected category and colour below before saving."
                    ),
                ))

            seasons_raw = raw.get("seasons", ["spring", "summer", "fall", "winter"])
            valid_seasons = {"spring", "summer", "fall", "winter"}
            seasons = [s for s in seasons_raw if s in valid_seasons] or ["spring", "summer", "fall", "winter"]

            attrs = GarmentAttributes(
                category=GarmentCategory(raw_cat),
                subcategory=raw.get("subcategory"),
                color_primary=raw.get("color_primary"),
                color_hex=raw.get("color_hex"),
                color_secondary=raw.get("color_secondary"),
                pattern=raw.get("pattern"),
                material=raw.get("material"),
                formality=raw.get("formality"),
                seasons=seasons,
                confidence=confidence,
            )

            has_error = any(w.severity == "error" for w in warnings)
            return GarmentExtractionResult(
                attributes=attrs,
                warnings=warnings,
                auto_confirm=confidence >= 0.80 and not has_error,
                garments_detected=garments_detected,
                confidence=confidence,
            )

        except Exception as e:
            # Add warning and fall through to mock
            warnings.append(ExtractionWarning(
                code="extraction_failed",
                severity="warning",
                message=f"⚠️ AI extraction encountered an issue ({type(e).__name__}). "
                        "Using estimated values — please review and correct before saving.",
            ))

    # ── Fallback mock ───────────────────────────────────────
    confidence = round(random.uniform(0.60, 0.75), 2)
    cat = hint_category or random.choice(list(_CATEGORY_VARIANTS.keys()))
    variant = _CATEGORY_VARIANTS.get(cat, _CATEGORY_VARIANTS["top"])

    if confidence < 0.75:
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
        created_at=g.created_at,
    )


def add_garment(
    user_id: str,
    category: Optional[str] = None,
    image_bytes: Optional[bytes] = None,
    extra_attrs: Optional[Dict[str, Optional[str]]] = None,
) -> GarmentItem:
    """
    Add a garment to a user's wardrobe.
    If extra_attrs are provided (from a confirmed extraction), use them directly.
    Otherwise fall back to mock_analyze_garment.
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
    """Compute capsule cohesion score for a user's wardrobe."""
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

        total = len(items)
        categories = set(g.category for g in items)

        # Versatility: how many categories covered (6 max) → 0–1
        versatility = min(len(categories) / 6, 1.0)

        # Colour cohesion: ratio of neutrals (white/black/grey/beige/navy/tan) to total
        neutral_keywords = {"white", "black", "grey", "gray", "beige", "navy", "tan", "cream", "nude"}
        neutral_count = sum(
            1 for g in items
            if any(n in g.color_primary.lower() for n in neutral_keywords)
        )
        colour_cohesion = min(neutral_count / max(total, 1) + 0.3, 1.0)  # +0.3 base for any wardrobe

        # Occasion coverage: distinct formality values
        formality_vals = set(g.formality for g in items)
        occasion_coverage = min(len(formality_vals) / 4, 1.0)

        # Season balance: distinct seasons covered
        all_seasons: set = set()
        for g in items:
            all_seasons.update(g.seasons or [])
        season_balance = min(len(all_seasons) / 4, 1.0)

        raw = (versatility * 0.30 + colour_cohesion * 0.30 + occasion_coverage * 0.20 + season_balance * 0.20) * 100
        score = round(min(raw, 100), 1)
        grade, grade_label = _grade(score)

        # Opportunities
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
        total = len(wardrobe)

        # Versatility: seasons count + neutralness
        seasons_count = len(garment.seasons or [])
        neutral_keywords = {"white", "black", "grey", "gray", "beige", "navy", "tan"}
        is_neutral = any(n in garment.color_primary.lower() for n in neutral_keywords)
        versatility = min((seasons_count / 4 * 0.5 + (0.5 if is_neutral else 0.2)), 1.0)

        # Compatibility: how many other categories can pair with this
        cat_pairs = {
            "top": ["bottom", "outerwear", "shoes", "accessory"],
            "bottom": ["top", "shoes", "accessory", "outerwear"],
            "dress": ["shoes", "accessory", "outerwear"],
            "outerwear": ["top", "bottom", "dress", "shoes"],
            "shoes": ["top", "bottom", "dress"],
            "accessory": ["top", "bottom", "dress", "outerwear"],
        }
        compatible_cats = cat_pairs.get(garment.category, [])
        wardrobe_cats = set(g.category for g in wardrobe if g.id != garment_id)
        matched = sum(1 for c in compatible_cats if c in wardrobe_cats)
        compatibility = matched / max(len(compatible_cats), 1)

        # Estimated outfit count
        outfit_count = max(1, int(compatibility * total * versatility * 0.6))

        # Impact: removing this garment's loss
        impact_score = round((versatility * 0.4 + compatibility * 0.6) * 100, 1)

        seasons = garment.seasons or []
        current_seasons_ready = len(seasons)

        return {
            "garment_id": garment_id,
            "name": garment.subcategory or garment.category,
            "versatility_score": round(versatility * 100, 1),
            "compatibility_score": round(compatibility * 100, 1),
            "outfit_count": outfit_count,
            "impact_score": impact_score,
            "seasons": seasons,
            "season_count": current_seasons_ready,
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
    """Suggest garments to remove based on a declutter profile."""
    with get_db_context() as db:
        items = db.query(GarmentItemDB).filter(GarmentItemDB.user_id == user_id).all()

        p = REMOVAL_PROFILES.get(profile, REMOVAL_PROFILES["balanced"])
        candidates = []

        for g in items:
            risk_score = 0
            reasons = []

            if g.times_worn == 0:
                risk_score += 40
                reasons.append("Never worn")
            elif g.times_worn <= p["low_use_threshold"]:
                risk_score += 20
                reasons.append(f"Worn only {g.times_worn} time(s)")

            seasons = g.seasons or []
            if len(seasons) <= 1:
                risk_score += 15
                reasons.append("Single season only")

            # Low confidence
            if g.confidence < 0.6:
                risk_score += 10
                reasons.append("Low style match")

            if risk_score >= 20:
                cat = g.category
                outfit_count = random.randint(0, 6)
                removal_impact = "safe" if outfit_count == 0 else "low" if outfit_count <= 2 else "medium"
                candidates.append({
                    "garment": garment_db_to_schema(g).model_dump(),
                    "risk_score": min(risk_score, 100),
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

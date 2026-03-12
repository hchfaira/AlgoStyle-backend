"""
Wardrobe service — business logic for garment management,
smart suggestions and closet audit.
"""
import uuid
import random
from typing import Optional, List, Dict

from fastapi import HTTPException
from models.schemas import GarmentItem, GarmentAttributes, GarmentCategory
from services.store import wardrobes


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


def mock_analyze_garment(category: Optional[str] = None) -> GarmentAttributes:
    """Mock garment analysis — would call vision pipeline in production."""
    cat = GarmentCategory(category) if category else GarmentCategory.TOP
    return GarmentAttributes(
        category=cat,
        subcategory="t-shirt" if cat == GarmentCategory.TOP else None,
        color_primary="navy",
        color_hex="#1B2A4A",
        pattern="solid",
        material="cotton",
        formality="casual",
        seasons=["spring", "summer", "fall"],
        confidence=0.85,
    )


def add_garment(user_id: str, category: Optional[str] = None, image_bytes: Optional[bytes] = None) -> GarmentItem:
    """Add a garment to a user's wardrobe."""
    attributes = mock_analyze_garment(category)
    garment = GarmentItem(
        id=f"g_{uuid.uuid4().hex[:10]}",
        user_id=user_id,
        attributes=attributes,
    )
    wardrobes.setdefault(user_id, []).append(garment)
    return garment


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
    items = wardrobes.get(user_id, [])

    if category:
        items = [g for g in items if g.attributes.category.value == category]
    if color:
        items = [g for g in items if color.lower() in g.attributes.color_primary.lower()]
    if season:
        items = [g for g in items if season in g.attributes.seasons]
    if formality:
        items = [g for g in items if g.attributes.formality == formality]
    if pattern:
        items = [g for g in items if g.attributes.pattern == pattern]
    if is_favorite is not None:
        items = [g for g in items if g.is_favorite == is_favorite]
    if for_sale is not None:
        items = [g for g in items if g.for_sale == for_sale]
    if search:
        q = search.lower()
        items = [g for g in items if (
            q in g.attributes.color_primary.lower()
            or q in (g.attributes.subcategory or "").lower()
            or q in (g.attributes.material or "").lower()
            or q in g.attributes.category.value.lower()
        )]
    return items


def get_garment(user_id: str, garment_id: str) -> GarmentItem:
    """Get a specific garment."""
    items = wardrobes.get(user_id, [])
    for g in items:
        if g.id == garment_id:
            return g
    raise HTTPException(404, "Garment not found")


def update_garment(user_id: str, garment_id: str, updates: dict) -> GarmentItem:
    """Update garment properties."""
    items = wardrobes.get(user_id, [])
    for g in items:
        if g.id == garment_id:
            for k, v in updates.items():
                if hasattr(g, k):
                    setattr(g, k, v)
                elif hasattr(g.attributes, k):
                    setattr(g.attributes, k, v)
            return g
    raise HTTPException(404, "Garment not found")


def delete_garment(user_id: str, garment_id: str) -> dict:
    """Delete a garment from wardrobe."""
    items = wardrobes.get(user_id, [])
    wardrobes[user_id] = [g for g in items if g.id != garment_id]
    return {"status": "deleted", "id": garment_id}


def toggle_favorite(user_id: str, garment_id: str) -> dict:
    """Toggle favorite status on a garment."""
    items = wardrobes.get(user_id, [])
    for g in items:
        if g.id == garment_id:
            g.is_favorite = not g.is_favorite
            return {"id": garment_id, "is_favorite": g.is_favorite}
    raise HTTPException(404, "Garment not found")


def toggle_for_sale(user_id: str, garment_id: str) -> dict:
    """Toggle for-sale status on a garment."""
    items = wardrobes.get(user_id, [])
    for g in items:
        if g.id == garment_id:
            g.for_sale = not g.for_sale
            return {"id": garment_id, "for_sale": g.for_sale}
    raise HTTPException(404, "Garment not found")


def get_wardrobe_stats(user_id: str) -> dict:
    """Get wardrobe statistics."""
    items = wardrobes.get(user_id, [])
    categories: dict[str, int] = {}
    for g in items:
        cat = g.attributes.category.value
        categories[cat] = categories.get(cat, 0) + 1
    return {
        "total_items": len(items),
        "by_category": categories,
        "favorites": sum(1 for g in items if g.is_favorite),
        "for_sale": sum(1 for g in items if g.for_sale),
    }


# ── Smart Suggestions ─────────────────────────────────────────

def get_smart_suggestions(user_id: str) -> dict:
    """AI-powered purchase suggestions that maximise outfit combinations."""
    items = wardrobes.get(user_id, [])
    existing_cats = [g.attributes.category.value for g in items]

    suggestions = sorted(
        SMART_SUGGESTIONS,
        key=lambda s: (s["category"] not in existing_cats, s["new_combinations"]),
        reverse=True,
    )

    cat_counts: dict[str, int] = {}
    for g in items:
        c = g.attributes.category.value
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
    items = wardrobes.get(user_id, [])

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

        cat = g.attributes.category.value
        restyle = RESTYLE_IDEAS.get(cat, [])
        impact_msg = (
            "Removing this loses 0 outfit combinations (no impact)."
            if outfit_count == 0
            else f"Removing this loses {outfit_count} outfit combination{'s' if outfit_count != 1 else ''} (low impact)."
        )

        flagged.append({
            "garment": g.model_dump(),
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

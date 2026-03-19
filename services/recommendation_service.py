"""
Recommendation service — outfit generation via HybridOutfitRecommender.

Wires algoStyle's PostgreSQL wardrobe into LLM_project's
HybridOutfitRecommender (Layer 2 + 3 style/context scoring).
Falls back to a fast mock when the recommender is unavailable or the
wardrobe has too few items.
"""
import sys
import uuid
import random
import asyncio
import logging
from typing import List, Optional

from services.weather_service import fetch_weather as _fetch_weather

# ── Add LLM_project to Python path ──────────────────────────
# Safe to insert at index 0: by the time this module is loaded, the
# backend's `config` is already cached in sys.modules (main.py loads it
# first), so Python won't re-resolve it from LLM_project/config/.
_LLM_PATH = "/home/chfaira-hajar/work/repos/LLM_project"
if _LLM_PATH not in sys.path:
    sys.path.insert(0, _LLM_PATH)

# ── algoStyle schemas ────────────────────────────────────────
from models.schemas import (
    RecommendationConfig, RecommendationResponse, OutfitResult,
    OutfitScore, GarmentItem, GarmentAttributes, GarmentCategory,
)
from models.database import GarmentItem as GarmentItemDB
from db import get_db_context
from services.wardrobe_service import garment_db_to_schema

logger = logging.getLogger(__name__)

# ── Lazy-load LLM_project types ───────────────────────────────
# We temporarily swap sys.modules['config'] so that LLM_project's internal
# imports (src.core.logger → `from config import get_settings`) resolve
# to LLM_project/config/ instead of the backend's config.py.
_LLM_AVAILABLE = False
_HybridOutfitRecommender = None
_LLMGarment = None
_LLMGarmentAttributes = None
_ColorProfile = None
_PatternInfo = None
_MaterialProfile = None
_GarmentHistory = None
_FormalityLevel = None
_UserContext = None
_WeatherContext = None
_LLMOccasion = None

try:
    import importlib as _importlib

    # Save & temporarily replace backend `config` so LLM_project's
    # sub-modules find LLM's config package instead of backend config.py
    _saved_config = sys.modules.get("config")
    _llm_config = _importlib.import_module("config")  # loads LLM_project/config/ since it's first in path now
    # But wait — `config` might already be the backend one. Force reload from LLM path:
    import importlib.util as _ilu
    _llm_config_spec = _ilu.spec_from_file_location(
        "config",
        f"{_LLM_PATH}/config/__init__.py",
        submodule_search_locations=[f"{_LLM_PATH}/config"],
    )
    if _llm_config_spec:
        _llm_config_mod = _ilu.module_from_spec(_llm_config_spec)
        sys.modules["config"] = _llm_config_mod
        _llm_config_spec.loader.exec_module(_llm_config_mod)  # type: ignore[union-attr]

    from src.layer2_style.hybrid_recommender import HybridOutfitRecommender as _HOR
    from src.core.models import (
        Garment as _G,
        GarmentAttributes as _GA,
        ColorProfile as _CP,
        PatternInfo as _PI,
        MaterialProfile as _MP,
        GarmentHistory as _GH,
        FormalityLevel as _FL,
        UserContext as _UC,
        WeatherContext as _WC,
        Occasion as _OC,
    )

    # Restore backend config
    if _saved_config is not None:
        sys.modules["config"] = _saved_config
    elif "config" in sys.modules:
        del sys.modules["config"]

    _HybridOutfitRecommender = _HOR
    _LLMGarment = _G
    _LLMGarmentAttributes = _GA
    _ColorProfile = _CP
    _PatternInfo = _PI
    _MaterialProfile = _MP
    _GarmentHistory = _GH
    _FormalityLevel = _FL
    _UserContext = _UC
    _WeatherContext = _WC
    _LLMOccasion = _OC
    _LLM_AVAILABLE = True
    logger.info("✅ HybridOutfitRecommender loaded")
except Exception as _e:
    logger.warning(f"⚠️  HybridOutfitRecommender unavailable: {_e} — using mock fallback")
    # Ensure backend config is restored on failure too
    try:
        if "_saved_config" in dir() and _saved_config is not None:
            sys.modules["config"] = _saved_config
    except Exception:
        pass

# ── Lazy recommender singleton ────────────────────────────────
_recommender: Optional[object] = None


def _get_recommender():
    global _recommender
    if _recommender is None and _HybridOutfitRecommender is not None:
        _recommender = _HybridOutfitRecommender()
    return _recommender


# ─── Formality mapping: algoStyle str → LLM FormalityLevel ───

_FORMALITY_MAP = {
    "very_casual":      "very_casual",
    "casual":           "casual",
    "smart_casual":     "smart_casual",
    "business_casual":  "business_casual",
    "business":         "business",
    "formal":           "formal",
    "black_tie":        "black_tie",
}


def _to_llm_garment(item: GarmentItem):
    """Convert an algoStyle GarmentItem schema → LLM_project Garment."""
    attrs = item.attributes
    formality_str = _FORMALITY_MAP.get(attrs.formality or "casual", "casual")

    color = _ColorProfile(
        primary=attrs.color_primary or "unknown",
        secondary=getattr(attrs, "color_secondary", None),
        hex_codes=[attrs.color_hex] if attrs.color_hex else [],
    )

    pattern = _PatternInfo(type=attrs.pattern or "solid")

    material = _MaterialProfile(primary=attrs.material) if attrs.material else None

    llm_attrs = _LLMGarmentAttributes(
        category=attrs.category,  # same enum values
        subcategory=attrs.subcategory,
        color=color,
        pattern=pattern,
        material=material,
        formality_level=_FormalityLevel(formality_str),
    )

    return _LLMGarment(
        id=item.id,
        image_url=item.image_url,
        attributes=llm_attrs,
        history=_GarmentHistory(),
    )


def _load_user_wardrobe(user_id: str) -> List[GarmentItem]:
    """Load all wardrobe items for a user from PostgreSQL."""
    with get_db_context() as db:
        rows = (
            db.query(GarmentItemDB)
            .filter(GarmentItemDB.user_id == user_id)
            .all()
        )
        return [garment_db_to_schema(r) for r in rows]


def _extract_score(bd: dict, key: str, fallback: float) -> float:
    """Extract a numeric score from a (possibly nested) breakdown dict."""
    val = bd.get(key, fallback)
    if isinstance(val, dict):
        return float(val.get("score", fallback))
    try:
        return float(val)
    except (TypeError, ValueError):
        return fallback


def _map_ranked_to_response(
    ranked_outfits: list,
    source_items: List[GarmentItem],
    config: RecommendationConfig,
) -> RecommendationResponse:
    """Map List[RankedOutfit] → RecommendationResponse."""
    item_map = {g.id: g for g in source_items}
    results: List[OutfitResult] = []

    for i, r in enumerate(ranked_outfits):
        # Resolve garments — skip any that aren't in our DB
        garments = [item_map[g.id] for g in r.garments if g.id in item_map]
        if not garments:
            continue

        sc = r.score
        style_bd = sc.style_breakdown or {}
        ctx_bd = sc.context_breakdown or {}

        outfit_score = OutfitScore(
            overall=round(float(sc.combined_score), 3),
            color_harmony=round(_extract_score(style_bd, "color_harmony", sc.style_score * 0.9), 3),
            formality_match=round(_extract_score(style_bd, "formality", sc.style_score), 3),
            occasion_fit=round(_extract_score(ctx_bd, "occasion", sc.context_score), 3),
            pattern_mixing=round(_extract_score(style_bd, "pattern_mixing", 0.85), 3),
            proportion=round(_extract_score(style_bd, "proportions", sc.style_score * 0.95), 3),
            season_fit=round(_extract_score(ctx_bd, "season", sc.context_score * 0.9), 3),
            creativity=round(_extract_score(style_bd, "seven_point_rule", 0.5), 3),
        )

        # Build a human-readable name: grade + dominant color + category
        grade = sc.grade or "B"
        dominant = garments[0].attributes.color_primary.capitalize() if garments else "Classic"
        cat_label = garments[0].attributes.subcategory or garments[0].attributes.category.value
        name = f"{dominant} {cat_label.title()} · Grade {grade}"

        # Use strengths as brief explanation, improvements as detailed
        strengths = sc.strengths or []
        improvements = sc.improvements or []
        brief = strengths[0] if strengths else "AI-curated outfit from your wardrobe."
        detailed_parts = []
        if strengths:
            detailed_parts.append("✦ STRENGTHS\n" + "\n".join(f"• {s}" for s in strengths))
        if improvements:
            detailed_parts.append("✦ TO ELEVATE\n" + "\n".join(f"• {imp}" for imp in improvements))
        detailed = "\n\n".join(detailed_parts) if detailed_parts else brief

        results.append(OutfitResult(
            id=f"outfit_{uuid.uuid4().hex[:8]}",
            rank=i + 1,
            name=name,
            grade=grade,
            garments=garments,
            score=outfit_score,
            explanation_brief=brief,
            explanation_detailed=detailed,
        ))

    return RecommendationResponse(outfits=results, total_combinations=len(results))

# ─── Mock fallback ────────────────────────────────────────────

_MOCK_NAMES = [
    "Smart Casual Navy Ensemble",
    "Effortless Weekend Look",
    "Modern Minimalist Set",
    "Urban Chic Collection",
    "Classic Elegance Combo",
    "Bold Color Story",
    "Relaxed Summer Vibes",
    "Business Power Look",
    "Date Night Perfection",
    "Bohemian Sunset Mix",
]

_MOCK_BRIEF = [
    "A harmonious blend of neutral tones creates a polished yet relaxed silhouette.",
    "Clean lines and solid colors deliver effortless sophistication for any occasion.",
    "The contrast between structured outerwear and relaxed bottoms balances formality.",
    "Complementary colors and matching formality levels make this combination seamless.",
    "A modern take on classic proportions with a pop of color for visual interest.",
]


def _mock_garments() -> List[GarmentItem]:
    return [
        GarmentItem(
            id=f"g_{uuid.uuid4().hex[:10]}",
            user_id="demo",
            attributes=GarmentAttributes(
                category=GarmentCategory.TOP,
                subcategory="oxford shirt",
                color_primary="navy", color_hex="#1B2A4A",
                pattern="solid", material="cotton", formality="smart_casual", confidence=0.92,
            ),
        ),
        GarmentItem(
            id=f"g_{uuid.uuid4().hex[:10]}",
            user_id="demo",
            attributes=GarmentAttributes(
                category=GarmentCategory.BOTTOM,
                subcategory="chino",
                color_primary="beige", color_hex="#D4C5A9",
                pattern="solid", material="cotton", formality="smart_casual", confidence=0.88,
            ),
        ),
        GarmentItem(
            id=f"g_{uuid.uuid4().hex[:10]}",
            user_id="demo",
            attributes=GarmentAttributes(
                category=GarmentCategory.SHOES,
                subcategory="leather loafers",
                color_primary="brown", color_hex="#8B6F47",
                pattern="solid", material="leather", formality="smart_casual", confidence=0.90,
            ),
        ),
    ]


def _mock_score() -> OutfitScore:
    return OutfitScore(
        overall=round(random.uniform(0.72, 0.96), 2),
        color_harmony=round(random.uniform(0.70, 0.98), 2),
        formality_match=round(random.uniform(0.75, 0.95), 2),
        occasion_fit=round(random.uniform(0.65, 0.95), 2),
        pattern_mixing=round(random.uniform(0.80, 1.0), 2),
        proportion=round(random.uniform(0.70, 0.95), 2),
        season_fit=round(random.uniform(0.60, 0.95), 2),
        creativity=round(random.uniform(0.40, 0.85), 2),
    )


def _mock_fallback(config: RecommendationConfig) -> RecommendationResponse:
    k = min(config.top_k, len(_MOCK_NAMES))
    results = [
        OutfitResult(
            id=f"outfit_{uuid.uuid4().hex[:8]}",
            rank=i + 1,
            name=_MOCK_NAMES[i],
            garments=_mock_garments(),
            score=_mock_score(),
            explanation_brief=_MOCK_BRIEF[i % len(_MOCK_BRIEF)],
            explanation_detailed="Add more items to your wardrobe to unlock real AI recommendations.",
        )
        for i in range(k)
    ]
    results.sort(key=lambda r: r.score.overall, reverse=True)
    for i, r in enumerate(results):
        r.rank = i + 1
    return RecommendationResponse(outfits=results, total_combinations=k)


# ─── Public API ──────────────────────────────────────────────

def get_recommendations(
    config: RecommendationConfig,
    user_id: Optional[str] = None,
) -> RecommendationResponse:
    """
    Generate outfit recommendations.

    1. Load user's real wardrobe from PostgreSQL.
    2. Convert each GarmentItem → LLM Garment.
    3. Run HybridOutfitRecommender.recommend() (async via asyncio.run).
    4. Map RankedOutfit → OutfitResult + RecommendationResponse.

    Falls back to mock data when:
    - user_id is missing
    - LLM_project is unavailable
    - wardrobe has fewer than 3 garments
    """
    if not user_id or not _LLM_AVAILABLE:
        return _mock_fallback(config)

    # Load wardrobe
    try:
        wardrobe_items = _load_user_wardrobe(user_id)
    except Exception as e:
        logger.warning(f"Could not load wardrobe for {user_id}: {e}")
        return _mock_fallback(config)

    # Filter to requested garment_ids if specified
    if config.garment_ids:
        id_set = set(config.garment_ids)
        wardrobe_items = [g for g in wardrobe_items if g.id in id_set]

    if len(wardrobe_items) < 3:
        logger.info(f"Wardrobe too small ({len(wardrobe_items)} items) — using mock fallback")
        return _mock_fallback(config)

    # Convert to LLM garments
    try:
        llm_garments = [_to_llm_garment(item) for item in wardrobe_items]
    except Exception as e:
        logger.warning(f"Garment conversion failed: {e}")
        return _mock_fallback(config)

    # Build UserContext
    try:
        occasion_val = config.occasion.value if config.occasion else "casual"
        try:
            llm_occasion = _LLMOccasion(occasion_val)
        except Exception:
            llm_occasion = _LLMOccasion("casual")

        # Build WeatherContext if we have temperature data
        weather_ctx = None
        if config.weather_temp is not None and _WeatherContext is not None:
            weather_ctx = _WeatherContext(
                temperature_celsius=config.weather_temp,
                condition=config.weather_condition or "clear",
            )

        user_ctx = _UserContext(
            occasion=llm_occasion,
            weather=weather_ctx,
            time_of_day=config.time_of_day,
        )
    except Exception as e:
        logger.warning(f"UserContext build failed: {e}")
        try:
            user_ctx = _UserContext()
        except Exception:
            return _mock_fallback(config)

    # Run recommender (sync version uses asyncio.run — only call from non-async contexts)
    try:
        recommender = _get_recommender()
        if recommender is None:
            return _mock_fallback(config)

        ranked = asyncio.run(
            recommender.recommend(
                wardrobe=llm_garments,
                user_context=user_ctx,
                top_k=config.top_k,
            )
        )
    except Exception as e:
        logger.warning(f"HybridOutfitRecommender failed: {e}", exc_info=True)
        return _mock_fallback(config)

    if not ranked:
        return _mock_fallback(config)

    return _map_ranked_to_response(ranked, wardrobe_items, config)


async def get_recommendations_async(
    config: RecommendationConfig,
    user_id: Optional[str] = None,
) -> RecommendationResponse:
    """
    Async version of get_recommendations — use this from FastAPI route handlers.
    Directly awaits the recommender coroutine instead of using asyncio.run().
    """
    if not user_id or not _LLM_AVAILABLE:
        return _mock_fallback(config)

    try:
        wardrobe_items = _load_user_wardrobe(user_id)
    except Exception as e:
        logger.warning(f"Could not load wardrobe for {user_id}: {e}")
        return _mock_fallback(config)

    if config.garment_ids:
        id_set = set(config.garment_ids)
        wardrobe_items = [g for g in wardrobe_items if g.id in id_set]

    if len(wardrobe_items) < 3:
        logger.info(f"Wardrobe too small ({len(wardrobe_items)} items) — using mock fallback")
        return _mock_fallback(config)

    try:
        llm_garments = [_to_llm_garment(item) for item in wardrobe_items]
    except Exception as e:
        logger.warning(f"Garment conversion failed: {e}")
        return _mock_fallback(config)

    try:
        occasion_val = config.occasion.value if config.occasion else "casual"
        try:
            llm_occasion = _LLMOccasion(occasion_val)
        except Exception:
            llm_occasion = _LLMOccasion("casual")

        # ── Real weather injection ────────────────────────────
        # If the caller already provided weather_temp, use it.
        # Otherwise, fetch live weather from wttr.in (free, no API key).
        weather_ctx = None
        if _WeatherContext is not None:
            if config.weather_temp is not None:
                weather_temp = config.weather_temp
                weather_cond = config.weather_condition or "clear"
            else:
                try:
                    w = await _fetch_weather("auto")
                    weather_temp = w["temperature_celsius"]
                    weather_cond = w["condition"]
                    logger.info(
                        f"Auto-injected weather: {weather_temp}°C, {weather_cond}"
                    )
                except Exception as we:
                    logger.warning(f"Weather auto-fetch failed: {we}")
                    weather_temp = None
                    weather_cond = "clear"

            if weather_temp is not None:
                weather_ctx = _WeatherContext(
                    temperature_celsius=weather_temp,
                    condition=weather_cond,
                )

        user_ctx = _UserContext(
            occasion=llm_occasion,
            weather=weather_ctx,
            time_of_day=config.time_of_day,
        )
    except Exception as e:
        logger.warning(f"UserContext build failed: {e}")
        try:
            user_ctx = _UserContext()
        except Exception:
            return _mock_fallback(config)

    try:
        recommender = _get_recommender()
        if recommender is None:
            return _mock_fallback(config)

        # Await directly — no asyncio.run() needed inside an async context
        ranked = await recommender.recommend(
            wardrobe=llm_garments,
            user_context=user_ctx,
            top_k=config.top_k,
        )
    except Exception as e:
        logger.warning(f"HybridOutfitRecommender failed: {e}", exc_info=True)
        return _mock_fallback(config)

    if not ranked:
        return _mock_fallback(config)

    return _map_ranked_to_response(ranked, wardrobe_items, config)


# ── Backward-compat alias ─────────────────────────────────────
def generate_outfits(config: RecommendationConfig) -> List[OutfitResult]:
    return get_recommendations(config).outfits

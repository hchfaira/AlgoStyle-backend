"""
Recommendation service — outfit generation via LLM_project HTTP API.

Fetches the user's wardrobe images from PostgreSQL, sends them to the
LLM_project pipeline endpoint (/api/v1/pipeline/recommend), and maps
the ranked OutfitRecommendation list back to AlgoStyle's schema.

Falls back to mock data when:
  - user_id is missing
  - LLM_project API is unreachable
  - wardrobe has fewer than 3 garments with image URLs
"""
import uuid
import random
import base64
import logging
from typing import List, Optional

import httpx

from services.weather_service import fetch_weather as _fetch_weather
from services import llm_client as _llm

from models.schemas import (
    RecommendationConfig, RecommendationResponse, OutfitResult,
    OutfitScore, GarmentItem, GarmentAttributes, GarmentCategory,
)
from models.database import GarmentItem as GarmentItemDB
from db import get_db_context
from services.wardrobe_service import garment_db_to_schema

logger = logging.getLogger(__name__)


# ─── Wardrobe loader ──────────────────────────────────────────

def _load_user_wardrobe(user_id: str) -> List[GarmentItem]:
    """Load all wardrobe items for a user from PostgreSQL."""
    with get_db_context() as db:
        rows = (
            db.query(GarmentItemDB)
            .filter(GarmentItemDB.user_id == user_id)
            .all()
        )
        return [garment_db_to_schema(r) for r in rows]


def _items_to_b64s(items: List[GarmentItem]) -> List[str]:
    """
    Extract base64 payloads from GarmentItem.image_url.

    Handles three formats:
      - data:<mime>;base64,<b64>  →  strip the header, return raw b64
      - http(s)://…               →  download and encode as base64
      - raw base64 string         →  return as-is
    """
    result = []
    for item in items:
        url = item.image_url or ""
        if not url:
            continue
        if url.startswith("data:") and "," in url:
            result.append(url.split(",", 1)[1])
        elif url.startswith("http://") or url.startswith("https://"):
            try:
                resp = httpx.get(url, timeout=10.0, follow_redirects=True)
                resp.raise_for_status()
                result.append(base64.b64encode(resp.content).decode())
            except Exception as e:
                logger.debug(f"Could not download garment image {url}: {e}")
        elif url:  # raw base64 already
            result.append(url)
    return result


# ─── Allowed occasion enum values for the pipeline ───────────

_ALLOWED_OCCASIONS = {
    "casual", "business", "formal", "sport", "evening",
    "beach", "date", "daily_wear", "work", "weekend",
    "travel", "evening_event",
}


# ─── Score helpers ────────────────────────────────────────────

def _grade_label(score: float) -> str:
    if score >= 0.93: return "S"
    if score >= 0.86: return "A"
    if score >= 0.78: return "B"
    if score >= 0.68: return "C"
    return "D"


# ─── Pipeline response mapper ─────────────────────────────────

def _map_pipeline_to_response(
    pipeline: dict,
    wardrobe_items: List[GarmentItem],
    config: RecommendationConfig,
) -> RecommendationResponse:
    """Map /api/v1/pipeline/recommend response → RecommendationResponse."""
    recs = pipeline.get("recommendations") or []
    item_map = {g.id: g for g in wardrobe_items}
    results: List[OutfitResult] = []

    for rec in recs:
        rank = rec.get("rank", len(results) + 1)
        overall = float(rec.get("overall_score", 0.80))
        breakdown = rec.get("score_breakdown") or {}
        grade = _grade_label(overall)

        outfit_score = OutfitScore(
            overall=round(overall, 3),
            color_harmony=round(float(breakdown.get("color_harmony", breakdown.get("season_color", overall * 0.95))), 3),
            formality_match=round(float(breakdown.get("formality", breakdown.get("occasion", overall * 0.90))), 3),
            occasion_fit=round(float(breakdown.get("occasion", overall * 0.90)), 3),
            pattern_mixing=round(float(breakdown.get("pattern_mixing", breakdown.get("pattern", 0.85))), 3),
            proportion=round(float(breakdown.get("proportion", overall * 0.92)), 3),
            season_fit=round(float(breakdown.get("season", overall * 0.88)), 3),
            creativity=round(float(breakdown.get("creativity", breakdown.get("seven_point_rule", 0.60))), 3),
        )

        garments: List[GarmentItem] = []
        for g in (rec.get("garments") or []):
            gid = g.get("id", "")
            if gid in item_map:
                garments.append(item_map[gid])
            else:
                garments.append(GarmentItem(
                    id=gid or f"g_{uuid.uuid4().hex[:8]}",
                    user_id="unknown",
                    attributes=GarmentAttributes(
                        category=GarmentCategory(g.get("category", "top")),
                        subcategory=g.get("subcategory"),
                        color_primary=g.get("color_primary", ""),
                        formality=g.get("formality"),
                        confidence=float(g.get("confidence", 0.80)),
                    ),
                ))

        explanation = rec.get("explanation") or "AI-curated outfit from your wardrobe."
        name = rec.get("name") or f"Outfit #{rank} · Grade {grade}"

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
    Generate outfit recommendations via LLM_project pipeline (HTTP).

    1. Load user wardrobe from PostgreSQL.
    2. Extract base64 image payloads.
    3. POST to /api/v1/pipeline/recommend on port 8001.
    4. Map pipeline response → RecommendationResponse.

    Falls back to mock data when:
    - user_id is missing
    - LLM_project is unreachable
    - wardrobe has fewer than 3 images
    """
    if not user_id:
        return _mock_fallback(config)

    try:
        wardrobe_items = _load_user_wardrobe(user_id)
    except Exception as e:
        logger.warning(f"Could not load wardrobe for {user_id}: {e}")
        return _mock_fallback(config)

    if config.garment_ids:
        id_set = set(config.garment_ids)
        wardrobe_items = [g for g in wardrobe_items if g.id in id_set]

    b64s = _items_to_b64s(wardrobe_items)
    min_required = 1 if config.garment_ids else 3
    if len(b64s) < min_required:
        logger.info(f"Not enough wardrobe images ({len(b64s)}) — using mock fallback")
        return _mock_fallback(config)

    # Build pipeline context
    occasion_val = None
    if config.occasion:
        raw = config.occasion.value if hasattr(config.occasion, "value") else str(config.occasion)
        if raw in _ALLOWED_OCCASIONS:
            occasion_val = raw

    context: dict = {}
    if occasion_val:
        context["occasion"] = occasion_val
    if config.weather_temp is not None:
        context["weather"] = {
            "temperature_celsius": config.weather_temp,
            "condition": config.weather_condition or "clear",
        }

    try:
        pipeline = _llm.run_pipeline(
            wardrobe_image_b64s=b64s,
            context=context,
            top_k=config.top_k,
        )
    except Exception as e:
        logger.warning(f"Pipeline call failed: {e}")
        return _mock_fallback(config)

    if not pipeline or not pipeline.get("recommendations"):
        return _mock_fallback(config)

    return _map_pipeline_to_response(pipeline, wardrobe_items, config)


async def get_recommendations_async(
    config: RecommendationConfig,
    user_id: Optional[str] = None,
) -> RecommendationResponse:
    """
    Async version — use from FastAPI route handlers.
    Also auto-injects live weather when weather_temp is not provided.
    """
    if not user_id:
        return _mock_fallback(config)

    try:
        wardrobe_items = _load_user_wardrobe(user_id)
    except Exception as e:
        logger.warning(f"Could not load wardrobe for {user_id}: {e}")
        return _mock_fallback(config)

    if config.garment_ids:
        id_set = set(config.garment_ids)
        wardrobe_items = [g for g in wardrobe_items if g.id in id_set]

    b64s = _items_to_b64s(wardrobe_items)
    min_required = 1 if config.garment_ids else 3
    if len(b64s) < min_required:
        logger.info(f"Not enough wardrobe images ({len(b64s)}) — using mock fallback")
        return _mock_fallback(config)

    # Build pipeline context
    occasion_val = None
    if config.occasion:
        raw = config.occasion.value if hasattr(config.occasion, "value") else str(config.occasion)
        if raw in _ALLOWED_OCCASIONS:
            occasion_val = raw

    weather_temp = config.weather_temp
    weather_cond = config.weather_condition or "clear"

    # Auto-inject weather if not provided
    if weather_temp is None:
        try:
            w = await _fetch_weather("auto")
            weather_temp = w["temperature_celsius"]
            weather_cond = w["condition"]
            logger.info(f"Auto-injected weather: {weather_temp}°C, {weather_cond}")
        except Exception as we:
            logger.warning(f"Weather auto-fetch failed: {we}")

    context: dict = {}
    if occasion_val:
        context["occasion"] = occasion_val
    if weather_temp is not None:
        context["weather"] = {
            "temperature_celsius": weather_temp,
            "condition": weather_cond,
        }

    try:
        pipeline = _llm.run_pipeline(
            wardrobe_image_b64s=b64s,
            context=context,
            top_k=config.top_k,
        )
    except Exception as e:
        logger.warning(f"Pipeline call failed: {e}")
        return _mock_fallback(config)

    if not pipeline or not pipeline.get("recommendations"):
        return _mock_fallback(config)

    return _map_pipeline_to_response(pipeline, wardrobe_items, config)


# ── Backward-compat alias ─────────────────────────────────────
def generate_outfits(config: RecommendationConfig) -> List[OutfitResult]:
    return get_recommendations(config).outfits

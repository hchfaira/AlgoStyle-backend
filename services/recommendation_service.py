"""
Recommendation service — outfit generation via LLM_project HTTP API.

Fetches the user's wardrobe images from PostgreSQL, sends them to the
LLM_project pipeline endpoint (/api/v1/pipeline/recommend), and maps
the ranked OutfitRecommendation list back to AlgoStyle's schema.

─── Three-layer optimisation ────────────────────────────────────────────────
  1. Index-first / no-image payload
     Garments that have vision_features stored skip raw-image transmission.
     Only their pre-computed feature dict + llm_attributes are forwarded.
     Legacy garments (no vision_features) still send the base64 image.
     Cuts per-request payload by ~99% for well-populated wardrobes.

  2. Vision cache at save time
     add_garment() already calls LLM_project Layer 1 at save time and
     stores the result in vision_features.  This function reads that cache;
     it never recomputes the vision analysis.

  3. Context-aware pre-filter
     Before any network call, the wardrobe is trimmed to only garments that
     are plausible for the requested occasion + current weather:
       • formality mismatch  → removed
       • season mismatch     → removed
     This reduces the wardrobe sent to LLM_project from 50+ to 10-20 items.

Falls back to mock data when:
  - user_id is missing
  - LLM_project API is unreachable
  - wardrobe has fewer than 3 garments after pre-filtering
"""
import uuid
import random
import base64
import logging
import time
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


# ─── Optimisation 3: Context-aware pre-filter ─────────────────────────────────

# Maps occasion names to the formality levels that are compatible.
_OCCASION_FORMALITY: dict = {
    "casual":       {"casual", "smart_casual"},
    "weekend":      {"casual", "smart_casual"},
    "sport":        {"casual", "sport"},
    "beach":        {"casual"},
    "daily_wear":   {"casual", "smart_casual"},
    "travel":       {"casual", "smart_casual"},
    "date":         {"casual", "smart_casual", "business_casual"},
    "work":         {"smart_casual", "business_casual", "business"},
    "business":     {"business_casual", "business", "formal"},
    "interview":    {"business", "formal"},
    "evening":      {"smart_casual", "business_casual", "formal"},
    "evening_event":{"smart_casual", "formal"},
    "formal":       {"formal"},
    "wedding":      {"formal"},
}

# Maps temperature ranges to compatible seasons.
def _temp_to_seasons(temp_c: float) -> set:
    if temp_c >= 22:  return {"summer", "spring"}
    if temp_c >= 12:  return {"spring", "fall"}
    return {"fall", "winter"}


def _prefilter_wardrobe(
    items: List[GarmentItem],
    occasion: Optional[str],
    weather_temp: Optional[float],
) -> List[GarmentItem]:
    """
    Optimisation 3 — trim the wardrobe to items that are plausible for the
    requested occasion + current weather.

    Rules:
      • Formality: only keep items whose formality level is compatible with
        the occasion (e.g. "casual" removes suits and ball gowns).
      • Season: only keep items tagged for the current temperature range.

    If fewer than 6 items remain after filtering we return the original list
    so the user always gets meaningful results (safety net).
    """
    if not items:
        return items

    allowed_formalities = _OCCASION_FORMALITY.get(occasion or "", set()) if occasion else set()
    allowed_seasons = _temp_to_seasons(weather_temp) if weather_temp is not None else set()

    logger.info(
        "Pre-filter config: occasion=%s, formalities=%s, temp=%s, seasons=%s, items=%d",
        occasion, allowed_formalities, weather_temp, allowed_seasons, len(items),
    )

    def _passes(g: GarmentItem) -> bool:
        # Formality gate (only applies when we know the target occasion)
        if allowed_formalities:
            formality = (g.attributes.formality or "casual").lower()
            if formality not in allowed_formalities:
                logger.debug("REJECT %s: formality %s not in %s", g.id, formality, allowed_formalities)
                return False
        # Season gate (only applies when we know the temperature)
        if allowed_seasons:
            item_seasons = {s.lower() for s in (g.attributes.seasons or [])}
            if item_seasons and not item_seasons.intersection(allowed_seasons):
                logger.debug("REJECT %s: seasons %s no overlap %s", g.id, item_seasons, allowed_seasons)
                return False
        return True

    filtered = [g for g in items if _passes(g)]

    # Safety net: never return fewer than 6 items
    if len(filtered) < 6:
        logger.info(
            "Pre-filter too aggressive (%d/%d passed) — using full wardrobe",
            len(filtered), len(items),
        )
        return items

    logger.info(
        "Pre-filter kept %d/%d garments (occasion=%s, temp=%s°C)",
        len(filtered), len(items), occasion, weather_temp,
    )
    return filtered


# ─── Optimisation 1+2: Feature-first payload builder ─────────────────────────

def _garment_to_b64(g: GarmentItem) -> Optional[str]:
    """
    Extract a base64 image string from a GarmentItem.
    Returns None if the image cannot be resolved.
    """
    url = g.image_url or ""
    if not url:
        return None
    if url.startswith("data:") and "," in url:
        return url.split(",", 1)[1]
    elif url.startswith("http://") or url.startswith("https://"):
        try:
            resp = httpx.get(url, timeout=10.0, follow_redirects=True)
            resp.raise_for_status()
            return base64.b64encode(resp.content).decode()
        except Exception as e:
            logger.debug("Could not download garment image %s: %s", url, e)
            return None
    elif url:
        return url  # raw base64 already
    return None


def _build_pipeline_payload(
    items: List[GarmentItem],
    context: dict,
    top_k: int,
    enable_explanation: bool = True,
    explanation_detail: str = "standard",
) -> tuple[dict, List[GarmentItem], list]:
    """
    Build the payload for /api/v1/pipeline/recommend.

    Two-track strategy:
      • FAST: Garments that have known attributes (category, color, etc.)
              are sent as pre_analyzed_garments — zero ML inference.
      • SLOW: Garments with no attributes fall back to base64 image
              extraction (legacy path).

    Returns:
      payload        — the JSON body for the pipeline endpoint
      sent_items     — ordered list of GarmentItems that were included
      skipped_items  — GarmentItems that had no resolvable data
    """
    pre_analyzed: list[dict] = []
    wardrobe_images: list[dict] = []
    sent_items: List[GarmentItem] = []
    skipped_items: List[GarmentItem] = []

    for g in items:
        # Fast path: garment has enough attributes to skip ML extraction
        has_attrs = (
            g.attributes
            and g.attributes.category
            and g.attributes.color_primary
        )
        if has_attrs:
            entry = {
                "id": g.id,
                "category": g.attributes.category.value,
                "subcategory": g.attributes.subcategory,
                "color_primary": g.attributes.color_primary or "unknown",
                "color_hex": g.attributes.color_hex,
                "pattern": g.attributes.pattern or "solid",
                "material": g.attributes.material,
                "formality": g.attributes.formality or "casual",
                "seasons": g.attributes.seasons or [],
                "confidence": g.attributes.confidence or 0.85,
            }
            pre_analyzed.append(entry)
            sent_items.append(g)
        else:
            # Slow fallback: send raw image
            b64 = None
            if g.vision_features and g.vision_features.get("image_b64"):
                b64 = g.vision_features["image_b64"]
            else:
                b64 = _garment_to_b64(g)

            if b64:
                cat_hint = g.attributes.category.value if g.attributes and g.attributes.category else None
                entry_img: dict = {"image_b64": b64}
                if cat_hint:
                    entry_img["extract_category"] = cat_hint
                wardrobe_images.append(entry_img)
                sent_items.append(g)
            else:
                logger.debug("Skipping garment %s — no image or attributes", g.id)
                skipped_items.append(g)

    # Build context in the format LLM_project expects
    llm_context: dict = {}
    if context.get("occasion"):
        llm_context["occasion"] = context["occasion"]
    weather = context.get("weather")
    if weather:
        llm_context["weather"] = weather

    payload: dict = {
        "top_k": top_k,
        "enable_explanation": enable_explanation,
        "explanation_detail": explanation_detail,
        "enable_visualization": False,
        "enable_tryon": False,
    }
    if pre_analyzed:
        payload["pre_analyzed_garments"] = pre_analyzed
    if wardrobe_images:
        payload["wardrobe_images"] = wardrobe_images
    if llm_context:
        payload["context"] = llm_context

    logger.info(
        "Pipeline payload: %d pre-analyzed (fast), %d images (slow), %d skipped",
        len(pre_analyzed), len(wardrobe_images), len(skipped_items),
    )
    return payload, sent_items, skipped_items


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
    """
    Map /api/v1/pipeline/recommend response → RecommendationResponse.

    LLM_project generates its own UUIDs for garments (from image extraction),
    so we can't use IDs to match back.  Instead we build a lookup by
    (category, color_primary) from sent_items and use it as a best-effort
    match.  When ambiguous we fall back to the GarmentOut data from the
    pipeline response.
    """
    recs = pipeline.get("recommendations") or []

    # Build lookup: (category_lower, color_lower) → GarmentItem
    # Keyed first by id for future compatibility, then by (cat, color)
    id_map: dict = {g.id: g for g in wardrobe_items}
    cat_color_map: dict = {}
    for g in wardrobe_items:
        cat = (g.attributes.category.value if g.attributes and g.attributes.category else "").lower()
        col = (g.attributes.color_primary or "").lower()
        key = (cat, col)
        if key not in cat_color_map:   # first match wins (earlier items have priority)
            cat_color_map[key] = g

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
            cat  = (g.get("category") or "").lower()
            col  = (g.get("color_primary") or "").lower()

            # Try exact id match first (forward-compat), then cat+color
            matched = id_map.get(gid) or cat_color_map.get((cat, col))

            if matched:
                garments.append(matched)
            else:
                # Build a lightweight stub from the pipeline data
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

    Optimised flow:
      1. Load wardrobe from PostgreSQL.
      2. Pre-filter by occasion + weather (Opt.3) → 10-20 items instead of 50.
      3. Build payload using vision_features/llm_attributes when available (Opt.1+2).
      4. POST to LLM_project.
      5. Map response → RecommendationResponse.
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

    # Opt.3 — context-aware pre-filter
    occasion_val = None
    if config.occasion:
        raw = config.occasion.value if hasattr(config.occasion, "value") else str(config.occasion)
        if raw in _ALLOWED_OCCASIONS:
            occasion_val = raw

    wardrobe_items = _prefilter_wardrobe(wardrobe_items, occasion_val, config.weather_temp)

    min_required = 1 if config.garment_ids else 3
    if len(wardrobe_items) < min_required:
        logger.info(f"Not enough wardrobe items ({len(wardrobe_items)}) — using mock fallback")
        return _mock_fallback(config)

    context: dict = {}
    if occasion_val:
        context["occasion"] = occasion_val
    if config.weather_temp is not None:
        context["weather"] = {
            "temperature_celsius": config.weather_temp,
            "condition": config.weather_condition or "clear",
        }

    # Opt.1+2 — feature-first payload
    payload, _, _ = _build_pipeline_payload(
        wardrobe_items, context, config.top_k,
        enable_explanation=config.enable_explanation,
        explanation_detail=config.explanation_detail,
    )

    try:
        with _llm._client() as c:
            r = c.post("/api/v1/pipeline/recommend", json=payload)
            r.raise_for_status()
            pipeline = r.json()
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
    Auto-injects live weather when weather_temp is not provided.

    Optimised flow:
      1. Load wardrobe from PostgreSQL.
      2. Auto-fetch weather (async, non-blocking).
      3. Pre-filter by occasion + weather (Opt.3).
      4. Build feature-first payload (Opt.1+2).
      5. POST async to LLM_project.
      6. Map response → RecommendationResponse.
    """
    t_total = time.perf_counter()

    if not user_id:
        return _mock_fallback(config)

    # ── STEP 1: Load wardrobe ──
    t0 = time.perf_counter()
    try:
        wardrobe_items = _load_user_wardrobe(user_id)
    except Exception as e:
        logger.warning(f"Could not load wardrobe for {user_id}: {e}")
        return _mock_fallback(config)
    logger.info("⏱ STEP 1 — Load wardrobe: %.1fms (%d items)", (time.perf_counter() - t0) * 1000, len(wardrobe_items))

    if config.garment_ids:
        id_set = set(config.garment_ids)
        wardrobe_items = [g for g in wardrobe_items if g.id in id_set]

    # Build occasion value
    occasion_val = None
    if config.occasion:
        raw = config.occasion.value if hasattr(config.occasion, "value") else str(config.occasion)
        if raw in _ALLOWED_OCCASIONS:
            occasion_val = raw

    weather_temp = config.weather_temp
    weather_cond = config.weather_condition or "clear"

    # ── STEP 2: Auto-inject weather ──
    t0 = time.perf_counter()
    if weather_temp is None:
        try:
            w = await _fetch_weather("auto")
            weather_temp = w["temperature_celsius"]
            weather_cond = w["condition"]
            logger.info(f"Auto-injected weather: {weather_temp}°C, {weather_cond}")
        except Exception as we:
            logger.warning(f"Weather auto-fetch failed: {we}")
    logger.info("⏱ STEP 2 — Weather fetch: %.1fms", (time.perf_counter() - t0) * 1000)

    # ── STEP 3: Pre-filter ──
    t0 = time.perf_counter()
    wardrobe_items = _prefilter_wardrobe(wardrobe_items, occasion_val, weather_temp)
    logger.info("⏱ STEP 3 — Pre-filter: %.1fms (%d items remaining)", (time.perf_counter() - t0) * 1000, len(wardrobe_items))

    min_required = 1 if config.garment_ids else 3
    if len(wardrobe_items) < min_required:
        logger.info(f"Not enough wardrobe items ({len(wardrobe_items)}) — using mock fallback")
        return _mock_fallback(config)

    context: dict = {}
    if occasion_val:
        context["occasion"] = occasion_val
    if weather_temp is not None:
        context["weather"] = {
            "temperature_celsius": weather_temp,
            "condition": weather_cond,
        }

    # ── STEP 4: Build payload ──
    t0 = time.perf_counter()
    payload, _, _ = _build_pipeline_payload(
        wardrobe_items, context, config.top_k,
        enable_explanation=config.enable_explanation,
        explanation_detail=config.explanation_detail,
    )
    payload_size_kb = len(str(payload)) / 1024
    logger.info("⏱ STEP 4 — Build payload: %.1fms (%.0f KB, %d images)", (time.perf_counter() - t0) * 1000, payload_size_kb, len(payload.get("wardrobe_images", [])))

    # ── STEP 5: POST to LLM_project pipeline ──
    t0 = time.perf_counter()
    try:
        async with _llm._async_client() as c:
            r = await c.post("/api/v1/pipeline/recommend", json=payload)
            r.raise_for_status()
            pipeline = r.json()
    except Exception as e:
        logger.warning(f"Pipeline call failed: {e}")
        return _mock_fallback(config)
    pipeline_ms = (time.perf_counter() - t0) * 1000
    llm_internal_ms = pipeline.get("processing_time_ms", 0)
    logger.info("⏱ STEP 5 — LLM_project pipeline HTTP: %.1fms (internal: %.1fms, stages: %s)", pipeline_ms, llm_internal_ms, pipeline.get("stages_completed", []))

    if not pipeline or not pipeline.get("recommendations"):
        return _mock_fallback(config)

    # ── STEP 6: Map response ──
    t0 = time.perf_counter()
    response = _map_pipeline_to_response(pipeline, wardrobe_items, config)
    logger.info("⏱ STEP 6 — Map response: %.1fms (%d outfits)", (time.perf_counter() - t0) * 1000, len(response.outfits))

    # ── STEP 7: CF hybrid re-rank (style 40% + CF 40% + context 20%) ──
    if user_id:
        t0 = time.perf_counter()
        try:
            candidates = [
                {"id": o.id, "overall_score": o.score.overall}
                for o in response.outfits
            ]
            # Build context scores from occasion + weather fit
            ctx_scores: dict[str, float] = {}
            for o in response.outfits:
                ctx = (o.score.occasion_fit + o.score.season_fit) / 2.0
                ctx_scores[o.id] = round(ctx, 4)

            hybrid = await _llm.cf_hybrid_score_async(
                user_id, candidates, context_scores=ctx_scores,
            )
            if hybrid.get("trained") and hybrid.get("results"):
                # Re-order outfits by combined_score
                score_map = {
                    r["outfit_id"]: r for r in hybrid["results"]
                }
                for outfit in response.outfits:
                    h = score_map.get(outfit.id)
                    if h:
                        outfit.score.overall = round(h["combined_score"], 3)
                response.outfits.sort(key=lambda o: o.score.overall, reverse=True)
                for i, o in enumerate(response.outfits):
                    o.rank = i + 1
                logger.info("CF hybrid re-rank applied for user %s", user_id)
        except Exception as e:
            logger.debug("CF hybrid re-rank skipped: %s", e)
        logger.info("⏱ STEP 7 — CF hybrid re-rank: %.1fms (trained=%s)", (time.perf_counter() - t0) * 1000, hybrid.get("trained", False) if 'hybrid' in locals() else "error")

    total_ms = (time.perf_counter() - t_total) * 1000
    logger.info("⏱ TOTAL — get_recommendations_async: %.1fms", total_ms)

    return response


# ── Backward-compat alias ─────────────────────────────────────
def generate_outfits(config: RecommendationConfig) -> List[OutfitResult]:
    return get_recommendations(config).outfits

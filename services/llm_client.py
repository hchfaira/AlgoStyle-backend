"""
LLM Project API Client
=======================
Thin HTTP client that proxies AI calls from the AlgoStyle backend
to the LLM_project FastAPI server (which owns all vision/scoring/LLM logic).

All functions in this module return raw dicts (already parsed JSON).
Callers are responsible for mapping the response to their own schemas.

LLM_project API base URL is configured via:
  • .env  →  LLM_API_URL=http://<host>:<port>

If LLM_API_URL is not set (or empty), the LLM integration is disabled and
callers should fall back to their local mock/heuristic implementations.

Endpoints used:
  POST /api/v1/analyze/image              – garment image analysis (Layer 1)
  POST /api/v1/pipeline/recommend         – full pipeline (scoring + LLM + try-on)
  POST /api/v1/outfits/search/prompt      – natural-language outfit search (Layer 3/4)
  POST /api/v1/context/morphology/advice  – body/style profile analysis (Layer 3)
"""
from __future__ import annotations

import os
import base64
import logging
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

# ── Base URL ────────────────────────────────────────────────────────────────
def _base_url() -> str:
    """
    Resolve the LLM service base URL from config / environment.

    Returns an empty string when LLM_API_URL is not configured, which lets
    ``is_available()`` return False and keeps callers in mock/fallback mode.
    """
    try:
        from config import settings
        return (settings.llm_api_url or "").rstrip("/")
    except Exception:
        return os.getenv("LLM_API_URL", "").rstrip("/")


# Generous timeout — vision / pipeline calls can take 20–40 s
_TIMEOUT = httpx.Timeout(180.0, connect=5.0)


def _client() -> httpx.Client:
    url = _base_url()
    if not url:
        raise RuntimeError(
            "LLM_API_URL is not configured. Set it in .env to enable LLM integration."
        )
    return httpx.Client(base_url=url, timeout=_TIMEOUT)


def _async_client() -> httpx.AsyncClient:
    url = _base_url()
    if not url:
        raise RuntimeError(
            "LLM_API_URL is not configured. Set it in .env to enable LLM integration."
        )
    return httpx.AsyncClient(base_url=url, timeout=_TIMEOUT)


# ── Health check ─────────────────────────────────────────────────────────────

def is_available() -> bool:
    """Return True if the LLM project API is reachable (and LLM_API_URL is configured)."""
    try:
        with _client() as c:
            r = c.get("/health")
            return r.status_code == 200
    except Exception:
        return False


# ── Garment image analysis (Layer 1 Vision) ──────────────────────────────────

def analyze_garment_image(
    image_bytes: bytes,
    mode: str = "auto",
    hint_category: Optional[str] = None,
) -> dict:
    """
    Analyze a garment image using LLM_project Layer 1 Vision.
    LLM endpoint: POST /api/v1/analyze/image  (multipart)
    Returns raw JSON dict with an "analysis" key, or {} on failure.
    """
    try:
        with _client() as c:
            files = {"image": ("garment.jpg", image_bytes, "image/jpeg")}
            data: dict = {"mode": mode}
            if hint_category:
                data["hint_category"] = hint_category
            r = c.post("/api/v1/analyze/image", files=files, data=data)
            r.raise_for_status()
            return r.json()
    except httpx.HTTPStatusError as e:
        logger.warning("LLM analyze/image HTTP %s: %s", e.response.status_code, e.response.text[:200])
    except Exception as e:
        logger.warning("LLM analyze/image unavailable: %s", e)
    return {}


# ── Outfit scoring (Layer 2 — pipeline) ──────────────────────────────────────

def score_outfit_photo(image_bytes: bytes) -> dict:
    """
    Score a worn-outfit photo using LLM_project pipeline.
    LLM endpoint: POST /api/v1/pipeline/recommend
    Returns the pipeline recommendations dict, or {} on failure.
    """
    try:
        b64 = base64.b64encode(image_bytes).decode()
        payload = {
            "wardrobe_images": [{"image_b64": b64, "extract_category": None}],
            "top_k": 1,
            "enable_explanation": True,
            "enable_visualization": False,
            "enable_tryon": False,
        }
        with _client() as c:
            r = c.post("/api/v1/pipeline/recommend", json=payload)
            r.raise_for_status()
            return r.json()
    except httpx.HTTPStatusError as e:
        logger.warning("LLM pipeline HTTP %s: %s", e.response.status_code, e.response.text[:200])
    except Exception as e:
        logger.warning("LLM pipeline unavailable: %s", e)
    return {}


# ── Prompt-based outfit generation (Layer 3/4) ───────────────────────────────

def outfit_from_prompt(prompt: str, wardrobe_image_b64s: list[str]) -> dict:
    """
    Generate outfit suggestions from a natural-language prompt using the
    user's wardrobe images via the LLM_project pipeline.

    The prompt is passed as the occasion/custom_notes in UserContext so
    Layer 3 context scoring is guided by it, and Layer 4 LLM explanations
    reference it.

    LLM endpoint: POST /api/v1/pipeline/recommend
    Returns the pipeline response dict, or {} on failure.
    """
    try:
        images = [{"image_b64": b64, "extract_category": None} for b64 in wardrobe_image_b64s]
        payload = {
            "wardrobe_images": images,
            "context": {
                "custom_notes": prompt,
            },
            "top_k": 3,
            "enable_explanation": True,
            "enable_visualization": False,
            "enable_tryon": False,
        }
        with _client() as c:
            r = c.post("/api/v1/pipeline/recommend", json=payload)
            r.raise_for_status()
            return r.json()
    except httpx.HTTPStatusError as e:
        logger.warning("LLM prompt-search HTTP %s: %s", e.response.status_code, e.response.text[:200])
    except Exception as e:
        logger.warning("LLM prompt-search unavailable: %s", e)
    return {}


# ── Image consulting — body/style profile (Layer 3 StyleProfilePipeline) ─────

def analyze_style_profile(
    image_bytes: bytes,
    height_cm: Optional[float] = None,
    weight_kg: Optional[float] = None,
) -> dict:
    """
    Analyse a user photo to extract body shape, skin tone, undertone,
    face shape, contrast level, and colour season.

    Uses the LLM_project pipeline endpoint with a minimal dummy wardrobe
    image so that the user_profile extraction (StyleProfilePipeline / Layer 3)
    is triggered.  The `user_profile` key in the response holds the result.

    LLM endpoint: POST /api/v1/pipeline/recommend
    Returns the `user_profile` sub-dict from the pipeline response, or {} on failure.
    """
    try:
        user_b64 = base64.b64encode(image_bytes).decode()

        # We send the same image as both the "wardrobe" and the "user photo"
        # so the pipeline can extract a profile even without a real wardrobe.
        payload: dict = {
            "wardrobe_images": [{"image_b64": user_b64, "extract_category": None}],
            "user_profile": {
                "image_b64": user_b64,
            },
            "top_k": 1,
            "generate_explanation": False,
            "visualize": False,
            "enable_tryon": False,
        }
        if height_cm is not None:
            payload["user_profile"]["height_cm"] = height_cm
        if weight_kg is not None:
            payload["user_profile"]["weight_kg"] = weight_kg

        with _client() as c:
            r = c.post("/api/v1/pipeline/recommend", json=payload)
            r.raise_for_status()
            data = r.json()
            # Return the user_profile sub-dict; callers map this to their schema
            return data.get("user_profile") or data
    except httpx.HTTPStatusError as e:
        logger.warning("LLM style-profile HTTP %s: %s", e.response.status_code, e.response.text[:200])
    except Exception as e:
        logger.warning("LLM style-profile unavailable: %s", e)
    return {}


# ── Virtual Try-On (Layer 6 CatVTON / Replicate) ─────────────────────────────

def virtual_tryon(
    person_image_bytes: bytes,
    garment_image_bytes: bytes,
    garment_description: Optional[str] = None,
    backend: str = "catvton",
) -> dict:
    """
    Run a virtual try-on: overlay a garment onto a person photo.

    Uses the LLM_project pipeline endpoint with `enable_tryon=True`.
    Both images are sent as the wardrobe + user_profile images respectively.

    LLM endpoint: POST /api/v1/pipeline/recommend
    Returns dict with `tryon_image_b64` if successful, or {} on failure.
    """
    try:
        person_b64 = base64.b64encode(person_image_bytes).decode()
        garment_b64 = base64.b64encode(garment_image_bytes).decode()

        payload: dict = {
            "wardrobe_images": [{"image_b64": garment_b64, "extract_category": None}],
            "user_profile": {"image_b64": person_b64},
            "top_k": 1,
            "generate_explanation": False,
            "visualize": False,
            "enable_tryon": True,
            "tryon_backend": backend,
        }
        if garment_description:
            payload["wardrobe_images"][0]["description"] = garment_description

        with _client() as c:
            r = c.post("/api/v1/pipeline/recommend", json=payload)
            r.raise_for_status()
            data = r.json()
            # Extract the tryon result from the first recommendation
            recs = data.get("recommendations") or []
            tryon_b64 = recs[0].get("tryon_image_b64") if recs else None
            return {
                "result_image_b64": tryon_b64,
                "processing_time_ms": data.get("processing_time_ms", 0),
                "stages_completed": data.get("stages_completed", []),
                "errors": data.get("errors", []),
            }
    except httpx.HTTPStatusError as e:
        logger.warning("LLM tryon HTTP %s: %s", e.response.status_code, e.response.text[:200])
    except Exception as e:
        logger.warning("LLM tryon unavailable: %s", e)
    return {}


# ── Full pipeline (all layers) ────────────────────────────────────────────────

def run_pipeline(
    wardrobe_image_b64s: list[str],
    context: Optional[dict] = None,
    top_k: int = 3,
    generate_explanation: bool = True,
    visualize: bool = False,
) -> dict:
    """
    Run the full LLM_project recommendation pipeline.
    LLM endpoint: POST /api/v1/pipeline/recommend
    Returns the pipeline result dict, or {} on failure.
    """
    try:
        images = [{"image_b64": b64, "extract_category": None} for b64 in wardrobe_image_b64s]
        payload: dict = {
            "wardrobe_images": images,
            "top_k": top_k,
            "enable_explanation": generate_explanation,
            "enable_visualization": visualize,
            "enable_tryon": False,
        }
        if context:
            payload["context"] = context
        with _client() as c:
            r = c.post("/api/v1/pipeline/recommend", json=payload)
            r.raise_for_status()
            return r.json()
    except httpx.HTTPStatusError as e:
        logger.warning("LLM pipeline HTTP %s: %s", e.response.status_code, e.response.text[:200])
    except Exception as e:
        logger.warning("LLM pipeline unavailable: %s", e)
    return {}


async def run_pipeline_async(
    wardrobe_image_b64s: list[str],
    context: Optional[dict] = None,
    top_k: int = 3,
    generate_explanation: bool = True,
    visualize: bool = False,
) -> dict:
    """
    Async version of run_pipeline — must be used from async route handlers
    so the event loop is not blocked during long LLM/vision calls.
    """
    try:
        images = [{"image_b64": b64, "extract_category": None} for b64 in wardrobe_image_b64s]
        payload: dict = {
            "wardrobe_images": images,
            "top_k": top_k,
            "enable_explanation": generate_explanation,
            "enable_visualization": visualize,
            "enable_tryon": False,
        }
        if context:
            payload["context"] = context
        async with _async_client() as c:
            r = await c.post("/api/v1/pipeline/recommend", json=payload)
            r.raise_for_status()
            return r.json()
    except httpx.HTTPStatusError as e:
        logger.warning("LLM pipeline HTTP %s: %s", e.response.status_code, e.response.text[:200])
    except Exception as e:
        logger.warning("LLM pipeline unavailable: %s", e)
    return {}


# ── Garment schema converter ──────────────────────────────────────────────────

def _algogarment_to_llm(g: dict) -> dict:
    """
    Convert an AlgoStyle GarmentItem (as dict) to the LLM_project Garment shape.

    Fast path: if the garment was analysed by LLM_project and its native
    attributes were stored in ``llm_attributes``, return that directly
    (no conversion needed — the shape is already correct).

    Slow path (legacy / mock-added garments): re-build the nested shape from
    the flat AlgoStyle columns (category, color_primary, color_hex, …).
    """
    # ── Fast path ────────────────────────────────────────────────────────────
    llm_attrs = g.get("llm_attributes")
    if llm_attrs and isinstance(llm_attrs, dict) and llm_attrs.get("category"):
        return {
            "id": g.get("id") or "",
            "image_url": g.get("image_url"),
            "attributes": llm_attrs,
            "history": {},
        }

    # ── Slow path (flat columns → LLM nested shape) ──────────────────────────
    attrs = g.get("attributes") or {}
    color_primary = attrs.get("color_primary") or "unknown"

    # Normalise formality to the exact enum LLM_project expects:
    # very_casual | casual | smart_casual | business_casual | business | formal | black_tie
    _FORMALITY_MAP = {
        "very_casual": "very_casual",
        "casual": "casual",
        "smart_casual": "smart_casual",
        "business_casual": "business_casual",
        "business": "business",
        "formal": "formal",
        "black_tie": "black_tie",
        # common aliases
        "smart": "smart_casual",
        "semi-formal": "business_casual",
        "semi_formal": "business_casual",
        "office": "business",
        "evening": "formal",
    }
    raw_formality = (attrs.get("formality") or "casual").lower().strip()
    formality = _FORMALITY_MAP.get(raw_formality, "casual")

    llm_nested: dict = {
        "category": attrs.get("category") or "top",
        "subcategory": attrs.get("subcategory"),
        "color": {
            "primary": color_primary,
            "secondary": attrs.get("color_secondary"),
            "hex_codes": [attrs["color_hex"]] if attrs.get("color_hex") else [],
        },
        "pattern": {"type": attrs.get("pattern") or "solid"},
        "formality_level": formality,
        "confidence_score": float(attrs.get("confidence") or 0.80),
    }
    if attrs.get("material"):
        llm_nested["material"] = {"primary": attrs["material"]}
    seasons = attrs.get("seasons") or []
    if seasons:
        llm_nested["season_suitable"] = [s.lower() for s in seasons]

    return {
        "id": g.get("id") or "",
        "image_url": g.get("image_url"),
        "attributes": llm_nested,
        "history": {},
    }


# ── Outfit scoring via scoring API ────────────────────────────────────────────

def score_outfit_garments(
    garments_dicts: list[dict],
    occasion: Optional[str] = None,
    profile: str = "default",
) -> dict:
    """
    Score a set of garments using the LLM_project scoring scorecard.
    LLM endpoint: POST /api/v1/scoring/scorecard
    Returns the scorecard dict, or {} on failure.
    """
    try:
        llm_garments = [_algogarment_to_llm(g) for g in garments_dicts]
        payload: dict = {
            "garments": llm_garments,
            "profile": profile,
            "include_all_scores": True,
        }
        if occasion:
            payload["occasion"] = occasion
        with _client() as c:
            r = c.post("/api/v1/scoring/scorecard", json=payload)
            r.raise_for_status()
            return r.json()
    except httpx.HTTPStatusError as e:
        logger.warning("LLM scorecard HTTP %s: %s", e.response.status_code, e.response.text[:200])
    except Exception as e:
        logger.warning("LLM scorecard unavailable: %s", e)
    return {}


def explain_outfit_garments(
    garments_dicts: list[dict],
    occasion: Optional[str] = None,
) -> dict:
    """
    Get a full style explanation (grade, breakdown, strengths, improvements)
    for a set of garments.
    LLM endpoint: POST /api/v1/scoring/total-style
    Returns the style report dict, or {} on failure.
    """
    try:
        llm_garments = [_algogarment_to_llm(g) for g in garments_dicts]
        with _client() as c:
            r = c.post("/api/v1/scoring/total-style", json=llm_garments)
            r.raise_for_status()
            return r.json()
    except httpx.HTTPStatusError as e:
        logger.warning("LLM total-style HTTP %s: %s", e.response.status_code, e.response.text[:200])
    except Exception as e:
        logger.warning("LLM total-style unavailable: %s", e)
    return {}


# ── Wardrobe analysis (capsule score, gaps, versatility) ─────────────────────

def analyze_wardrobe(
    garments_dicts: list[dict],
    occasion: Optional[str] = None,
    top_k_versatile: int = 5,
) -> dict:
    """
    Run a full wardrobe analysis: distribution, gaps, occasion coverage,
    versatility scores, and purchase suggestions.
    LLM endpoint: POST /api/v1/wardrobe-analysis/analyze
    Returns the analysis dict, or {} on failure.
    """
    # Use a short timeout for the capsule-score path — if the LLM is slow,
    # the rule-based fallback kicks in instead of blocking the request for 3 min.
    _score_timeout = httpx.Timeout(10.0, connect=3.0)
    try:
        llm_garments = [_algogarment_to_llm(g) for g in garments_dicts]
        payload: dict = {
            "wardrobe": llm_garments,
            "top_k_versatile": top_k_versatile,
        }
        if occasion:
            payload["context"] = {"occasion": occasion}
        url = _base_url()
        with httpx.Client(base_url=url, timeout=_score_timeout) as c:
            r = c.post("/api/v1/wardrobe-analysis/analyze", json=payload)
            r.raise_for_status()
            return r.json()
    except httpx.HTTPStatusError as e:
        logger.warning("LLM wardrobe-analysis HTTP %s: %s", e.response.status_code, e.response.text[:200])
    except Exception as e:
        logger.warning("LLM wardrobe-analysis unavailable: %s", e)
    return {}


def get_purchase_suggestions(
    garments_dicts: list[dict],
    user_profile: Optional[dict] = None,
    max_suggestions: int = 6,
) -> dict:
    """
    Get prioritised purchase suggestions by running a full wardrobe analysis
    and extracting the purchase_suggestions + contextual data.

    LLM endpoint: POST /api/v1/wardrobe-analysis/analyze
    (same as analyze_wardrobe but scoped to what Smart-Add needs)

    Returns a dict with:
      purchase_suggestions: list of {
        priority, category, description, reason,
        estimated_outfit_increase, suggested_colors,
        suggested_styles, target_occasions,
        purchase_impact_score,        ← computed here
        color_hex,                    ← best suggested hex
        severity,                     ← from backing gap if available
      }
      gaps: list of {gap_type, severity, description, recommendation}
      occasion_coverage: list of {occasion, coverage_score, missing_categories, suggestion}
      overall_score: float (0-1)
      summary: str
      wardrobe_bottleneck: {category, count, impact_label}  ← category with fewest items
      dominant_colors: list[str]

    Returns {} on failure.
    """
    try:
        llm_garments = [_algogarment_to_llm(g) for g in garments_dicts]
        payload: dict = {
            "wardrobe": llm_garments,
            "top_k_versatile": 5,
            "occasions": ["daily_wear", "work", "date", "cocktail", "sport"],
        }
        with _client() as c:
            r = c.post("/api/v1/wardrobe-analysis/analyze", json=payload)
            r.raise_for_status()
            data = r.json()

        # ── Extract raw fields ──────────────────────────────────────────────
        raw_suggestions: list[dict] = data.get("purchase_suggestions") or []
        raw_gaps: list[dict] = data.get("gaps") or []
        raw_coverage: list[dict] = data.get("occasion_coverage") or []
        overall_score: float = float(data.get("overall_score") or 0.0)
        summary: str = data.get("summary") or ""

        # ── Build gap severity lookup by category ──────────────────────────
        # WardrobeGap fields: gap_type, severity, description, recommendation
        gap_severity_by_cat: dict[str, str] = {}
        for g in raw_gaps:
            # gap_type may be "category_missing" or "formality_gap" etc.
            # try to extract a category keyword from description/recommendation
            for field in ("description", "recommendation"):
                text = (g.get(field) or "").lower()
                for cat in ("top", "bottom", "dress", "outerwear", "shoes", "accessory"):
                    if cat in text:
                        gap_severity_by_cat[cat] = g.get("severity", "medium")
                        break

        # ── Identify wardrobe bottleneck (category with fewest items) ──────
        cat_counts: dict[str, int] = {}
        for g in garments_dicts:
            attrs = g.get("attributes") or {}
            cat = attrs.get("category") or g.get("category") or "other"
            cat_counts[cat] = cat_counts.get(cat, 0) + 1

        bottleneck_cat: Optional[str] = None
        bottleneck_count: int = 0
        if cat_counts:
            bottleneck_cat = min(cat_counts, key=lambda c: cat_counts[c])
            bottleneck_count = cat_counts[bottleneck_cat]

        # ── Dominant colors already in the wardrobe ──────────────────────
        color_tally: dict[str, int] = {}
        for g in garments_dicts:
            attrs = g.get("attributes") or {}
            col = (attrs.get("color_primary") or "").lower().strip()
            if col:
                color_tally[col] = color_tally.get(col, 0) + 1
        dominant_colors = sorted(color_tally, key=lambda c: -color_tally[c])[:4]

        # ── Enrich each suggestion with a purchase_impact_score ───────────
        # Score = 0.40 * outfit_increase_norm + 0.25 * gap_severity_weight
        #       + 0.20 * occasion_gap_weight + 0.15 * bottleneck_bonus
        max_increase = max(
            (s.get("estimated_outfit_increase") or 0 for s in raw_suggestions), default=1
        ) or 1

        # Build occasion gap weight: occasions with coverage_score < 0.6
        low_coverage_occasions: set[str] = {
            c["occasion"] for c in raw_coverage
            if isinstance(c.get("coverage_score"), (int, float))
            and c["coverage_score"] < 0.6
        }

        enriched: list[dict] = []
        for sug in raw_suggestions[:max_suggestions]:
            cat = (sug.get("category") or "").lower()
            outfit_inc = int(sug.get("estimated_outfit_increase") or 0)
            outfit_inc_norm = outfit_inc / max_increase

            sev = gap_severity_by_cat.get(cat, "low")
            gap_w = {"high": 1.0, "medium": 0.6, "low": 0.3}.get(sev, 0.3)

            target_occ = [o.lower() for o in (sug.get("target_occasions") or [])]
            occ_w = 1.0 if any(o in low_coverage_occasions for o in target_occ) else 0.4

            bottleneck_bonus = 1.0 if cat == bottleneck_cat else 0.0

            impact = (
                outfit_inc_norm * 0.40
                + gap_w         * 0.25
                + occ_w         * 0.20
                + bottleneck_bonus * 0.15
            )

            # Best suggested hex — pick first from suggested_colors if available
            # otherwise fall back to a neutral per category
            _CAT_HEX_FALLBACK = {
                "top":       "#F8F6F0",
                "bottom":    "#1A1A1A",
                "outerwear": "#1B2A4A",
                "shoes":     "#C19A6B",
                "accessory": "#C19A6B",
                "dress":     "#EDE8E2",
            }
            suggested_colors = sug.get("suggested_colors") or []
            color_hex = _CAT_HEX_FALLBACK.get(cat, "#D8D0C8")

            enriched.append({
                "priority":               int(sug.get("priority") or 99),
                "category":               cat,
                "description":            sug.get("description") or "",
                "reason":                 sug.get("reason") or "",
                "estimated_outfit_increase": outfit_inc,
                "suggested_colors":       suggested_colors,
                "suggested_styles":       sug.get("suggested_styles") or [],
                "target_occasions":       sug.get("target_occasions") or [],
                "purchase_impact_score":  round(impact, 3),
                "color_hex":              color_hex,
                "severity":               sev,
            })

        # Sort by purchase_impact_score descending
        enriched.sort(key=lambda x: -x["purchase_impact_score"])

        return {
            "purchase_suggestions": enriched,
            "gaps": raw_gaps,
            "occasion_coverage": raw_coverage,
            "overall_score": overall_score,
            "summary": summary,
            "wardrobe_bottleneck": {
                "category":    bottleneck_cat or "",
                "count":       bottleneck_count,
                "impact_label": f"Only {bottleneck_count} {bottleneck_cat}(s)" if bottleneck_cat else "",
            },
            "dominant_colors": dominant_colors,
        }

    except httpx.HTTPStatusError as e:
        logger.warning("LLM purchase-suggestions HTTP %s: %s", e.response.status_code, e.response.text[:200])
    except Exception as e:
        logger.warning("LLM purchase-suggestions unavailable: %s", e)
    return {}



def analyze_removal_impact(
    garment_id: str,
    garments_dicts: list[dict],
) -> dict:
    """
    Analyse the impact of removing a single garment from the wardrobe.
    LLM endpoint: POST /api/v1/wardrobe-analysis/impact-removal
    Returns the removal impact dict, or {} on failure.
    """
    try:
        llm_garments = [_algogarment_to_llm(g) for g in garments_dicts]
        payload = {"garment_id": garment_id, "wardrobe": llm_garments}
        with _client() as c:
            r = c.post("/api/v1/wardrobe-analysis/impact-removal", json=payload)
            r.raise_for_status()
            return r.json()
    except httpx.HTTPStatusError as e:
        logger.warning("LLM removal-impact HTTP %s: %s", e.response.status_code, e.response.text[:200])
    except Exception as e:
        logger.warning("LLM removal-impact unavailable: %s", e)
    return {}


def smart_removal_verdict(
    garment_id: str,
    garments_dicts: list[dict],
    user_goal: str = "maximize_options",
) -> dict:
    """
    Get a smart removal verdict for a single garment.
    LLM endpoint: POST /api/v1/wardrobe-analysis/smart-removal
    Returns the verdict dict, or {} on failure.
    """
    try:
        llm_garments = [_algogarment_to_llm(g) for g in garments_dicts]
        payload = {
            "garment_id": garment_id,
            "wardrobe": llm_garments,
            "user_goal": user_goal,
        }
        with _client() as c:
            r = c.post("/api/v1/wardrobe-analysis/smart-removal", json=payload)
            r.raise_for_status()
            return r.json()
    except httpx.HTTPStatusError as e:
        logger.warning("LLM smart-removal HTTP %s: %s", e.response.status_code, e.response.text[:200])
    except Exception as e:
        logger.warning("LLM smart-removal unavailable: %s", e)
    return {}


def smart_removal_wardrobe(
    garments_dicts: list[dict],
    user_goal: str = "maximize_options",
) -> list[dict]:
    """
    Get smart removal verdicts for every garment in the wardrobe at once.
    LLM endpoint: POST /api/v1/wardrobe-analysis/smart-removal/wardrobe
    Returns a list of verdict dicts sorted by regret_risk ascending (safest to remove first).
    """
    try:
        llm_garments = [_algogarment_to_llm(g) for g in garments_dicts]
        payload = {
            "wardrobe": llm_garments,
            "user_goal": user_goal,
        }
        with _client() as c:
            r = c.post("/api/v1/wardrobe-analysis/smart-removal/wardrobe", json=payload)
            r.raise_for_status()
            return r.json()
    except httpx.HTTPStatusError as e:
        logger.warning("LLM smart-removal/wardrobe HTTP %s: %s", e.response.status_code, e.response.text[:200])
    except Exception as e:
        logger.warning("LLM smart-removal/wardrobe unavailable: %s", e)
    return []


# ── Smart Add — Layer 2 simulate-addition (async) ────────────────────────────

async def simulate_garment_addition_async(
    new_garment_dict: dict,
    wardrobe_dicts: list[dict],
) -> dict:
    """
    Async — simulate adding a new garment to the wardrobe using Layer 2.

    Calls LLM_project POST /api/v1/wardrobe-analysis/simulate-addition.

    Returns a dict with keys:
      pair_count, outfit_count, versatility_score, is_gap_fill,
      gap_fill_reason, duplicate_id, duplicate_similarity,
      recommendation  (e.g. "Great addition — fills a key gap")

    Returns {} on failure (LLM_project unavailable or error).
    """
    try:
        llm_new = _algogarment_to_llm(new_garment_dict)
        llm_wardrobe = [_algogarment_to_llm(g) for g in wardrobe_dicts]
        payload = {
            "virtual_garment": llm_new,
            "wardrobe": llm_wardrobe,
        }
        async with _async_client() as c:
            r = await c.post("/api/v1/wardrobe-analysis/simulate-addition", json=payload)
            r.raise_for_status()
            return r.json()
    except httpx.HTTPStatusError as e:
        logger.warning(
            "LLM simulate-addition HTTP %s: %s",
            e.response.status_code, e.response.text[:200],
        )
    except Exception as e:
        logger.warning("LLM simulate-addition unavailable: %s", e)
    return {}


async def get_profile_fit_async(
    garment_dict: dict,
    user_profile: dict,
) -> dict:
    """
    Async — check how well a garment fits the user's body + color profile.

    Uses Layer 3 context: POST /api/v1/context/morphology/advice
    Expects user_profile to contain keys from UserProfile DB row:
      body_shape, color_season, undertone, skin_tone, etc.

    Returns a dict with keys:
      body_compatibility (0–1), color_season_match (bool),
      color_season_label (str), profile_notes (str)

    Returns {} on failure.
    """
    try:
        llm_garment = _algogarment_to_llm(garment_dict)
        payload = {
            "garments": [llm_garment],
            "body_shape": user_profile.get("body_shape"),
            "color_season": user_profile.get("color_season"),
            "skin_tone": user_profile.get("skin_tone"),
            "undertone": user_profile.get("undertone"),
            "height_cm": user_profile.get("height_cm"),
        }
        async with _async_client() as c:
            r = await c.post("/api/v1/context/morphology/advice", json=payload)
            r.raise_for_status()
            data = r.json()
            # Normalise the response to a flat dict regardless of shape
            advice = data.get("advice") or data
            return {
                "body_compatibility":  float(advice.get("body_compatibility") or advice.get("score") or 0.0),
                "color_season_match":  bool(advice.get("color_season_match", False)),
                "color_season_label":  advice.get("color_season_label") or user_profile.get("color_season", ""),
                "profile_notes":       advice.get("notes") or advice.get("recommendation") or "",
            }
    except httpx.HTTPStatusError as e:
        logger.warning(
            "LLM morphology-advice HTTP %s: %s",
            e.response.status_code, e.response.text[:200],
        )
    except Exception as e:
        logger.warning("LLM morphology-advice unavailable: %s", e)
    return {}


# ── Collaborative Filtering (Layer 7) ───────────────────────────────────────

async def cf_retrain_async(interactions: list[dict]) -> dict:
    """POST interaction data to LLM_project to retrain the CF model."""
    try:
        async with _async_client() as c:
            r = await c.post("/cf/retrain", json={"interactions": interactions})
            r.raise_for_status()
            return r.json()
    except Exception as e:
        logger.warning("CF retrain failed: %s", e)
        return {"trained": False, "error": str(e)}


async def cf_recommend_async(user_id: str, n: int = 10) -> dict:
    """GET CF recommendations for a user."""
    try:
        async with _async_client() as c:
            r = await c.get(f"/cf/recommendations/{user_id}", params={"n": n})
            r.raise_for_status()
            return r.json()
    except Exception as e:
        logger.warning("CF recommend failed: %s", e)
        return {"trained": False, "user_id": user_id, "recommendations": []}


async def cf_similar_users_async(user_id: str, n: int = 5) -> dict:
    """GET similar users (user-based CF)."""
    try:
        async with _async_client() as c:
            r = await c.get(f"/cf/similar-users/{user_id}", params={"n": n})
            r.raise_for_status()
            return r.json()
    except Exception as e:
        logger.warning("CF similar-users failed: %s", e)
        return {"trained": False, "user_id": user_id, "similar_users": []}


async def cf_item_pairs_async(garment_id: str, n: int = 5) -> dict:
    """GET item co-occurrence pairs (item-based CF)."""
    try:
        async with _async_client() as c:
            r = await c.get(f"/cf/item-pairs/{garment_id}", params={"n": n})
            r.raise_for_status()
            return r.json()
    except Exception as e:
        logger.warning("CF item-pairs failed: %s", e)
        return {"trained": False, "garment_id": garment_id, "pairs": []}


async def cf_hybrid_score_async(
    user_id: str,
    candidates: list[dict],
    context_scores: dict[str, float] | None = None,
) -> dict:
    """POST hybrid scoring (style 40% + CF 40% + context 20%)."""
    payload: dict = {
        "user_id": user_id,
        "candidates": candidates,
    }
    if context_scores:
        payload["context_scores"] = context_scores
    try:
        async with _async_client() as c:
            r = await c.post("/cf/hybrid-score", json=payload)
            r.raise_for_status()
            return r.json()
    except Exception as e:
        logger.warning("CF hybrid-score failed: %s", e)
        return {"trained": False, "user_id": user_id, "results": []}


async def cf_boost_score_async(user_id: str, garment_id: str) -> dict:
    """GET CF affinity score for a single user × garment pair."""
    try:
        async with _async_client() as c:
            r = await c.get(f"/cf/boost-score/{user_id}/{garment_id}")
            r.raise_for_status()
            return r.json()
    except Exception as e:
        logger.warning("CF boost-score failed: %s", e)
        return {"trained": False, "score": 0.5, "confidence": 0.0}


async def cf_status_async() -> dict:
    """GET the current CF model status."""
    try:
        async with _async_client() as c:
            r = await c.get("/cf/status")
            r.raise_for_status()
            return r.json()
    except Exception as e:
        logger.warning("CF status failed: %s", e)
        return {"trained": False}

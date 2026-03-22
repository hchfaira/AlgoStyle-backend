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
_TIMEOUT = httpx.Timeout(90.0, connect=5.0)


def _client() -> httpx.Client:
    url = _base_url()
    if not url:
        raise RuntimeError(
            "LLM_API_URL is not configured. Set it in .env to enable LLM integration."
        )
    return httpx.Client(base_url=url, timeout=_TIMEOUT)


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
    formality = attrs.get("formality") or "casual"

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
    try:
        llm_garments = [_algogarment_to_llm(g) for g in garments_dicts]
        payload: dict = {
            "wardrobe": llm_garments,
            "top_k_versatile": top_k_versatile,
        }
        if occasion:
            payload["context"] = {"occasion": occasion}
        with _client() as c:
            r = c.post("/api/v1/wardrobe-analysis/analyze", json=payload)
            r.raise_for_status()
            return r.json()
    except httpx.HTTPStatusError as e:
        logger.warning("LLM wardrobe-analysis HTTP %s: %s", e.response.status_code, e.response.text[:200])
    except Exception as e:
        logger.warning("LLM wardrobe-analysis unavailable: %s", e)
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


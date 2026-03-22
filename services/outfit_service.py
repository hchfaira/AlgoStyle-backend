"""
Outfit service — custom outfit CRUD + AI scoring & generation.
Uses PostgreSQL database for persistence.

AI scoring (score_outfit_photo) and prompt-based generation (outfit_from_prompt)
are delegated to the LLM_project API via llm_client.
"""
import uuid
import random
import base64
import logging
from datetime import datetime
from typing import List, Optional

logger = logging.getLogger(__name__)

from fastapi import HTTPException
from models.schemas import (
    CreateCustomOutfitRequest,
    UpdateOutfitPlanRequest,
    CustomOutfit,
    CustomOutfitResponse,
    GarmentItem,
    ReminderSetting,
)
from models.database import CustomOutfit as CustomOutfitDB
from db import get_db_context
from services import llm_client as _llm



# ── Serialization helper ──────────────────────────────────────────────────

def _db_to_schema(o: CustomOutfitDB, garments: Optional[List[GarmentItem]] = None) -> CustomOutfit:
    """Convert ORM row → Pydantic schema."""
    reminder = None
    if o.reminder_setting:
        try:
            reminder = ReminderSetting(**o.reminder_setting)
        except Exception:
            pass
    return CustomOutfit(
        id=o.id,
        name=o.name,
        description=o.description,
        garments=garments or [],
        is_public=o.is_public,
        planned_date=o.planned_date,
        reminder=reminder,
        user_timezone=o.user_timezone,
        source=o.source or "build",
        ai_grade=o.ai_grade,
        ai_score=o.ai_score,
        explanation_brief=o.explanation_brief,
        explanation_detailed=o.explanation_detailed,
        created_at=o.created_at,
        updated_at=o.updated_at,
    )


# ── CRUD ──────────────────────────────────────────────────────────────────

def create_outfit(
    user_id: str,
    request: CreateCustomOutfitRequest,
    garments: Optional[List[GarmentItem]] = None,
) -> CustomOutfitResponse:
    """Create a custom outfit from selected garments."""
    with get_db_context() as db:
        outfit = CustomOutfitDB(
            user_id=user_id,
            name=request.name,
            description=request.description,
            garment_ids=request.garment_ids,
            is_public=request.is_public,
            planned_date=request.planned_date,
            reminder_setting=request.reminder.model_dump() if request.reminder else None,
            user_timezone=request.user_timezone,
            source=request.source,
            ai_grade=request.ai_grade,
            ai_score=request.ai_score,
            explanation_brief=request.explanation_brief,
            explanation_detailed=request.explanation_detailed,
        )
        db.add(outfit)
        db.commit()
        db.refresh(outfit)

        return CustomOutfitResponse(
            success=True,
            outfit=_db_to_schema(outfit, garments),
            message=f"Outfit '{outfit.name}' created successfully!",
        )


def list_outfits(user_id: str, upcoming_only: bool = False, past_only: bool = False) -> dict:
    """Get all custom outfits for a user, optionally filtered by past/upcoming."""
    with get_db_context() as db:
        q = db.query(CustomOutfitDB).filter(CustomOutfitDB.user_id == user_id)
        now = datetime.utcnow()
        if upcoming_only:
            q = q.filter(
                (CustomOutfitDB.planned_date >= now) | (CustomOutfitDB.planned_date.is_(None))
            ).order_by(CustomOutfitDB.planned_date.asc().nullslast())
        elif past_only:
            q = q.filter(CustomOutfitDB.planned_date < now).order_by(CustomOutfitDB.planned_date.desc())
        else:
            q = q.order_by(CustomOutfitDB.planned_date.asc().nullslast(), CustomOutfitDB.created_at.desc())

        outfits = q.all()
        return {
            "outfits": [_db_to_schema(o).model_dump(mode="json") for o in outfits],
            "total": len(outfits),
        }


def get_outfit(user_id: str, outfit_id: str) -> CustomOutfit:
    """Get a specific custom outfit."""
    with get_db_context() as db:
        outfit = db.query(CustomOutfitDB).filter(
            CustomOutfitDB.id == outfit_id,
            CustomOutfitDB.user_id == user_id
        ).first()
        if not outfit:
            raise HTTPException(404, "Outfit not found")
        return _db_to_schema(outfit)


def update_outfit_plan(user_id: str, outfit_id: str, patch: UpdateOutfitPlanRequest) -> CustomOutfit:
    """Patch planning fields (date, reminder, name) on an existing outfit."""
    with get_db_context() as db:
        outfit = db.query(CustomOutfitDB).filter(
            CustomOutfitDB.id == outfit_id,
            CustomOutfitDB.user_id == user_id
        ).first()
        if not outfit:
            raise HTTPException(404, "Outfit not found")

        if patch.planned_date is not None:
            outfit.planned_date = patch.planned_date
        if patch.reminder is not None:
            outfit.reminder_setting = patch.reminder.model_dump()
        if patch.user_timezone is not None:
            outfit.user_timezone = patch.user_timezone
        if patch.name is not None:
            outfit.name = patch.name
        if patch.description is not None:
            outfit.description = patch.description
        outfit.updated_at = datetime.utcnow()

        db.commit()
        db.refresh(outfit)
        return _db_to_schema(outfit)


def delete_outfit(user_id: str, outfit_id: str) -> dict:
    """Delete a custom outfit."""
    with get_db_context() as db:
        outfit = db.query(CustomOutfitDB).filter(
            CustomOutfitDB.id == outfit_id,
            CustomOutfitDB.user_id == user_id
        ).first()
        if not outfit:
            raise HTTPException(404, "Outfit not found")
        outfit_name = outfit.name
        db.delete(outfit)
        db.commit()
        return {"success": True, "message": f"Outfit '{outfit_name}' deleted successfully!"}


def share_outfit(user_id: str, outfit_id: str) -> dict:
    """Generate a shareable link for a custom outfit."""
    with get_db_context() as db:
        outfit = db.query(CustomOutfitDB).filter(
            CustomOutfitDB.id == outfit_id,
            CustomOutfitDB.user_id == user_id
        ).first()
        if not outfit:
            raise HTTPException(404, "Outfit not found")
        share_code = str(uuid.uuid4())[:8]
        return {
            "success": True,
            "share_code": share_code,
            "share_url": f"https://algostyle.app/shared-outfit/{share_code}",
            "outfit_name": outfit.name,
        }


def get_planned_this_week(user_id: str) -> dict:
    """Return outfits with planned_date in the next 7 days."""
    from datetime import timedelta
    now = datetime.utcnow()
    week_end = now + timedelta(days=7)
    with get_db_context() as db:
        outfits = (
            db.query(CustomOutfitDB)
            .filter(
                CustomOutfitDB.user_id == user_id,
                CustomOutfitDB.planned_date >= now,
                CustomOutfitDB.planned_date <= week_end,
            )
            .order_by(CustomOutfitDB.planned_date.asc())
            .all()
        )
        return {
            "outfits": [_db_to_schema(o).model_dump(mode="json") for o in outfits],
            "total": len(outfits),
        }




# ── AI: Score a worn-outfit photo — delegated to LLM_project ─────────────

_SCORE_MOCK = {
    "overall": 0.84,
    "color_harmony": 0.88,
    "formality_match": 0.80,
    "proportion": 0.82,
    "creativity": 0.74,
    "summary": "Strong color story with good proportions. The layering adds dimension.",
    "improvements": [
        "Try a belt to define the waist and add structure.",
        "Swap the white sneakers for a tan leather loafer to elevate formality.",
        "A slim watch in gold would tie the warm tones together beautifully.",
    ],
    "style_score_label": "Editorial",
}


def _grade_label(score: float) -> str:
    """Convert a 0–1 overall score to a human-readable grade label."""
    if score >= 0.93: return "Iconic"
    if score >= 0.86: return "Editorial"
    if score >= 0.78: return "Polished"
    if score >= 0.68: return "Casual Chic"
    if score >= 0.55: return "Everyday"
    return "Work in Progress"


def score_outfit_photo(user_id: str, image_bytes: bytes) -> dict:
    """
    Score a worn-outfit photo via LLM_project pipeline (Layer 2 scoring).
    Falls back to mock scores if the LLM project is unreachable.
    """
    llm_result = _llm.score_outfit_photo(image_bytes)
    if llm_result:
        # Pipeline returns {"recommendations": [{"overall_score", "score_breakdown", "explanation", ...}]}
        recs = llm_result.get("recommendations") or []
        top = recs[0] if recs else {}
        breakdown = top.get("score_breakdown") or {}
        result = {
            "overall":           top.get("overall_score", 0.84),
            "color_harmony":     breakdown.get("color_harmony", breakdown.get("season_color", 0.88)),
            "formality_match":   breakdown.get("formality", breakdown.get("occasion", 0.80)),
            "proportion":        breakdown.get("proportion", 0.82),
            "creativity":        breakdown.get("creativity", 0.74),
            "summary":           top.get("explanation") or _SCORE_MOCK["summary"],
            "improvements":      _SCORE_MOCK["improvements"],
            "style_score_label": _grade_label(top.get("overall_score", 0.84)),
            "user_id":           user_id,
        }
        return result

    # Mock fallback
    result = dict(_SCORE_MOCK)
    result["user_id"] = user_id
    result["overall"] = round(random.uniform(0.72, 0.96), 2)
    result["color_harmony"] = round(random.uniform(0.70, 0.98), 2)
    result["formality_match"] = round(random.uniform(0.75, 0.95), 2)
    result["proportion"] = round(random.uniform(0.70, 0.95), 2)
    result["creativity"] = round(random.uniform(0.40, 0.85), 2)
    return result


# ── AI: Build outfit from a text prompt — delegated to LLM_project ───────

_PROMPT_OUTFITS = [
    {
        "name": "Parisian Sunday",
        "description": "Effortless chic for a morning at a Paris café.",
        "pieces": [
            {"label": "Cream linen shirt", "category": "top", "color": "#F5F0E8"},
            {"label": "Dark navy straight trousers", "category": "bottom", "color": "#1B2A4A"},
            {"label": "White leather loafers", "category": "shoes", "color": "#FFFFFF"},
            {"label": "Silk square scarf", "category": "accessory", "color": "#C8A96E"},
        ],
        "score": 0.91,
        "mood": "Refined & approachable",
        "styling_tip": "Tuck the shirt loosely — half-tuck for movement.",
    },
]


def outfit_from_prompt(user_id: str, prompt: str) -> dict:
    """
    Generate an outfit suggestion from a natural-language prompt.
    Fetches the user's wardrobe image_urls, sends them + the prompt to
    LLM_project pipeline (Layer 2+3+4), maps the top recommendation.
    Falls back to mock if the LLM project is unreachable or wardrobe is empty.
    """
    # Pull image URLs from wardrobe
    from models.database import GarmentItem as GarmentItemDB
    wardrobe_b64s: list[str] = []
    try:
        with get_db_context() as db:
            items = db.query(GarmentItemDB).filter(
                GarmentItemDB.user_id == user_id,
                GarmentItemDB.image_url.isnot(None),
            ).limit(20).all()
            for item in items:
                url = item.image_url or ""
                if url.startswith("data:"):
                    # data:<mime>;base64,<data>  →  extract base64 part
                    b64 = url.split(",", 1)[-1] if "," in url else ""
                    if b64:
                        wardrobe_b64s.append(b64)
    except Exception as e:
        logger.warning("Could not fetch wardrobe for prompt: %s", e)

    if not wardrobe_b64s:
        # No wardrobe — return mock directly (nothing to style)
        result = dict(_PROMPT_OUTFITS[0])
        result["user_id"] = user_id
        result["prompt"] = prompt
        result["name"] = f"Outfit for: {prompt[:40]}"
        result["score"] = round(random.uniform(0.80, 0.97), 2)
        return result

    llm_result = _llm.outfit_from_prompt(prompt, wardrobe_b64s)
    if llm_result:
        # Pipeline returns {"recommendations": [{"name", "overall_score", "garments", "explanation", ...}]}
        recs = llm_result.get("recommendations") or []
        pieces = []
        for rec in recs:
            for g in (rec.get("garments") or []):
                pieces.append({
                    "label":    g.get("subcategory") or g.get("category", ""),
                    "category": g.get("category", ""),
                    "color":    g.get("color_primary") or "#888888",
                })
        first = recs[0] if recs else {}
        return {
            "user_id":     user_id,
            "prompt":      prompt,
            "name":        first.get("name") or f"Outfit for: {prompt[:40]}",
            "description": first.get("explanation") or "",
            "mood":        "",
            "pieces":      pieces,
            "score":       first.get("overall_score", round(random.uniform(0.80, 0.97), 2)),
            "styling_tip": "",
        }

    # Mock fallback
    result = dict(_PROMPT_OUTFITS[0])
    result["user_id"] = user_id
    result["prompt"] = prompt
    result["name"] = f"Outfit for: {prompt[:40]}"
    result["score"] = round(random.uniform(0.80, 0.97), 2)
    return result


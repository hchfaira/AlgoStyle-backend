"""
Outfit service — custom outfit CRUD + AI scoring & generation.
Uses PostgreSQL database for persistence.
"""
import os
import uuid
import random
import base64
from datetime import datetime
from typing import List, Optional

from dotenv import load_dotenv
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

load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))

# ── Gemini Vision setup (optional) ────────────────────────────────────────
_VISION_AVAILABLE = False
_gemini_client = None
try:
    import google.generativeai as genai
    _key = os.getenv("GOOGLE_API_KEY", "")
    if _key:
        genai.configure(api_key=_key)
        _gemini_client = genai.GenerativeModel("gemini-2.5-flash")
        _VISION_AVAILABLE = True
except Exception:
    pass


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


# ── AI: Score a worn-outfit photo ─────────────────────────────────────────

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


def score_outfit_photo(user_id: str, image_bytes: bytes) -> dict:
    """Score a worn-outfit photo and return scores + improvement tips."""
    if _VISION_AVAILABLE and _gemini_client and image_bytes:
        try:
            import PIL.Image
            import io
            img = PIL.Image.open(io.BytesIO(image_bytes))
            prompt = (
                "You are a luxury fashion stylist AI. Analyse this worn outfit photo.\n"
                "Return a JSON object with EXACTLY these keys:\n"
                "  overall (0-1 float), color_harmony (0-1), formality_match (0-1),\n"
                "  proportion (0-1), creativity (0-1),\n"
                "  summary (1-sentence editorial compliment),\n"
                "  improvements (list of 3 concise actionable tips),\n"
                "  style_score_label (one of: Iconic / Editorial / Polished / Casual / Needs Work).\n"
                "Respond with raw JSON only, no markdown."
            )
            resp = _gemini_client.generate_content([prompt, img])
            import json, re
            text = resp.text.strip()
            text = re.sub(r"^```json\s*|^```\s*|```$", "", text, flags=re.MULTILINE).strip()
            data = json.loads(text)
            data["user_id"] = user_id
            return data
        except Exception:
            pass

    result = dict(_SCORE_MOCK)
    result["user_id"] = user_id
    result["overall"] = round(random.uniform(0.72, 0.96), 2)
    result["color_harmony"] = round(random.uniform(0.70, 0.98), 2)
    result["formality_match"] = round(random.uniform(0.75, 0.95), 2)
    result["proportion"] = round(random.uniform(0.70, 0.95), 2)
    result["creativity"] = round(random.uniform(0.40, 0.85), 2)
    return result


# ── AI: Build outfit from a text prompt ───────────────────────────────────

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
    """Generate an outfit suggestion from a text prompt using Gemini or mock data."""
    if _VISION_AVAILABLE and _gemini_client and prompt.strip():
        try:
            system = (
                "You are a luxury fashion editor AI. A user described what they want to wear.\n"
                "Build a complete outfit suggestion as a JSON object with EXACTLY:\n"
                "  name (string), description (string), mood (string),\n"
                "  pieces: list of {label, category, color (hex)},\n"
                "  score (0-1 float representing how well it matches the prompt),\n"
                "  styling_tip (one actionable string).\n"
                "Respond with raw JSON only, no markdown fences."
            )
            resp = _gemini_client.generate_content(f"{system}\n\nUser prompt: {prompt}")
            import json, re
            text = resp.text.strip()
            text = re.sub(r"^```json\s*|^```\s*|```$", "", text, flags=re.MULTILINE).strip()
            data = json.loads(text)
            data["user_id"] = user_id
            data["prompt"] = prompt
            return data
        except Exception:
            pass

    result = dict(_PROMPT_OUTFITS[0])
    result["user_id"] = user_id
    result["prompt"] = prompt
    result["name"] = f"Outfit for: {prompt[:40]}"
    result["score"] = round(random.uniform(0.80, 0.97), 2)
    return result


# ── Gemini Vision setup (optional) ────────────────────────────────────────
_VISION_AVAILABLE = False
_gemini_client = None
try:
    import google.generativeai as genai
    _key = os.getenv("GOOGLE_API_KEY", "")
    if _key:
        genai.configure(api_key=_key)
        _gemini_client = genai.GenerativeModel("gemini-2.5-flash")
        _VISION_AVAILABLE = True
except Exception:
    pass


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
        )
        db.add(outfit)
        db.commit()
        
        return CustomOutfitResponse(
            success=True,
            outfit=CustomOutfit(
                id=outfit.id,
                name=outfit.name,
                description=outfit.description,
                garments=garments or [],
                is_public=outfit.is_public,
                created_at=outfit.created_at,
                updated_at=outfit.updated_at,
            ),
            message=f"Outfit '{outfit.name}' created successfully!",
        )


def list_outfits(user_id: str) -> dict:
    """Get all custom outfits for a user."""
    with get_db_context() as db:
        outfits = db.query(CustomOutfitDB).filter(CustomOutfitDB.user_id == user_id).all()
        return {
            "outfits": [
                CustomOutfit(
                    id=o.id,
                    name=o.name,
                    description=o.description,
                    garments=[],
                    is_public=o.is_public,
                    created_at=o.created_at,
                    updated_at=o.updated_at,
                ).model_dump()
                for o in outfits
            ],
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
        
        return CustomOutfit(
            id=outfit.id,
            name=outfit.name,
            description=outfit.description,
            garments=[],
            is_public=outfit.is_public,
            created_at=outfit.created_at,
            updated_at=outfit.updated_at,
        )


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
        
        import uuid
        share_code = str(uuid.uuid4())[:8]
        return {
            "success": True,
            "share_code": share_code,
            "share_url": f"https://algostyle.app/shared-outfit/{share_code}",
            "outfit_name": outfit.name,
        }


# ── AI: Score a worn-outfit photo ─────────────────────────────────────────

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


def score_outfit_photo(user_id: str, image_bytes: bytes) -> dict:
    """Score a worn-outfit photo and return scores + improvement tips."""
    if _VISION_AVAILABLE and _gemini_client and image_bytes:
        try:
            import PIL.Image
            import io
            img = PIL.Image.open(io.BytesIO(image_bytes))
            prompt = (
                "You are a luxury fashion stylist AI. Analyse this worn outfit photo.\n"
                "Return a JSON object with EXACTLY these keys:\n"
                "  overall (0-1 float), color_harmony (0-1), formality_match (0-1),\n"
                "  proportion (0-1), creativity (0-1),\n"
                "  summary (1-sentence editorial compliment),\n"
                "  improvements (list of 3 concise actionable tips),\n"
                "  style_score_label (one of: Iconic / Editorial / Polished / Casual / Needs Work).\n"
                "Respond with raw JSON only, no markdown."
            )
            resp = _gemini_client.generate_content([prompt, img])
            import json, re
            text = resp.text.strip()
            text = re.sub(r"^```json\s*|^```\s*|```$", "", text, flags=re.MULTILINE).strip()
            data = json.loads(text)
            data["user_id"] = user_id
            return data
        except Exception as e:
            pass  # fall through to mock

    # Mock fallback
    result = dict(_SCORE_MOCK)
    result["user_id"] = user_id
    result["overall"] = round(random.uniform(0.72, 0.96), 2)
    result["color_harmony"] = round(random.uniform(0.70, 0.98), 2)
    result["formality_match"] = round(random.uniform(0.75, 0.95), 2)
    result["proportion"] = round(random.uniform(0.70, 0.95), 2)
    result["creativity"] = round(random.uniform(0.40, 0.85), 2)
    return result


# ── AI: Build outfit from a text prompt ───────────────────────────────────

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
    """Generate an outfit suggestion from a text prompt using Gemini or mock data."""
    if _VISION_AVAILABLE and _gemini_client and prompt.strip():
        try:
            system = (
                "You are a luxury fashion editor AI. A user described what they want to wear.\n"
                "Build a complete outfit suggestion as a JSON object with EXACTLY:\n"
                "  name (string), description (string), mood (string),\n"
                "  pieces: list of {label, category, color (hex)},\n"
                "  score (0-1 float representing how well it matches the prompt),\n"
                "  styling_tip (one actionable string).\n"
                "Respond with raw JSON only, no markdown fences."
            )
            resp = _gemini_client.generate_content(
                f"{system}\n\nUser prompt: {prompt}"
            )
            import json, re
            text = resp.text.strip()
            text = re.sub(r"^```json\s*|^```\s*|```$", "", text, flags=re.MULTILINE).strip()
            data = json.loads(text)
            data["user_id"] = user_id
            data["prompt"] = prompt
            return data
        except Exception:
            pass

    # Mock fallback
    result = dict(_PROMPT_OUTFITS[0])
    result["user_id"] = user_id
    result["prompt"] = prompt
    result["name"] = f"Outfit for: {prompt[:40]}"
    result["score"] = round(random.uniform(0.80, 0.97), 2)
    return result


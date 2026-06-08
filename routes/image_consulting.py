"""
Image Consulting Routes — thin handlers delegating to image_consulting_service.
"""
from typing import Optional

from fastapi import APIRouter, HTTPException, UploadFile, File, Form
from pydantic import BaseModel
from models.schemas import ImageConsultingResult
from services import image_consulting_service
from deps import CurrentUser

router = APIRouter()


@router.post("/analyze/{user_id}", response_model=ImageConsultingResult)
async def analyze_image(
    current_user: CurrentUser,
    user_id: str,
    image: UploadFile = File(...),
    height_cm: Optional[float] = Form(default=None),
    weight_kg: Optional[float] = Form(default=None),
):
    """Run the full image consulting pipeline on a user photo."""
    if current_user["user_id"] != user_id:
        raise HTTPException(403, "Access forbidden")
    image_bytes = await image.read()
    if not image_bytes:
        raise HTTPException(400, "Empty image file")
    return await image_consulting_service.analyze_image(
        user_id, image_bytes, height_cm, weight_kg,
    )


class ManualAnalysisRequest(BaseModel):
    """Manual-entry body/coloring data for Style DNA analysis without a photo."""
    sex: str
    age: int
    height_cm: Optional[float] = None
    weight_kg: Optional[float] = None
    body_shape: str
    skin_tone: str
    hair_color: str
    undertone: str


@router.post("/analyze-manual/{user_id}", response_model=ImageConsultingResult)
async def analyze_manual(current_user: CurrentUser, user_id: str, req: ManualAnalysisRequest):
    """Run Style DNA analysis using manually-entered attributes (no photo)."""
    if current_user["user_id"] != user_id:
        raise HTTPException(403, "Access forbidden")
    return await image_consulting_service.analyze_manual(
        user_id=user_id,
        sex=req.sex,
        age=req.age,
        height_cm=req.height_cm,
        weight_kg=req.weight_kg,
        body_shape=req.body_shape,
        skin_tone=req.skin_tone,
        hair_color=req.hair_color,
        undertone=req.undertone,
    )


@router.get("/result/{user_id}", response_model=ImageConsultingResult)
async def get_result(current_user: CurrentUser, user_id: str):
    """Return the most recent image consulting result for a user."""
    if current_user["user_id"] != user_id:
        raise HTTPException(403, "Access forbidden")
    return image_consulting_service.get_cached_result(user_id)

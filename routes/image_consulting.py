"""
Image Consulting Routes — thin handlers delegating to image_consulting_service.
"""
from typing import Optional

from fastapi import APIRouter, HTTPException, UploadFile, File, Form
from models.schemas import ImageConsultingResult
from services import image_consulting_service

router = APIRouter()


@router.post("/analyze/{user_id}", response_model=ImageConsultingResult)
async def analyze_image(
    user_id: str,
    image: UploadFile = File(...),
    height_cm: Optional[float] = Form(default=None),
    weight_kg: Optional[float] = Form(default=None),
):
    """Run the full image consulting pipeline on a user photo."""
    image_bytes = await image.read()
    if not image_bytes:
        raise HTTPException(400, "Empty image file")
    return await image_consulting_service.analyze_image(
        user_id, image_bytes, height_cm, weight_kg,
    )


@router.get("/result/{user_id}", response_model=ImageConsultingResult)
async def get_result(user_id: str):
    """Return the most recent image consulting result for a user."""
    return image_consulting_service.get_cached_result(user_id)

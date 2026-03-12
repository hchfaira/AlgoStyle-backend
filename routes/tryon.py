"""
Try-On routes — thin handler delegating to tryon_service.
"""
from fastapi import APIRouter
from models.schemas import TryOnRequest, TryOnResponse
from services import tryon_service

router = APIRouter()


@router.post("/virtual", response_model=TryOnResponse)
async def virtual_tryon(req: TryOnRequest):
    """Virtual try-on endpoint."""
    return tryon_service.virtual_tryon(req)

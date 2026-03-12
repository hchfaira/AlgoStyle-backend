"""
LLM Outfit Explanation Route — thin handler delegating to explain_service.
"""
from fastapi import APIRouter
from models.schemas import ExplainOutfitRequest, ExplainOutfitResponse
from services import explain_service

router = APIRouter()


@router.post("/explain", response_model=ExplainOutfitResponse)
async def explain_outfit(request: ExplainOutfitRequest):
    """Generate a detailed LLM explanation for a single outfit."""
    return await explain_service.explain_outfit(request)

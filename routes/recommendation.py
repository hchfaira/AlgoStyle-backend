"""
Recommendation routes — Generate outfit recommendations.
Delegates to services.recommendation_service.
"""
from fastapi import APIRouter
from models.schemas import RecommendationConfig, RecommendationResponse
from services import recommendation_service

router = APIRouter()


@router.post("/outfits", response_model=RecommendationResponse)
async def get_recommendations(config: RecommendationConfig):
    """
    Generate outfit recommendations.
    V1: Returns mock scored outfits. Production: calls Layer 2+3+4.
    """
    return recommendation_service.get_recommendations(config)

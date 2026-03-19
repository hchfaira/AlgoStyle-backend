"""
Recommendation routes — Generate outfit recommendations + context snapshot.
Delegates to services.recommendation_service and services.weather_service.
"""
from typing import Optional
from fastapi import APIRouter, Query
from models.schemas import RecommendationConfig, RecommendationResponse
from services import recommendation_service
from services.weather_service import fetch_weather

router = APIRouter()


@router.post("/outfits", response_model=RecommendationResponse)
async def get_recommendations(
    config: RecommendationConfig,
    user_id: Optional[str] = Query(None, description="User ID to load wardrobe for"),
):
    """
    Generate outfit recommendations using HybridOutfitRecommender.
    Pass ?user_id=<id> to load the user's real wardrobe from the database.
    Falls back to mock data when user_id is absent or wardrobe is too small.
    """
    return await recommendation_service.get_recommendations_async(config, user_id=user_id)


@router.get("/context")
async def get_context(
    city: Optional[str] = Query(None, description="City name (auto-detects from IP if omitted)"),
):
    """
    Return current weather + time-of-day context snapshot.

    Used by the Recommend tab to display live conditions and feed the
    HybridOutfitRecommender's context engine.

    Response fields:
        temperature_celsius   – real-time temperature (°C)
        feels_like_celsius    – perceived temperature
        condition             – e.g. "sunny", "rainy", "cloudy"
        humidity              – relative humidity %
        city_name             – resolved city
        time_of_day           – "morning" | "afternoon" | "evening" | "night"
        ai_context_summary    – short human-readable string for the UI
        cached                – whether result came from in-memory cache
    """
    weather = await fetch_weather(city or "auto")

    temp = weather["temperature_celsius"]
    condition = weather["condition"]
    tod = weather["time_of_day"]
    city_name = weather.get("city_name", "")

    # Build a short AI context label for display in the app
    temp_label = (
        "very cold" if temp < 5
        else "cold" if temp < 12
        else "cool" if temp < 18
        else "comfortable" if temp < 24
        else "warm" if temp < 30
        else "hot"
    )
    ai_context_summary = (
        f"{city_name} · {temp}°C · {condition.capitalize()} · "
        f"{tod.capitalize()} · {temp_label.capitalize()}"
    )

    return {
        **weather,
        "ai_context_summary": ai_context_summary,
    }

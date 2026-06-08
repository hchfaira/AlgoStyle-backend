"""
Recommendation routes — Generate outfit recommendations + context snapshot.
Delegates to services.recommendation_service and services.weather_service.
"""
from typing import Optional
from fastapi import APIRouter, Query, UploadFile, File, Form
from models.schemas import RecommendationConfig, RecommendationResponse
from services import recommendation_service
from services import inspiration_service
from services.weather_service import fetch_weather
from deps import CurrentUser
import base64

router = APIRouter()


@router.post("/outfits", response_model=RecommendationResponse)
async def get_recommendations(
    current_user: CurrentUser,
    config: RecommendationConfig,
):
    """
    Generate outfit recommendations using HybridOutfitRecommender.
    Uses the authenticated user's wardrobe. Falls back to mock data when
    the wardrobe is too small.
    """
    return await recommendation_service.get_recommendations_async(config, user_id=current_user["user_id"])


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


@router.post("/outfits/from-inspiration", response_model=RecommendationResponse)
async def outfit_from_inspiration(
    current_user: CurrentUser,
    photo: UploadFile = File(..., description="Inspiration outfit photo"),
    occasion: Optional[str] = Form(None, description="Target occasion"),
    top_k: int = Form(3, description="Number of outfits to return"),
):
    """
    Generate outfits from an inspiration photo using the user's wardrobe.

    Accepts a multipart form with an image file.
    Internally:
      1. Reads the image and encodes to base64
      2. Loads the user's wardrobe + body shape from the DB
      3. Calls LLM_project /pipeline/from-inspiration
      4. Returns the standard RecommendationResponse
    """
    image_bytes = await photo.read()
    image_b64 = base64.b64encode(image_bytes).decode("utf-8")

    return await inspiration_service.generate_from_inspiration_async(
        inspiration_image_b64=image_b64,
        user_id=current_user["user_id"],
        occasion=occasion,
        top_k=top_k,
    )

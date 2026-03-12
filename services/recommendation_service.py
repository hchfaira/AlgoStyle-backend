"""
Recommendation service — outfit generation logic.
"""
import uuid
import random
import time
from typing import List

from models.schemas import (
    RecommendationConfig, RecommendationResponse, OutfitResult,
    OutfitScore, GarmentItem, GarmentAttributes, GarmentCategory,
)


OUTFIT_NAMES = [
    "Smart Casual Navy Ensemble",
    "Effortless Weekend Look",
    "Modern Minimalist Set",
    "Urban Chic Collection",
    "Classic Elegance Combo",
    "Bold Color Story",
    "Relaxed Summer Vibes",
    "Business Power Look",
    "Date Night Perfection",
    "Bohemian Sunset Mix",
]

EXPLANATIONS_BRIEF = [
    "A harmonious blend of navy and earth tones creates a polished yet relaxed silhouette.",
    "Clean lines and neutral colors deliver effortless sophistication for any occasion.",
    "The contrast between structured outerwear and relaxed bottoms balances formality and comfort.",
    "Complementary colors and matching formality levels make this combination work seamlessly.",
    "A modern take on classic proportions with a pop of color for visual interest.",
]

EXPLANATIONS_DETAILED = [
    "This outfit scores highly on color harmony due to the analogous navy-blue palette, which creates visual cohesion. The formality levels are well-matched across all pieces, making it versatile for smart casual settings. The three-color rule is respected with navy, white, and tan as the dominant palette. The proportions follow the golden ratio — a fitted top with relaxed bottoms creates a balanced silhouette. For your warm undertone, these cool-toned pieces provide a flattering contrast.",
]


def _create_mock_garments() -> List[GarmentItem]:
    """Create mock garments for a demo outfit."""
    return [
        GarmentItem(
            id=f"g_{uuid.uuid4().hex[:10]}",
            user_id="demo",
            attributes=GarmentAttributes(
                category=GarmentCategory.TOP,
                subcategory="oxford shirt",
                color_primary="navy",
                color_hex="#1B2A4A",
                pattern="solid",
                material="cotton",
                formality="smart_casual",
                confidence=0.92,
            ),
        ),
        GarmentItem(
            id=f"g_{uuid.uuid4().hex[:10]}",
            user_id="demo",
            attributes=GarmentAttributes(
                category=GarmentCategory.BOTTOM,
                subcategory="chino",
                color_primary="beige",
                color_hex="#D4C5A9",
                pattern="solid",
                material="cotton",
                formality="smart_casual",
                confidence=0.88,
            ),
        ),
        GarmentItem(
            id=f"g_{uuid.uuid4().hex[:10]}",
            user_id="demo",
            attributes=GarmentAttributes(
                category=GarmentCategory.SHOES,
                subcategory="leather loafers",
                color_primary="brown",
                color_hex="#8B6F47",
                pattern="solid",
                material="leather",
                formality="smart_casual",
                confidence=0.90,
            ),
        ),
    ]


def _create_mock_score() -> OutfitScore:
    """Create a random mock outfit score."""
    return OutfitScore(
        overall=round(random.uniform(0.72, 0.96), 2),
        color_harmony=round(random.uniform(0.70, 0.98), 2),
        formality_match=round(random.uniform(0.75, 0.95), 2),
        occasion_fit=round(random.uniform(0.65, 0.95), 2),
        pattern_mixing=round(random.uniform(0.80, 1.0), 2),
        proportion=round(random.uniform(0.70, 0.95), 2),
        season_fit=round(random.uniform(0.60, 0.95), 2),
        creativity=round(random.uniform(0.40, 0.85), 2),
    )


def generate_outfits(config: RecommendationConfig) -> List[OutfitResult]:
    """Generate mock outfit recommendations for V1 demo."""
    results = []
    k = min(config.top_k, len(OUTFIT_NAMES))

    for i in range(k):
        results.append(
            OutfitResult(
                rank=i + 1,
                name=OUTFIT_NAMES[i],
                garments=_create_mock_garments(),
                score=_create_mock_score(),
                explanation_brief=EXPLANATIONS_BRIEF[i % len(EXPLANATIONS_BRIEF)],
                explanation_detailed=EXPLANATIONS_DETAILED[0],
            )
        )

    # Sort by overall score descending
    results.sort(key=lambda o: o.score.overall, reverse=True)
    for idx, r in enumerate(results):
        r.rank = idx + 1

    return results


def get_recommendations(config: RecommendationConfig) -> RecommendationResponse:
    """Generate outfit recommendations. V1 returns mock data."""
    start = time.time()
    outfits = generate_outfits(config)
    elapsed = (time.time() - start) * 1000

    return RecommendationResponse(
        outfits=outfits,
        total_combinations=random.randint(50, 200),
        processing_time_ms=round(elapsed, 1),
    )

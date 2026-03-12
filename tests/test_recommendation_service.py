"""Tests for recommendation service."""
from services.recommendation_service import (
    generate_outfits, get_recommendations,
)
from models.schemas import RecommendationConfig, Occasion, ScoringProfile


class TestGenerateOutfits:
    def test_generates_requested_count(self):
        config = RecommendationConfig(top_k=3)
        outfits = generate_outfits(config)
        assert len(outfits) == 3

    def test_generates_max_available(self):
        config = RecommendationConfig(top_k=100)
        outfits = generate_outfits(config)
        assert len(outfits) == 10  # limited by OUTFIT_NAMES

    def test_outfits_are_ranked(self):
        config = RecommendationConfig(top_k=5)
        outfits = generate_outfits(config)
        for i, outfit in enumerate(outfits):
            assert outfit.rank == i + 1

    def test_outfits_sorted_by_score(self):
        config = RecommendationConfig(top_k=5)
        outfits = generate_outfits(config)
        scores = [o.score.overall for o in outfits]
        assert scores == sorted(scores, reverse=True)

    def test_outfit_has_garments(self):
        config = RecommendationConfig(top_k=1)
        outfits = generate_outfits(config)
        assert len(outfits[0].garments) == 3

    def test_outfit_has_valid_scores(self):
        config = RecommendationConfig(top_k=1)
        outfits = generate_outfits(config)
        score = outfits[0].score
        assert 0 <= score.overall <= 1
        assert 0 <= score.color_harmony <= 1
        assert 0 <= score.formality_match <= 1


class TestGetRecommendations:
    def test_returns_response_model(self):
        config = RecommendationConfig(top_k=2, occasion=Occasion.CASUAL)
        response = get_recommendations(config)
        assert len(response.outfits) == 2
        assert response.total_combinations > 0
        assert response.processing_time_ms >= 0

    def test_with_scoring_profile(self):
        config = RecommendationConfig(
            top_k=3,
            scoring_profile=ScoringProfile.MINIMALIST,
        )
        response = get_recommendations(config)
        assert len(response.outfits) == 3

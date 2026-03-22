"""Pydantic models for AlgoStyle mobile API."""
from pydantic import BaseModel, Field
from typing import List, Optional, Dict, Any
from datetime import datetime, date
from enum import Enum
import uuid


# ─── Enums ────────────────────────────────────────────────────

class UserRole(str, Enum):
    USER = "user"
    STYLIST = "stylist"
    BUSINESS = "business"


class GarmentCategory(str, Enum):
    TOP = "top"
    BOTTOM = "bottom"
    DRESS = "dress"
    OUTERWEAR = "outerwear"
    SHOES = "shoes"
    ACCESSORY = "accessory"


class Occasion(str, Enum):
    CASUAL = "casual"
    BUSINESS = "business"
    FORMAL = "formal"
    DATE = "date"
    PARTY = "party"
    WEDDING = "wedding"
    INTERVIEW = "interview"
    SPORT = "sport"
    TRAVEL = "travel"
    BEACH = "beach"


class StylePreference(str, Enum):
    MINIMALIST = "minimalist"
    CLASSIC = "classic"
    STREETWEAR = "streetwear"
    BOHEMIAN = "bohemian"
    PREPPY = "preppy"
    EDGY = "edgy"
    ROMANTIC = "romantic"


class ScoringProfile(str, Enum):
    DEFAULT = "default"
    MINIMALIST = "minimalist"
    CREATIVE = "creative"
    BUSINESS = "business"
    CASUAL = "casual"


# ─── Auth Models ──────────────────────────────────────────────

class RegisterRequest(BaseModel):
    email: str
    password: str
    name: str
    role: UserRole = UserRole.USER


class LoginRequest(BaseModel):
    email: str
    password: str


class AuthResponse(BaseModel):
    user_id: str
    token: str
    name: str
    email: str
    role: UserRole
    is_onboarded: bool = False


# ─── User Profile / Onboarding ───────────────────────────────

class BodyAnalysis(BaseModel):
    body_shape: Optional[str] = None  # hourglass, pear, apple, rectangle, inverted_triangle
    skin_tone: Optional[str] = None   # fair, light, medium, olive, tan, brown, deep
    undertone: Optional[str] = None   # warm, cool, neutral
    hair_color: Optional[str] = None
    contrast_level: Optional[str] = None  # low, medium, high
    estimated_top_size: Optional[str] = None
    estimated_bottom_size: Optional[str] = None


class UserProfile(BaseModel):
    user_id: str
    name: str
    email: str
    role: UserRole = UserRole.USER

    # Physical
    height_cm: Optional[float] = None
    weight_kg: Optional[float] = None
    gender: Optional[str] = None  # homme, femme, non-genre
    body_analysis: Optional[BodyAnalysis] = None
    profile_photo_url: Optional[str] = None

    # Style preferences
    style_preferences: List[str] = Field(default_factory=list)
    favorite_colors: List[str] = Field(default_factory=list)
    avoid_colors: List[str] = Field(default_factory=list)
    comfort_vs_style: int = Field(default=50, ge=0, le=100)  # 0=comfort, 100=style
    budget: Optional[str] = None  # low, medium, high, luxury

    # Location
    location: Optional[str] = None
    timezone: Optional[str] = None

    is_onboarded: bool = False
    created_at: datetime = Field(default_factory=datetime.utcnow)


class OnboardingUpdate(BaseModel):
    height_cm: Optional[float] = None
    weight_kg: Optional[float] = None
    gender: Optional[str] = None
    style_preferences: Optional[List[str]] = None
    favorite_colors: Optional[List[str]] = None
    avoid_colors: Optional[List[str]] = None
    comfort_vs_style: Optional[int] = None
    budget: Optional[str] = None
    location: Optional[str] = None


# ─── Wardrobe Models ─────────────────────────────────────────

class GarmentAttributes(BaseModel):
    category: GarmentCategory
    subcategory: Optional[str] = None
    color_primary: str = "unknown"
    color_hex: Optional[str] = None
    color_secondary: Optional[str] = None
    pattern: str = "solid"
    material: Optional[str] = None
    formality: str = "casual"
    seasons: List[str] = Field(default_factory=lambda: ["spring", "summer", "fall", "winter"])
    confidence: float = 0.0


class GarmentItem(BaseModel):
    id: str = Field(default_factory=lambda: f"g_{uuid.uuid4().hex[:10]}")
    user_id: str
    image_url: Optional[str] = None
    attributes: GarmentAttributes
    is_favorite: bool = False
    for_sale: bool = False
    tags: List[str] = Field(default_factory=list)
    times_worn: int = 0
    last_worn: Optional[datetime] = None
    # Cost-per-wear
    purchase_price: Optional[float] = None
    worn_count: int = 0
    # LLM-native attributes stored verbatim — used by llm_client to skip re-mapping
    llm_attributes: Optional[dict] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)


class WardrobeFilters(BaseModel):
    category: Optional[GarmentCategory] = None
    color: Optional[str] = None
    season: Optional[str] = None
    formality: Optional[str] = None
    pattern: Optional[str] = None
    material: Optional[str] = None
    is_favorite: Optional[bool] = None
    for_sale: Optional[bool] = None
    search: Optional[str] = None


# ─── Garment Image Extraction ────────────────────────────────

class ExtractionWarning(BaseModel):
    """A quality or confidence warning raised during garment extraction."""
    code: str           # e.g. "low_confidence", "multiple_garments", "poor_lighting"
    severity: str       # "info" | "warning" | "error"
    message: str        # Human-readable message shown to the user


class GarmentExtractionResult(BaseModel):
    """Result of analyzing a photo to extract a garment."""
    # Extracted attributes (best-guess)
    attributes: GarmentAttributes
    # Quality / validation warnings
    warnings: List[ExtractionWarning] = Field(default_factory=list)
    # Whether the pipeline is confident enough to auto-confirm
    auto_confirm: bool = False
    # Number of garments detected (>1 means outfit photo)
    garments_detected: int = 1
    # Overall extraction confidence 0-1
    confidence: float = 0.0
    # Base64-encoded cropped garment image (optional — for preview)
    cropped_image_b64: Optional[str] = None
    # LLM-native attributes dict — stored verbatim so add_garment() can
    # persist it in the llm_attributes column, skipping re-conversion later.
    llm_attributes: Optional[dict] = None


# ─── Recommendation Models ───────────────────────────────────

class RecommendationConfig(BaseModel):
    garment_ids: Optional[List[str]] = None  # None = use all wardrobe
    occasion: Optional[Occasion] = None
    weather_temp: Optional[float] = None
    weather_condition: Optional[str] = None
    time_of_day: Optional[str] = None
    scoring_profile: ScoringProfile = ScoringProfile.DEFAULT
    no_repeat_weeks: int = 2
    top_k: int = 3


class OutfitScore(BaseModel):
    overall: float = 0.0
    color_harmony: float = 0.0
    formality_match: float = 0.0
    occasion_fit: float = 0.0
    pattern_mixing: float = 0.0
    proportion: float = 0.0
    season_fit: float = 0.0
    creativity: float = 0.0


class OutfitResult(BaseModel):
    id: str = Field(default_factory=lambda: f"outfit_{uuid.uuid4().hex[:10]}")
    rank: int
    name: str
    grade: Optional[str] = None
    garments: List[GarmentItem]
    score: OutfitScore
    explanation_brief: Optional[str] = None
    explanation_detailed: Optional[str] = None
    catalogue_image_url: Optional[str] = None


class RecommendationResponse(BaseModel):
    outfits: List[OutfitResult]
    total_combinations: int = 0
    processing_time_ms: float = 0.0


# ─── Chat Models ─────────────────────────────────────────────

class ChatMessage(BaseModel):
    session_id: str
    message: str
    user_id: Optional[str] = None


class ChatResponse(BaseModel):
    session_id: str
    response: str
    suggestions: List[str] = Field(default_factory=list)


# ─── Try-On Models ───────────────────────────────────────────

class TryOnRequest(BaseModel):
    user_photo_url: Optional[str] = None  # or use profile photo
    garment_ids: List[str]
    backend: str = "catvton"  # catvton or replicate


class TryOnResponse(BaseModel):
    result_image_url: Optional[str] = None
    result_image_b64: Optional[str] = None
    processing_time_ms: float = 0.0


# ─── Reminder & Planning Models ─────────────────────────────

class ReminderType(str, Enum):
    NONE  = "none"
    PUSH  = "push"
    EMAIL = "email"


class ReminderSetting(BaseModel):
    """Per-outfit reminder configuration."""
    type: ReminderType = ReminderType.NONE
    minutes_before: int = Field(default=60, ge=0, le=10080)  # up to 1 week


# ─── Custom Outfit Models ────────────────────────────────────

class CreateCustomOutfitRequest(BaseModel):
    name: str
    description: Optional[str] = None
    garment_ids: List[str]
    is_public: bool = False
    # Planning fields (optional)
    planned_date: Optional[datetime] = None       # ISO-8601 with TZ preferred
    reminder: Optional[ReminderSetting] = None
    user_timezone: Optional[str] = None           # IANA e.g. "Europe/Paris"
    source: str = "build"                         # build | ai | score | prompt
    # AI scoring (carried from HybridOutfitRecommender)
    ai_grade: Optional[str] = None
    ai_score: Optional[float] = None
    explanation_brief: Optional[str] = None
    explanation_detailed: Optional[str] = None


class UpdateOutfitPlanRequest(BaseModel):
    """Patch just the planning fields on an existing outfit."""
    planned_date: Optional[datetime] = None
    reminder: Optional[ReminderSetting] = None
    user_timezone: Optional[str] = None
    name: Optional[str] = None
    description: Optional[str] = None


class CustomOutfit(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    name: str
    description: Optional[str] = None
    garments: List[GarmentItem]
    is_public: bool = False
    # Planning
    planned_date: Optional[datetime] = None
    reminder: Optional[ReminderSetting] = None
    user_timezone: Optional[str] = None
    source: str = "build"
    # AI scoring
    ai_grade: Optional[str] = None
    ai_score: Optional[float] = None
    explanation_brief: Optional[str] = None
    explanation_detailed: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.now)
    updated_at: datetime = Field(default_factory=datetime.now)


class CustomOutfitResponse(BaseModel):
    success: bool
    outfit: Optional[CustomOutfit] = None
    message: Optional[str] = None


# ─── Image Consulting Models ─────────────────────────────────

class ColorPaletteRecommendation(BaseModel):
    """Best, good, and avoid colors based on user's coloring."""
    best_colors: List[str] = Field(default_factory=list)
    good_colors: List[str] = Field(default_factory=list)
    colors_to_avoid: List[str] = Field(default_factory=list)
    neutral_colors: List[str] = Field(default_factory=list)
    accent_colors: List[str] = Field(default_factory=list)
    tips: List[str] = Field(default_factory=list)


class BodyShapeGuidance(BaseModel):
    """Style guidance based on body shape."""
    body_shape: str
    flattering_silhouettes: List[str] = Field(default_factory=list)
    good_patterns: List[str] = Field(default_factory=list)
    items_to_avoid: List[str] = Field(default_factory=list)
    styling_tips: List[str] = Field(default_factory=list)
    proportion_tips: List[str] = Field(default_factory=list)


class FaceShapeGuidance(BaseModel):
    """Neckline / collar / accessory guidance based on face shape."""
    face_shape: str
    flattering_necklines: List[str] = Field(default_factory=list)
    flattering_collars: List[str] = Field(default_factory=list)
    earring_styles: List[str] = Field(default_factory=list)
    glasses_styles: List[str] = Field(default_factory=list)
    tips: List[str] = Field(default_factory=list)


class ImageConsultingResult(BaseModel):
    """Full image consulting analysis result."""
    # Analysis IDs
    user_id: str
    analyzed_at: datetime = Field(default_factory=datetime.utcnow)

    # Core profile data
    body_shape: Optional[str] = None        # hourglass, pear, apple, rectangle, inverted_triangle, athletic
    face_shape: Optional[str] = None        # oval, round, square, heart, diamond, rectangle
    skin_tone: Optional[str] = None         # very_light → deep
    undertone: Optional[str] = None         # warm, cool, neutral
    hair_color: Optional[str] = None
    contrast_level: Optional[str] = None    # low, medium, high, very_high
    visual_weight: Optional[str] = None     # light, medium, heavy …
    color_season: Optional[str] = None      # Spring, Summer, Autumn, Winter

    # Estimated sizes (if height / weight provided)
    estimated_top_size: Optional[str] = None
    estimated_bottom_size: Optional[str] = None

    # Detailed recommendations
    color_palette: Optional[ColorPaletteRecommendation] = None
    body_shape_guidance: Optional[BodyShapeGuidance] = None
    face_shape_guidance: Optional[FaceShapeGuidance] = None

    # Summary blurb generated by LLM or rules
    summary: Optional[str] = None

    # Confidence 0-1
    overall_confidence: float = 0.0


class ImageConsultingRequest(BaseModel):
    """Request to run or re-run image consulting."""
    height_cm: Optional[float] = None
    weight_kg: Optional[float] = None


# ─── LLM Explain Models ───────────────────────────────────────

class ExplainOutfitRequest(BaseModel):
    outfit: OutfitResult
    occasion: Optional[Occasion] = None
    scoring_profile: Optional[ScoringProfile] = None
    detail_level: str = "standard"   # "brief" | "standard" | "detailed"


class ExplainOutfitResponse(BaseModel):
    outfit_id: str
    detailed: Optional[str] = None
    style_notes: List[str] = Field(default_factory=list)
    color_note: Optional[str] = None
    occasion_note: Optional[str] = None
    styling_tips: List[str] = Field(default_factory=list)


# ─── Wardrobe Insights Models ─────────────────────────────────

class GapItem(BaseModel):
    """A single wardrobe gap (missing foundation piece)."""
    gap_type: str                  # category_missing, color_imbalance, formality_gap, season_gap
    severity: str                  # low, medium, high
    description: str
    recommendation: str


class OccasionCoverageItem(BaseModel):
    """Coverage for a single occasion."""
    occasion: str
    coverage_score: float          # 0-1
    suitable_items_count: int
    missing_categories: List[str] = Field(default_factory=list)
    suggestion: Optional[str] = None


class VersatilityItem(BaseModel):
    """Versatility stats for a single garment."""
    garment_id: str
    garment_description: str
    versatility_score: float       # 0-1
    compatible_outfit_count: int
    compatible_categories: List[str] = Field(default_factory=list)
    compatible_occasions: List[str] = Field(default_factory=list)


class DuplicateGroup(BaseModel):
    """A group of near-identical garments."""
    garment_ids: List[str]
    descriptions: List[str]
    shared_category: str
    shared_color: str
    shared_pattern: str
    similarity_score: float        # 0-1
    recommendation: str            # e.g. "Keep the most versatile, consider selling the others"


class CostPerWearItem(BaseModel):
    """Cost-per-wear ranking for a single garment."""
    garment_id: str
    garment_description: str
    purchase_price: float
    worn_count: int
    cost_per_wear: float           # purchase_price / max(worn_count, 1)
    value_tier: str                # excellent, good, fair, poor, unworn


class WardrobeInsightsResponse(BaseModel):
    """Full wardrobe insights — all 5 features in one response."""
    # 1. Capsule gap analysis
    gaps: List[GapItem] = Field(default_factory=list)
    # 2. Cost-per-wear
    cost_per_wear: List[CostPerWearItem] = Field(default_factory=list)
    has_price_data: bool = False   # false when no garments have purchase_price
    # 3. Duplicate detection
    duplicate_groups: List[DuplicateGroup] = Field(default_factory=list)
    total_duplicates: int = 0
    # 4. Occasion coverage
    occasion_coverage: List[OccasionCoverageItem] = Field(default_factory=list)
    overall_coverage_score: float = 0.0
    # 5. Versatility ranking
    versatility_ranking: List[VersatilityItem] = Field(default_factory=list)
    # Summary
    overall_score: float = 0.0
    summary: str = ""
    cached: bool = False

"""
SQLAlchemy ORM models for AlgoStyle database.
All entities persist in PostgreSQL.
"""
from sqlalchemy import Column, String, Integer, Float, Boolean, DateTime, Text, JSON, ForeignKey, ARRAY
from sqlalchemy.orm import relationship
from datetime import datetime
import uuid

from db import Base


class User(Base):
    """User account - authentication and identity."""
    __tablename__ = "users"

    id = Column(String, primary_key=True, default=lambda: f"user_{uuid.uuid4().hex[:12]}")
    email = Column(String, unique=True, nullable=False, index=True)
    password_hash = Column(String, nullable=False)
    name = Column(String, nullable=False)
    role = Column(String, default="user")  # user, stylist, business
    is_onboarded = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    profile = relationship("UserProfile", back_populates="user", uselist=False, cascade="all, delete-orphan")
    garments = relationship("GarmentItem", back_populates="user", cascade="all, delete-orphan")
    custom_outfits = relationship("CustomOutfit", back_populates="user", cascade="all, delete-orphan")
    chat_sessions = relationship("ChatSession", back_populates="user", cascade="all, delete-orphan")
    consulting_results = relationship("ImageConsultingResult", back_populates="user", cascade="all, delete-orphan")
    tokens = relationship("UserToken", back_populates="user", cascade="all, delete-orphan")


class UserToken(Base):
    """Authentication tokens - one-to-many relationship."""
    __tablename__ = "user_tokens"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id = Column(String, ForeignKey("users.id"), nullable=False, index=True)
    token = Column(String, unique=True, nullable=False, index=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    expires_at = Column(DateTime, nullable=True)

    user = relationship("User", back_populates="tokens")


class UserProfile(Base):
    """User profile and preferences - onboarding data."""
    __tablename__ = "user_profiles"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id = Column(String, ForeignKey("users.id"), unique=True, nullable=False)
    
    # Physical attributes
    height_cm = Column(Float, nullable=True)
    weight_kg = Column(Float, nullable=True)
    gender = Column(String, nullable=True)  # homme, femme, non-genre
    
    # Body analysis
    body_shape = Column(String, nullable=True)  # hourglass, pear, apple, etc.
    skin_tone = Column(String, nullable=True)
    undertone = Column(String, nullable=True)  # warm, cool, neutral
    hair_color = Column(String, nullable=True)
    contrast_level = Column(String, nullable=True)  # low, medium, high
    estimated_top_size = Column(String, nullable=True)
    estimated_bottom_size = Column(String, nullable=True)
    profile_photo_url = Column(String, nullable=True)
    
    # Style preferences
    style_preferences = Column(ARRAY(String), default=[])
    favorite_colors = Column(ARRAY(String), default=[])
    avoid_colors = Column(ARRAY(String), default=[])
    comfort_vs_style = Column(Integer, default=50)  # 0=comfort, 100=style
    budget = Column(String, nullable=True)  # low, medium, high, luxury
    
    # Location
    location = Column(String, nullable=True)
    timezone = Column(String, nullable=True)
    
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    user = relationship("User", back_populates="profile")


class GarmentItem(Base):
    """Wardrobe item - clothing piece."""
    __tablename__ = "garment_items"

    id = Column(String, primary_key=True, default=lambda: f"g_{uuid.uuid4().hex[:10]}")
    user_id = Column(String, ForeignKey("users.id"), nullable=False, index=True)
    
    # Image
    image_url = Column(String, nullable=True)
    
    # Attributes (as JSON for flexibility)
    category = Column(String, nullable=False)  # top, bottom, dress, outerwear, shoes, accessory
    subcategory = Column(String, nullable=True)
    color_primary = Column(String, default="unknown")
    color_hex = Column(String, nullable=True)
    color_secondary = Column(String, nullable=True)
    pattern = Column(String, default="solid")
    material = Column(String, nullable=True)
    formality = Column(String, default="casual")
    seasons = Column(ARRAY(String), default=["spring", "summer", "fall", "winter"])
    confidence = Column(Float, default=0.0)
    
    # Metadata
    is_favorite = Column(Boolean, default=False)
    for_sale = Column(Boolean, default=False)
    tags = Column(ARRAY(String), default=[])
    times_worn = Column(Integer, default=0)
    last_worn = Column(DateTime, nullable=True)

    # Cost-per-wear tracking
    purchase_price = Column(Float, nullable=True)   # optional, entered by user
    worn_count = Column(Integer, default=0)          # incremented when outfit is worn

    # LLM-native attributes — stored verbatim from LLM_project /analyze/image response.
    # Shape: { category, subcategory, color: {primary, secondary, hex_codes},
    #          pattern: {type}, material: {primary}, formality_level,
    #          confidence_score, season_suitable }
    # When present, llm_client._algogarment_to_llm() uses this directly,
    # eliminating the flat-column → nested-object conversion.
    llm_attributes = Column(JSON, nullable=True)

    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    user = relationship("User", back_populates="garments")


class WardrobeAnalysisCache(Base):
    """Persisted wardrobe analysis cache — one row per user.

    is_dirty=True means the wardrobe changed since the last LLM run.
    get_wardrobe_insights() skips the LLM call when is_dirty=False and
    result_json is populated, returning the stored JSON directly.
    Mark dirty by calling mark_wardrobe_dirty(user_id) whenever a garment
    is added, updated, or deleted.
    """
    __tablename__ = "wardrobe_analysis_cache"

    user_id     = Column(String, ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    result_json = Column(JSON, nullable=True)   # full WardrobeInsightsResponse dict
    is_dirty    = Column(Boolean, default=True) # True → recompute on next read
    cached_at   = Column(DateTime, nullable=True)
    updated_at  = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class CustomOutfit(Base):
    """Custom outfit created by user — may have a planned_date, reminder and AI score."""
    __tablename__ = "custom_outfits"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id = Column(String, ForeignKey("users.id"), nullable=False, index=True)

    name = Column(String, nullable=False)
    description = Column(Text, nullable=True)

    # Garment IDs as array
    garment_ids = Column(ARRAY(String), default=[])

    # ── Planning fields ────────────────────────────────────────
    # ISO-8601 datetime (with TZ) when the user plans to wear this outfit
    planned_date = Column(DateTime(timezone=True), nullable=True, index=True)
    # Reminder: JSON blob {"type": "push"|"email"|"none", "minutes_before": 60}
    reminder_setting = Column(JSON, nullable=True)
    # IANA timezone of the user at planning time, e.g. "Europe/Paris"
    user_timezone = Column(String(64), nullable=True)

    # ── Source ────────────────────────────────────────────────
    # How the outfit was created: build | ai | score | prompt
    source = Column(String(32), default="build")

    # ── AI scoring fields ─────────────────────────────────────
    ai_grade = Column(String(4), nullable=True)
    ai_score = Column(Float, nullable=True)
    explanation_brief = Column(Text, nullable=True)
    explanation_detailed = Column(Text, nullable=True)

    is_public = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    user = relationship("User", back_populates="custom_outfits")


class ChatSession(Base):
    """Chat session for styling advice."""
    __tablename__ = "chat_sessions"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id = Column(String, ForeignKey("users.id"), nullable=False, index=True)
    
    # Messages as JSON array for flexibility
    messages = Column(JSON, default=[])
    
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    user = relationship("User", back_populates="chat_sessions")


class ImageConsultingResult(Base):
    """Image consulting analysis results."""
    __tablename__ = "image_consulting_results"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id = Column(String, ForeignKey("users.id"), nullable=False, index=True)
    
    # Core profile data
    body_shape = Column(String, nullable=True)
    face_shape = Column(String, nullable=True)
    skin_tone = Column(String, nullable=True)
    undertone = Column(String, nullable=True)
    hair_color = Column(String, nullable=True)
    contrast_level = Column(String, nullable=True)
    visual_weight = Column(String, nullable=True)
    color_season = Column(String, nullable=True)
    
    # Estimated sizes
    estimated_top_size = Column(String, nullable=True)
    estimated_bottom_size = Column(String, nullable=True)
    
    # Detailed recommendations (as JSON)
    color_palette = Column(JSON, nullable=True)
    body_shape_guidance = Column(JSON, nullable=True)
    face_shape_guidance = Column(JSON, nullable=True)
    
    # Summary
    summary = Column(Text, nullable=True)
    overall_confidence = Column(Float, default=0.0)
    
    analyzed_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    user = relationship("User", back_populates="consulting_results")

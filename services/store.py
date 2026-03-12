"""
In-memory data stores shared across services.
In production, replace with a proper database (PostgreSQL, MongoDB, etc.).
"""
from models.schemas import UserProfile, GarmentItem, CustomOutfit

# Auth stores
users: dict[str, dict] = {}          # user_id → user data
tokens: dict[str, str] = {}          # token  → user_id
email_index: dict[str, str] = {}     # email  → user_id

# Profile store
profiles: dict[str, UserProfile] = {}

# Wardrobe store: user_id → list of garments
wardrobes: dict[str, list[GarmentItem]] = {}

# Custom outfit store: user_id → { outfit_id → outfit }
custom_outfits: dict[str, dict[str, CustomOutfit]] = {}

# Chat session store: session_id → list of messages
chat_sessions: dict[str, list[dict]] = {}

# Image consulting cache: user_id → result
consulting_cache: dict[str, object] = {}

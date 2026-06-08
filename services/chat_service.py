"""
Chat service — conversational style assistant logic.
Uses PostgreSQL database for persistence and Google Gemini for AI responses.
"""
import random
import logging
from typing import List, Optional
import google.generativeai as genai

from models.schemas import ChatResponse
from models.database import ChatSession, User, UserProfile as UserProfileDB, GarmentItem as GarmentDB
from db import get_db_context
from config import settings

logger = logging.getLogger(__name__)

# Configure Gemini
try:
    genai.configure(api_key=settings.google_api_key)
    GEMINI_MODEL = genai.GenerativeModel('gemini-1.5-flash')
    GEMINI_AVAILABLE = True
except Exception as e:
    logger.warning(f"Gemini not configured: {e}")
    GEMINI_AVAILABLE = False


# ── System prompt for the AI Style Assistant ────────────────────────────────

SYSTEM_PROMPT = """You are an expert AI style assistant for AlgoStyle, a personal wardrobe and fashion app. Your role is to help users:

1. **Style Advice**: Provide personalized fashion recommendations based on their wardrobe, body type, coloring, and preferences
2. **Outfit Creation**: Suggest outfit combinations from their existing wardrobe items
3. **Shopping Guidance**: Recommend what pieces to add to complete their wardrobe
4. **Trend Insights**: Share current fashion trends and how to adapt them to the user's style
5. **Occasion Dressing**: Help them dress appropriately for specific events or weather
6. **Color & Pattern Matching**: Guide them on complementary colors and pattern mixing

**Your tone**: Friendly, encouraging, and knowledgeable — like a stylish friend who genuinely wants to help. Use emojis sparingly for warmth.

**Keep responses**:
- Concise (2-4 sentences typically)
- Actionable and specific
- Personalized when you have user context
- Encouraging and positive

**When you don't have specific user data** (wardrobe, profile, etc.), provide general style advice and suggest they complete their profile or add wardrobe items for more personalized recommendations.
"""


MOCK_RESPONSES = [
    "Great question! Based on your wardrobe, I'd suggest pairing your navy blazer with the white oxford shirt and dark chinos. The combination creates a polished smart-casual look perfect for the occasion.",
    "For a date night, consider your black dress with the statement earrings. Add the leather clutch for a touch of elegance. The monochromatic palette is always a winning choice!",
    "To make this look more casual, try rolling up the sleeves and swapping the loafers for clean white sneakers. It instantly relaxes the outfit while keeping it put-together.",
    "A belt in a complementary color would tie the whole outfit together. Try the tan leather belt — it echoes the shoe color and adds definition to the waistline.",
    "Based on your body type, A-line silhouettes and V-necks will flatter you most. The wrap dress in your wardrobe is actually perfect for this!",
]

SUGGESTION_SETS: List[List[str]] = [
    ["What accessories would work?", "Make it more formal", "Try a different color palette"],
    ["Show me similar outfits", "What shoes go with this?", "Is this weather-appropriate?"],
    ["What's trending this season?", "Build a capsule wardrobe", "Help with color matching"],
]


def create_session(user_id: str = "") -> str:
    """Create a new chat session and return the session ID."""
    with get_db_context() as db:
        session = ChatSession(user_id=user_id if user_id else None, messages=[])
        db.add(session)
        db.commit()
        return session.id


def process_message(session_id: str, message: str) -> ChatResponse:
    """Process a user message and generate a response."""
    with get_db_context() as db:
        session = db.query(ChatSession).filter(ChatSession.id == session_id).first()
        if not session:
            raise Exception("Session not found")
        
        messages = list(session.messages) if session.messages else []
        messages.append({"role": "user", "content": message})
        
        # V1: Mock response. Production: calls ConversationHandler / LLM
        response_text = random.choice(MOCK_RESPONSES)
        suggestions = random.choice(SUGGESTION_SETS)
        
        messages.append({"role": "assistant", "content": response_text})
        session.messages = messages
        
        db.commit()
        
        return ChatResponse(
            session_id=session_id,
            response=response_text,
            suggestions=suggestions,
        )


def get_history(session_id: str) -> dict:
    """Get chat history for a session."""
    with get_db_context() as db:
        session = db.query(ChatSession).filter(ChatSession.id == session_id).first()
        if not session:
            raise Exception("Session not found")
        
        return {
            "session_id": session_id,
            "messages": session.messages if session.messages else [],
        }


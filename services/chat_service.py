"""
Chat service — conversational style assistant logic.
"""
import uuid
import random
from typing import List

from models.schemas import ChatResponse
from services.store import chat_sessions


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
    sid = f"chat_{uuid.uuid4().hex[:10]}"
    chat_sessions[sid] = []
    return sid


def process_message(session_id: str, message: str) -> ChatResponse:
    """Process a user message and generate a response."""
    history = chat_sessions.setdefault(session_id, [])
    history.append({"role": "user", "content": message})

    # V1: Mock response. Production: calls ConversationHandler / LLM
    response_text = random.choice(MOCK_RESPONSES)
    suggestions = random.choice(SUGGESTION_SETS)

    history.append({"role": "assistant", "content": response_text})

    return ChatResponse(
        session_id=session_id,
        response=response_text,
        suggestions=suggestions,
    )


def get_history(session_id: str) -> dict:
    """Get chat history for a session."""
    return {
        "session_id": session_id,
        "messages": chat_sessions.get(session_id, []),
    }

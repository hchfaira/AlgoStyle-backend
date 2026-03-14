"""
Chat service — conversational style assistant logic.
Uses PostgreSQL database for persistence.
"""
import random
from typing import List

from models.schemas import ChatResponse
from models.database import ChatSession
from db import get_db_context


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
        
        messages = session.messages if session.messages else []
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


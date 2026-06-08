"""
Chat routes — Conversational style assistant.
Delegates to services.chat_service.
"""
from fastapi import APIRouter
from models.schemas import ChatMessage, ChatResponse
from services import chat_service
from deps import CurrentUser

router = APIRouter()


@router.post("/start-session")
async def start_session(current_user: CurrentUser):
    sid = chat_service.create_session(current_user["user_id"])
    return {"session_id": sid}


@router.post("/message", response_model=ChatResponse)
async def send_message(current_user: CurrentUser, msg: ChatMessage):
    return chat_service.process_message(msg.session_id, msg.message)


@router.get("/history/{session_id}")
async def get_history(current_user: CurrentUser, session_id: str):
    return chat_service.get_history(session_id)

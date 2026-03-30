"""
Notification routes — list, unread count, mark read.
"""
from fastapi import APIRouter, Query
from models.schemas import NotificationListResponse, UnreadCountResponse
from services import notification_service

router = APIRouter(prefix="/api/v1/notifications", tags=["Notifications"])


@router.get("", response_model=NotificationListResponse)
async def list_notifications(
    user_id: str = Query(...),
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
):
    return notification_service.get_notifications(user_id, limit=limit, offset=offset)


@router.get("/unread-count", response_model=UnreadCountResponse)
async def unread_count(user_id: str = Query(...)):
    return notification_service.get_unread_count(user_id)


@router.post("/mark-all-read")
async def mark_all_read(user_id: str = Query(...)):
    notification_service.mark_all_read(user_id)
    return {"status": "ok"}


@router.post("/{notification_id}/read")
async def mark_read(notification_id: str, user_id: str = Query(...)):
    notification_service.mark_read(user_id, notification_id)
    return {"status": "ok"}

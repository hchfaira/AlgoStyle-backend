"""
Notification service — create and query in-app notifications.
"""
import logging

from sqlalchemy import func

from models.database import Notification, User as UserDB
from models.schemas import NotificationItem, NotificationListResponse, UnreadCountResponse
from db import get_db_context

logger = logging.getLogger(__name__)


def create_notification(
    user_id: str,
    type: str,
    actor_id: str,
    content: str,
    outfit_id: str | None = None,
) -> None:
    """Insert a notification row. Silently skips if actor == recipient."""
    if user_id == actor_id:
        return
    try:
        with get_db_context() as db:
            db.add(Notification(
                user_id=user_id,
                type=type,
                actor_id=actor_id,
                outfit_id=outfit_id,
                content=content,
            ))
            db.commit()
    except Exception:
        logger.exception("Failed to create notification")


def get_notifications(user_id: str, limit: int = 50, offset: int = 0) -> NotificationListResponse:
    """Return paginated notifications for a user, newest first."""
    with get_db_context() as db:
        query = (
            db.query(Notification, UserDB)
            .outerjoin(UserDB, Notification.actor_id == UserDB.id)
            .filter(Notification.user_id == user_id)
            .order_by(Notification.created_at.desc())
        )
        total = query.count()
        rows = query.offset(offset).limit(limit).all()

        unread = (
            db.query(func.count(Notification.id))
            .filter(Notification.user_id == user_id, Notification.is_read == False)  # noqa: E712
            .scalar()
        ) or 0

        items = [
            NotificationItem(
                id=n.id,
                type=n.type,
                actor_id=n.actor_id,
                actor_name=u.name if u else None,
                outfit_id=n.outfit_id,
                content=n.content,
                is_read=n.is_read,
                created_at=n.created_at,
            )
            for n, u in rows
        ]
        return NotificationListResponse(notifications=items, total=total, unread_count=unread)


def get_unread_count(user_id: str) -> UnreadCountResponse:
    with get_db_context() as db:
        count = (
            db.query(func.count(Notification.id))
            .filter(Notification.user_id == user_id, Notification.is_read == False)  # noqa: E712
            .scalar()
        ) or 0
        return UnreadCountResponse(unread_count=count)


def mark_all_read(user_id: str) -> None:
    with get_db_context() as db:
        db.query(Notification).filter(
            Notification.user_id == user_id,
            Notification.is_read == False,  # noqa: E712
        ).update({"is_read": True})
        db.commit()


def mark_read(user_id: str, notification_id: str) -> None:
    with get_db_context() as db:
        db.query(Notification).filter(
            Notification.id == notification_id,
            Notification.user_id == user_id,
        ).update({"is_read": True})
        db.commit()

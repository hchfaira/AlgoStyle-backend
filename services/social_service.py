import logging

from fastapi import HTTPException
from sqlalchemy import func

from models.database import (
    CustomOutfit as CustomOutfitDB,
    GarmentItem as GarmentItemDB,
    OutfitLike,
    OutfitSave,
    User as UserDB,
)
from models.schemas import (
    SocialFeedPost,
    SocialFeedItem,
    SocialFeedResponse,
    ToggleLikeResponse,
    ToggleSaveResponse,
    UserStats,
)
from db import get_db_context
from services import neo4j_service

logger = logging.getLogger(__name__)


def get_user_stats(user_id: str) -> UserStats:
    with get_db_context() as db:
        outfits_shared = (
            db.query(func.count(CustomOutfitDB.id))
            .filter(CustomOutfitDB.user_id == user_id, CustomOutfitDB.is_public == True)  # noqa: E712
            .scalar()
        ) or 0
        likes_received = (
            db.query(func.count(OutfitLike.id))
            .join(CustomOutfitDB, OutfitLike.outfit_id == CustomOutfitDB.id)
            .filter(CustomOutfitDB.user_id == user_id)
            .scalar()
        ) or 0
        return UserStats(outfits_shared=outfits_shared, likes_received=likes_received)


def get_feed(current_user_id: str, limit: int = 20, offset: int = 0) -> SocialFeedResponse:
    with get_db_context() as db:
        rows = (
            db.query(CustomOutfitDB, UserDB)
            .join(UserDB, CustomOutfitDB.user_id == UserDB.id)
            .filter(CustomOutfitDB.is_public == True)  # noqa: E712
            .order_by(CustomOutfitDB.created_at.desc())
            .offset(offset)
            .limit(limit)
            .all()
        )

        total_count = (
            db.query(func.count(CustomOutfitDB.id))
            .filter(CustomOutfitDB.is_public == True)  # noqa: E712
            .scalar()
        )

        if not rows:
            return SocialFeedResponse(posts=[], total=total_count or 0)

        outfit_ids = [o.id for o, _ in rows]

        all_garment_ids: list[str] = []
        for outfit, _ in rows:
            all_garment_ids.extend(outfit.garment_ids or [])

        # Fetch garment attributes from Neo4j when available.
        neo4j_map = neo4j_service.get_garments_by_ids(all_garment_ids) if all_garment_ids else {}
        # Always query PostgreSQL for ALL garment IDs: PG is the reliable source
        # for image_url (Neo4j may store an empty string for large base64 images,
        # or the upsert may have silently failed).
        pg_map: dict = {}
        if all_garment_ids:
            pg_garments = db.query(GarmentItemDB).filter(GarmentItemDB.id.in_(all_garment_ids)).all()
            pg_map = {g.id: g for g in pg_garments}

        def _resolve_garment(gid: str):
            neo = neo4j_map.get(gid)
            pg = pg_map.get(gid)
            if neo:
                return SocialFeedItem(
                    id=gid,
                    color=neo.get("color_hex") or "#888888",
                    color_name=(pg.color_primary if pg else None) or neo.get("color") or neo.get("color_primary"),
                    category=neo.get("category", ""),
                    subcategory=neo.get("subcategory"),
                    material=(pg.material if pg else None) or neo.get("material"),
                    # PG is the canonical store for image_url; fall back to Neo4j
                    image_url=(pg.image_url if pg else None) or neo.get("image_url") or None,
                )
            if pg:
                return SocialFeedItem(
                    id=gid,
                    color=pg.color_hex or "#888888",
                    color_name=pg.color_primary,
                    category=pg.category,
                    subcategory=pg.subcategory,
                    material=pg.material,
                    image_url=pg.image_url,
                )
            return None

        liked_ids = {
            r.outfit_id
            for r in db.query(OutfitLike.outfit_id)
            .filter(OutfitLike.outfit_id.in_(outfit_ids), OutfitLike.user_id == current_user_id)
            .all()
        }
        saved_ids = {
            r.outfit_id
            for r in db.query(OutfitSave.outfit_id)
            .filter(OutfitSave.outfit_id.in_(outfit_ids), OutfitSave.user_id == current_user_id)
            .all()
        }

        likes_counts = dict(
            db.query(OutfitLike.outfit_id, func.count(OutfitLike.id))
            .filter(OutfitLike.outfit_id.in_(outfit_ids))
            .group_by(OutfitLike.outfit_id)
            .all()
        )
        saves_counts = dict(
            db.query(OutfitSave.outfit_id, func.count(OutfitSave.id))
            .filter(OutfitSave.outfit_id.in_(outfit_ids))
            .group_by(OutfitSave.outfit_id)
            .all()
        )

        posts = []
        for outfit, user in rows:
            items = [
                item
                for gid in (outfit.garment_ids or [])
                if (item := _resolve_garment(gid)) is not None
            ]

            posts.append(
                SocialFeedPost(
                    id=outfit.id,
                    user_id=outfit.user_id,
                    user_name=user.name,
                    outfit_name=outfit.name,
                    description=outfit.description,
                    items=items,
                    ai_grade=outfit.ai_grade,
                    ai_score=outfit.ai_score,
                    source=outfit.source or "build",
                    likes=likes_counts.get(outfit.id, 0),
                    saves=saves_counts.get(outfit.id, 0),
                    liked=outfit.id in liked_ids,
                    saved=outfit.id in saved_ids,
                    created_at=outfit.created_at,
                )
            )

        return SocialFeedResponse(
            posts=posts,
            total=total_count or len(posts),
            has_more=(offset + limit) < (total_count or 0),
        )


def toggle_like(current_user_id: str, outfit_id: str) -> ToggleLikeResponse:
    with get_db_context() as db:
        outfit = (
            db.query(CustomOutfitDB)
            .filter(CustomOutfitDB.id == outfit_id, CustomOutfitDB.is_public == True)  # noqa: E712
            .first()
        )
        if not outfit:
            raise HTTPException(status_code=404, detail="Post not found")

        existing = (
            db.query(OutfitLike)
            .filter(OutfitLike.outfit_id == outfit_id, OutfitLike.user_id == current_user_id)
            .first()
        )
        if existing:
            db.delete(existing)
            liked = False
        else:
            db.add(OutfitLike(outfit_id=outfit_id, user_id=current_user_id))
            liked = True

        db.commit()
        likes = db.query(func.count(OutfitLike.id)).filter(OutfitLike.outfit_id == outfit_id).scalar() or 0
        return ToggleLikeResponse(liked=liked, likes=likes)


def toggle_save(current_user_id: str, outfit_id: str) -> ToggleSaveResponse:
    with get_db_context() as db:
        outfit = (
            db.query(CustomOutfitDB)
            .filter(CustomOutfitDB.id == outfit_id, CustomOutfitDB.is_public == True)  # noqa: E712
            .first()
        )
        if not outfit:
            raise HTTPException(status_code=404, detail="Post not found")

        existing = (
            db.query(OutfitSave)
            .filter(OutfitSave.outfit_id == outfit_id, OutfitSave.user_id == current_user_id)
            .first()
        )
        if existing:
            db.delete(existing)
            saved = False
        else:
            db.add(OutfitSave(outfit_id=outfit_id, user_id=current_user_id))
            saved = True

        db.commit()
        saves = db.query(func.count(OutfitSave.id)).filter(OutfitSave.outfit_id == outfit_id).scalar() or 0
        return ToggleSaveResponse(saved=saved, saves=saves)

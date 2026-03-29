import logging

from fastapi import HTTPException
from sqlalchemy import func

from models.database import (
    CustomOutfit as CustomOutfitDB,
    GarmentItem as GarmentItemDB,
    OutfitLike,
    OutfitSave,
    User as UserDB,
    UserFollow,
    UserProfile as UserProfileDB,
)
from models.schemas import (
    FollowCounts,
    FollowListResponse,
    FollowRequest as FollowRequestSchema,
    FollowRequestsResponse,
    FollowResponse,
    FollowUserSummary,
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
        # IDs of users the current user is following
        following_ids_sq = (
            db.query(UserFollow.following_id)
            .filter(UserFollow.follower_id == current_user_id, UserFollow.status == "accepted")
            .subquery()
        )

        rows = (
            db.query(CustomOutfitDB, UserDB)
            .join(UserDB, CustomOutfitDB.user_id == UserDB.id)
            .filter(
                CustomOutfitDB.is_public == True,  # noqa: E712
                CustomOutfitDB.user_id.in_(following_ids_sq),
            )
            .order_by(CustomOutfitDB.created_at.desc())
            .offset(offset)
            .limit(limit)
            .all()
        )

        total_count = (
            db.query(func.count(CustomOutfitDB.id))
            .filter(
                CustomOutfitDB.is_public == True,  # noqa: E712
                CustomOutfitDB.user_id.in_(following_ids_sq),
            )
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


# ─── Follow helpers ──────────────────────────────────────────

def _follow_counts(db, user_id: str) -> tuple[int, int]:
    """Return (followers_count, following_count) for a user."""
    followers = (
        db.query(func.count(UserFollow.id))
        .filter(UserFollow.following_id == user_id, UserFollow.status == "accepted")
        .scalar()
    ) or 0
    following = (
        db.query(func.count(UserFollow.id))
        .filter(UserFollow.follower_id == user_id, UserFollow.status == "accepted")
        .scalar()
    ) or 0
    return followers, following


def _user_handle(user: UserDB) -> str:
    return f"@{(user.name or user.id).lower().replace(' ', '_')[:16]}"


# ─── Follow / Unfollow ──────────────────────────────────────

def follow_user(current_user_id: str, target_user_id: str) -> FollowResponse:
    if current_user_id == target_user_id:
        raise HTTPException(status_code=400, detail="Cannot follow yourself")

    with get_db_context() as db:
        target = db.query(UserDB).filter(UserDB.id == target_user_id).first()
        if not target:
            raise HTTPException(status_code=404, detail="User not found")

        existing = (
            db.query(UserFollow)
            .filter(UserFollow.follower_id == current_user_id, UserFollow.following_id == target_user_id)
            .first()
        )
        if existing:
            followers_c, following_c = _follow_counts(db, target_user_id)
            return FollowResponse(status=existing.status, followers_count=followers_c, following_count=following_c)

        # Check if target account is private → pending, else accepted
        target_profile = db.query(UserProfileDB).filter(UserProfileDB.user_id == target_user_id).first()
        is_private = target_profile and target_profile.is_public is False
        status = "pending" if is_private else "accepted"

        db.add(UserFollow(follower_id=current_user_id, following_id=target_user_id, status=status))
        db.commit()

        followers_c, following_c = _follow_counts(db, target_user_id)
        return FollowResponse(status=status, followers_count=followers_c, following_count=following_c)


def unfollow_user(current_user_id: str, target_user_id: str) -> FollowResponse:
    with get_db_context() as db:
        existing = (
            db.query(UserFollow)
            .filter(UserFollow.follower_id == current_user_id, UserFollow.following_id == target_user_id)
            .first()
        )
        if existing:
            db.delete(existing)
            db.commit()

        followers_c, following_c = _follow_counts(db, target_user_id)
        return FollowResponse(status="unfollowed", followers_count=followers_c, following_count=following_c)


# ─── Followers / Following lists ─────────────────────────────

def get_followers(user_id: str, current_user_id: str) -> FollowListResponse:
    with get_db_context() as db:
        rows = (
            db.query(UserFollow, UserDB, UserProfileDB)
            .join(UserDB, UserFollow.follower_id == UserDB.id)
            .outerjoin(UserProfileDB, UserProfileDB.user_id == UserDB.id)
            .filter(UserFollow.following_id == user_id, UserFollow.status == "accepted")
            .all()
        )

        # IDs that current user is following
        my_following_ids = {
            r.following_id
            for r in db.query(UserFollow.following_id)
            .filter(UserFollow.follower_id == current_user_id, UserFollow.status == "accepted")
            .all()
        }

        users = [
            FollowUserSummary(
                user_id=u.id,
                name=u.name,
                handle=_user_handle(u),
                bio=p.bio if p else None,
                is_following=u.id in my_following_ids,
            )
            for _f, u, p in rows
        ]
        return FollowListResponse(users=users, total=len(users))


def get_following(user_id: str, current_user_id: str) -> FollowListResponse:
    with get_db_context() as db:
        rows = (
            db.query(UserFollow, UserDB, UserProfileDB)
            .join(UserDB, UserFollow.following_id == UserDB.id)
            .outerjoin(UserProfileDB, UserProfileDB.user_id == UserDB.id)
            .filter(UserFollow.follower_id == user_id, UserFollow.status == "accepted")
            .all()
        )

        my_following_ids = {
            r.following_id
            for r in db.query(UserFollow.following_id)
            .filter(UserFollow.follower_id == current_user_id, UserFollow.status == "accepted")
            .all()
        }

        users = [
            FollowUserSummary(
                user_id=u.id,
                name=u.name,
                handle=_user_handle(u),
                bio=p.bio if p else None,
                is_following=u.id in my_following_ids,
            )
            for _f, u, p in rows
        ]
        return FollowListResponse(users=users, total=len(users))


# ─── Follow Requests (for private accounts) ──────────────────

def get_follow_requests(user_id: str) -> FollowRequestsResponse:
    with get_db_context() as db:
        rows = (
            db.query(UserFollow, UserDB)
            .join(UserDB, UserFollow.follower_id == UserDB.id)
            .filter(UserFollow.following_id == user_id, UserFollow.status == "pending")
            .order_by(UserFollow.created_at.desc())
            .all()
        )

        # Count mutual connections for each requester
        requests = []
        for follow, user in rows:
            mutuals = (
                db.query(func.count(UserFollow.id))
                .filter(
                    UserFollow.follower_id == user.id,
                    UserFollow.status == "accepted",
                    UserFollow.following_id.in_(
                        db.query(UserFollow.follower_id)
                        .filter(UserFollow.following_id == user_id, UserFollow.status == "accepted")
                    ),
                )
                .scalar()
            ) or 0
            requests.append(
                FollowRequestSchema(
                    id=follow.id,
                    from_user_id=user.id,
                    name=user.name,
                    handle=_user_handle(user),
                    mutuals=mutuals,
                    created_at=follow.created_at,
                )
            )
        return FollowRequestsResponse(requests=requests, total=len(requests))


def accept_follow_request(user_id: str, request_id: str) -> FollowResponse:
    with get_db_context() as db:
        follow = (
            db.query(UserFollow)
            .filter(UserFollow.id == request_id, UserFollow.following_id == user_id, UserFollow.status == "pending")
            .first()
        )
        if not follow:
            raise HTTPException(status_code=404, detail="Follow request not found")

        follow.status = "accepted"
        db.commit()

        followers_c, following_c = _follow_counts(db, user_id)
        return FollowResponse(status="accepted", followers_count=followers_c, following_count=following_c)


def decline_follow_request(user_id: str, request_id: str) -> FollowResponse:
    with get_db_context() as db:
        follow = (
            db.query(UserFollow)
            .filter(UserFollow.id == request_id, UserFollow.following_id == user_id, UserFollow.status == "pending")
            .first()
        )
        if not follow:
            raise HTTPException(status_code=404, detail="Follow request not found")

        db.delete(follow)
        db.commit()

        followers_c, following_c = _follow_counts(db, user_id)
        return FollowResponse(status="declined", followers_count=followers_c, following_count=following_c)


def get_follow_counts(user_id: str) -> FollowCounts:
    with get_db_context() as db:
        followers_c, following_c = _follow_counts(db, user_id)
        return FollowCounts(followers_count=followers_c, following_count=following_c)


# ─── User Search ──────────────────────────────────────────────

def search_users(query: str, current_user_id: str, limit: int = 20) -> "UserSearchResponse":
    from models.schemas import UserSearchResponse, UserSearchResult

    with get_db_context() as db:
        pattern = f"%{query}%"

        rows = (
            db.query(UserDB, UserProfileDB)
            .outerjoin(UserProfileDB, UserProfileDB.user_id == UserDB.id)
            .filter(
                UserDB.id != current_user_id,
                UserDB.name.ilike(pattern) | UserDB.email.ilike(pattern),
            )
            .limit(limit)
            .all()
        )

        if not rows:
            return UserSearchResponse(users=[], total=0)

        my_following_ids = {
            r.following_id
            for r in db.query(UserFollow.following_id)
            .filter(UserFollow.follower_id == current_user_id, UserFollow.status == "accepted")
            .all()
        }

        users = [
            UserSearchResult(
                user_id=u.id,
                name=u.name,
                handle=_user_handle(u),
                bio=p.bio if p else None,
                is_following=u.id in my_following_ids,
            )
            for u, p in rows
        ]
        return UserSearchResponse(users=users, total=len(users))

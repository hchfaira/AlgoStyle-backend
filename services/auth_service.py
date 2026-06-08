"""
Auth service — handles user registration, login, and token management.
Uses PostgreSQL database for persistence.
"""
import secrets
from datetime import datetime, timedelta
from typing import Optional

from fastapi import HTTPException
from passlib.context import CryptContext
from models.schemas import AuthResponse, UserRole
from models.database import User, UserToken
from db import get_db_context

# bcrypt with auto-rehashing — safe against brute-force attacks
_pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

# Tokens expire after 30 days
TOKEN_TTL_DAYS = 30


def hash_password(pw: str) -> str:
    """Hash a password using bcrypt (salted)."""
    return _pwd_context.hash(pw)


def verify_password(plain: str, hashed: str) -> bool:
    """Verify a plain password against a stored bcrypt hash.

    Also handles the legacy sha-256 hashes that may still exist in the DB:
    if passlib rejects the hash format we fall back to the old sha-256
    comparison so existing accounts keep working on their first new login,
    after which the hash is re-stored as bcrypt.
    """
    try:
        return _pwd_context.verify(plain, hashed)
    except Exception:
        # Legacy sha-256 path — transparently upgrade on login
        import hashlib
        return hashlib.sha256(plain.encode()).hexdigest() == hashed


def generate_token() -> str:
    """Generate a secure random token."""
    return secrets.token_urlsafe(32)


def _token_expiry() -> datetime:
    """Return the absolute expiry datetime for a newly-created token."""
    return datetime.utcnow() + timedelta(days=TOKEN_TTL_DAYS)


def register_user(email: str, password: str, name: str, role: UserRole = UserRole.USER) -> AuthResponse:
    """Register a new user account."""
    with get_db_context() as db:
        # Check if email already exists
        existing = db.query(User).filter(User.email == email).first()
        if existing:
            raise HTTPException(400, "Email already registered")
        
        # Create new user
        user = User(
            email=email,
            password_hash=hash_password(password),
            name=name,
            role=role.value,
            is_onboarded=False,
        )
        db.add(user)
        db.flush()
        
        # Create token with expiry
        token = generate_token()
        user_token = UserToken(user_id=user.id, token=token, expires_at=_token_expiry())
        db.add(user_token)
        db.commit()
        
        return AuthResponse(
            user_id=user.id,
            token=token,
            name=user.name,
            email=user.email,
            role=UserRole(user.role),
            is_onboarded=user.is_onboarded,
        )


def login_user(email: str, password: str) -> AuthResponse:
    """Authenticate and log in a user."""
    with get_db_context() as db:
        user = db.query(User).filter(User.email == email).first()
        if not user:
            raise HTTPException(401, "Invalid credentials")
        
        if not verify_password(password, user.password_hash):
            raise HTTPException(401, "Invalid credentials")

        # Upgrade legacy sha-256 hash to bcrypt transparently on first new login
        if not user.password_hash.startswith("$2"):
            user.password_hash = hash_password(password)

        # Create token with expiry
        token = generate_token()
        user_token = UserToken(user_id=user.id, token=token, expires_at=_token_expiry())
        db.add(user_token)
        db.commit()
        
        return AuthResponse(
            user_id=user.id,
            token=token,
            name=user.name,
            email=user.email,
            role=UserRole(user.role),
            is_onboarded=user.is_onboarded,
        )


def create_guest() -> AuthResponse:
    """Create a guest user session."""
    with get_db_context() as db:
        user = User(
            email="",  # Guest has no email
            password_hash="",
            name="Guest",
            role=UserRole.USER.value,
            is_onboarded=False,
        )
        db.add(user)
        db.flush()
        
        # Create token with expiry
        token = generate_token()
        user_token = UserToken(user_id=user.id, token=token, expires_at=_token_expiry())
        db.add(user_token)
        db.commit()
        
        return AuthResponse(
            user_id=user.id,
            token=token,
            name=user.name,
            email=user.email,
            role=UserRole(user.role),
            is_onboarded=user.is_onboarded,
        )


def get_user_by_token(token: str) -> Optional[dict]:
    """Look up user data from token, enforcing expiry."""
    with get_db_context() as db:
        user_token = db.query(UserToken).filter(UserToken.token == token).first()
        if not user_token:
            raise HTTPException(401, "Invalid token")

        # Enforce expiry
        if user_token.expires_at and user_token.expires_at < datetime.utcnow():
            db.delete(user_token)
            db.commit()
            raise HTTPException(401, "Token expired — please log in again")
        
        user = db.query(User).filter(User.id == user_token.user_id).first()
        if not user:
            raise HTTPException(401, "Invalid token")
        
        return {
            "user_id": user.id,
            "email": user.email,
            "name": user.name,
            "role": user.role,
            "is_onboarded": user.is_onboarded,
        }


def logout_user(token: str) -> None:
    """Revoke a token — immediate logout."""
    with get_db_context() as db:
        user_token = db.query(UserToken).filter(UserToken.token == token).first()
        if user_token:
            db.delete(user_token)
            db.commit()

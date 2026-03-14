"""
Auth service — handles user registration, login, and token management.
Uses PostgreSQL database for persistence.
"""
import hashlib
import secrets
from typing import Optional

from fastapi import HTTPException
from sqlalchemy import select
from models.schemas import AuthResponse, UserRole
from models.database import User, UserToken
from db import get_db_context


def hash_password(pw: str) -> str:
    """Hash a password using SHA-256."""
    return hashlib.sha256(pw.encode()).hexdigest()


def generate_token() -> str:
    """Generate a secure random token."""
    return secrets.token_urlsafe(32)


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
        
        # Create token
        token = generate_token()
        user_token = UserToken(user_id=user.id, token=token)
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
        
        if user.password_hash != hash_password(password):
            raise HTTPException(401, "Invalid credentials")
        
        # Create token
        token = generate_token()
        user_token = UserToken(user_id=user.id, token=token)
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
        
        # Create token
        token = generate_token()
        user_token = UserToken(user_id=user.id, token=token)
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
    """Look up user data from token."""
    with get_db_context() as db:
        user_token = db.query(UserToken).filter(UserToken.token == token).first()
        if not user_token:
            raise HTTPException(401, "Invalid token")
        
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


"""
Auth service — handles user registration, login, and token management.
"""
import hashlib
import secrets
import uuid
from typing import Optional

from fastapi import HTTPException
from models.schemas import AuthResponse, UserRole
from services.store import users, tokens, email_index


def hash_password(pw: str) -> str:
    """Hash a password using SHA-256."""
    return hashlib.sha256(pw.encode()).hexdigest()


def generate_token() -> str:
    """Generate a secure random token."""
    return secrets.token_urlsafe(32)


def generate_user_id(prefix: str = "user") -> str:
    """Generate a unique user ID."""
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def register_user(email: str, password: str, name: str, role: UserRole = UserRole.USER) -> AuthResponse:
    """Register a new user account."""
    if email in email_index:
        raise HTTPException(400, "Email already registered")

    uid = generate_user_id()
    token = generate_token()

    users[uid] = {
        "user_id": uid,
        "email": email,
        "name": name,
        "password_hash": hash_password(password),
        "role": role,
        "is_onboarded": False,
    }
    email_index[email] = uid
    tokens[token] = uid

    return AuthResponse(
        user_id=uid,
        token=token,
        name=name,
        email=email,
        role=role,
        is_onboarded=False,
    )


def login_user(email: str, password: str) -> AuthResponse:
    """Authenticate and log in a user."""
    uid = email_index.get(email)
    if not uid:
        raise HTTPException(401, "Invalid credentials")

    user = users[uid]
    if user["password_hash"] != hash_password(password):
        raise HTTPException(401, "Invalid credentials")

    token = generate_token()
    tokens[token] = uid

    return AuthResponse(
        user_id=uid,
        token=token,
        name=user["name"],
        email=user["email"],
        role=user["role"],
        is_onboarded=user.get("is_onboarded", False),
    )


def create_guest() -> AuthResponse:
    """Create a guest user session."""
    uid = generate_user_id("guest")
    token = generate_token()
    users[uid] = {
        "user_id": uid,
        "email": "",
        "name": "Guest",
        "password_hash": "",
        "role": UserRole.USER,
        "is_onboarded": False,
    }
    tokens[token] = uid
    return AuthResponse(
        user_id=uid,
        token=token,
        name="Guest",
        email="",
        role=UserRole.USER,
        is_onboarded=False,
    )


def get_user_by_token(token: str) -> Optional[dict]:
    """Look up user data from token."""
    uid = tokens.get(token)
    if not uid:
        raise HTTPException(401, "Invalid token")
    return users.get(uid)

"""
Authentication routes — Registration, Login, Guest mode, Logout, /me.
Delegates business logic to services.auth_service.

Rate limits (applied via slowapi):
  POST /register  — 3 requests / minute per IP
  POST /login     — 5 requests / minute per IP
  POST /guest     — 10 requests / minute per IP
"""
from fastapi import APIRouter, Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from slowapi import Limiter
from slowapi.util import get_remote_address

from models.schemas import RegisterRequest, LoginRequest, AuthResponse
from services import auth_service
from deps import CurrentUser

router = APIRouter()
_bearer = HTTPBearer(auto_error=False)

# Module-level limiter — same key function as main.py's shared instance.
# slowapi decorators work independently per-route.
limiter = Limiter(key_func=get_remote_address)


@router.post("/register", response_model=AuthResponse)
@limiter.limit("3/minute")
async def register(request: Request, req: RegisterRequest):
    return auth_service.register_user(req.email, req.password, req.name, req.role)


@router.post("/login", response_model=AuthResponse)
@limiter.limit("5/minute")
async def login(request: Request, req: LoginRequest):
    return auth_service.login_user(req.email, req.password)


@router.post("/guest", response_model=AuthResponse)
@limiter.limit("10/minute")
async def guest_login(request: Request):
    """Continue without account — local-only mode."""
    return auth_service.create_guest()


@router.post("/logout")
async def logout(
    current_user: CurrentUser,
    credentials: HTTPAuthorizationCredentials = Depends(_bearer),
):
    """Revoke the current Bearer token immediately."""
    token = credentials.credentials if credentials else None
    if token:
        auth_service.logout_user(token)
    return {"detail": "Logged out"}


@router.get("/me")
async def get_current_user_me(current_user: CurrentUser):
    """Return the authenticated user's profile (token via Authorization header)."""
    return current_user

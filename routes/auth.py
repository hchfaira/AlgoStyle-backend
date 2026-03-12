"""
Authentication routes — Registration, Login, Guest mode.
Delegates business logic to services.auth_service.
"""
from fastapi import APIRouter
from models.schemas import RegisterRequest, LoginRequest, AuthResponse
from services import auth_service

router = APIRouter()


@router.post("/register", response_model=AuthResponse)
async def register(req: RegisterRequest):
    return auth_service.register_user(req.email, req.password, req.name, req.role)


@router.post("/login", response_model=AuthResponse)
async def login(req: LoginRequest):
    return auth_service.login_user(req.email, req.password)


@router.post("/guest", response_model=AuthResponse)
async def guest_login():
    """Continue without account — local-only mode."""
    return auth_service.create_guest()


@router.get("/me")
async def get_current_user(token: str):
    return auth_service.get_user_by_token(token)

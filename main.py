"""
AlgoStyle Backend — FastAPI Server
===================================
Mobile-first API gateway for the Fashion AI recommendation engine.
Wraps the LLM_project services and exposes mobile-friendly endpoints.
"""
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from contextlib import asynccontextmanager
import os
import logging

from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware

from config import settings
from routes import auth, onboarding, wardrobe, recommendation, chat, tryon, outfit, explain, image_consulting, social, notification
from db import init_db, test_connection

# ── Configure Python logging so logger.info() appears in console/log ──
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    datefmt="%H:%M:%S",
)

# ── Rate limiter — shared instance imported by route modules ──────────
limiter = Limiter(key_func=get_remote_address, default_limits=["200/minute"])

# ── Upload size guard ─────────────────────────────────────────────────
MAX_UPLOAD_BYTES = 10 * 1024 * 1024  # 10 MB


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifecycle events."""
    print("🚀 AlgoStyle API starting...")
    
    # Initialize database on startup
    try:
        init_db()
        if test_connection():
            print("✅ Database ready for requests")
        else:
            print("⚠️  Database connection test failed - check configuration")
    except Exception as e:
        print(f"❌ Database initialization error: {e}")
    
    yield
    print("👋 AlgoStyle API shutting down...")
    # Close Neo4j driver if it was opened
    try:
        from services.neo4j_service import _driver
        if _driver is not None:
            _driver.close()
            print("✅ Neo4j driver closed")
    except Exception as e:
        print(f"⚠️  Neo4j driver close error: {e}")


is_dev = settings.environment == "development"

app = FastAPI(
    title="AlgoStyle — Fashion AI",
    description="Mobile API for AI-powered fashion recommendations",
    version="0.1.0",
    lifespan=lifespan,
    # Disable interactive docs in production — reduces information leakage
    docs_url="/docs" if is_dev else None,
    redoc_url="/redoc" if is_dev else None,
    openapi_url="/openapi.json" if is_dev else None,
)

# ── Rate limiting ─────────────────────────────────────────────────────
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
app.add_middleware(SlowAPIMiddleware)

# ── Upload size guard middleware ──────────────────────────────────────
@app.middleware("http")
async def limit_upload_size(request: Request, call_next):
    content_length = request.headers.get("content-length")
    if content_length and int(content_length) > MAX_UPLOAD_BYTES:
        return JSONResponse(
            status_code=413,
            content={"detail": f"Request body too large. Maximum allowed size is {MAX_UPLOAD_BYTES // (1024 * 1024)} MB."},
        )
    return await call_next(request)

# ── CORS middleware — environment-aware configuration ─────────────────
cors_origins = settings.get_cors_origins()

app.add_middleware(
    CORSMiddleware,
    # In dev: wildcard ("*") so any Expo/browser origin works without listing every IP.
    # In prod: restrict to explicit domains only.
    allow_origins=["*"] if is_dev else cors_origins,
    allow_origin_regex=None if is_dev else r"http://(localhost|127\.0\.0\.1)(:\d+)?",
    allow_credentials=False if is_dev else settings.cors_credentials,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["*"],
)

print(f"🔒 CORS enabled for: {cors_origins} (Environment: {settings.environment})")

# Mount routes
app.include_router(auth.router, prefix="/api/v1/auth", tags=["Authentication"])
app.include_router(onboarding.router, prefix="/api/v1/onboarding", tags=["Onboarding"])
app.include_router(wardrobe.router, prefix="/api/v1/wardrobe", tags=["Wardrobe"])
app.include_router(recommendation.router, prefix="/api/v1/recommend", tags=["Recommendations"])
app.include_router(explain.router, prefix="/api/v1/recommend", tags=["Recommendations"])
app.include_router(chat.router, prefix="/api/v1/chat", tags=["Chat"])
app.include_router(tryon.router, prefix="/api/v1/tryon", tags=["Try-On"])
app.include_router(outfit.router)
app.include_router(image_consulting.router, prefix="/api/v1/image-consulting", tags=["Image Consulting"])
app.include_router(social.router)
app.include_router(notification.router)


@app.get("/health")
async def health():
    return {"status": "healthy", "version": "0.1.0", "service": "algostyle"}


@app.get("/")
async def root():
    return {
        "message": "AlgoStyle — Fashion AI API",
        "docs": "/docs",
        "version": "0.1.0",
    }

"""
AlgoStyle Backend — FastAPI Server
===================================
Mobile-first API gateway for the Fashion AI recommendation engine.
Wraps the LLM_project services and exposes mobile-friendly endpoints.
"""
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager
import os

from config import settings
from routes import auth, onboarding, wardrobe, recommendation, chat, tryon, outfit, explain, image_consulting, social
from db import init_db, test_connection


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


app = FastAPI(
    title="AlgoStyle — Fashion AI",
    description="Mobile API for AI-powered fashion recommendations",
    version="0.1.0",
    lifespan=lifespan,
)

# CORS middleware - environment-aware configuration
cors_origins = settings.get_cors_origins()
is_dev = settings.environment == "development"

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

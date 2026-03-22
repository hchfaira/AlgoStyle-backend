"""
Configuration management for AlgoStyle Backend
Supports dev, staging, and production environments
"""
from pydantic_settings import BaseSettings
from typing import List


class Settings(BaseSettings):
    """Application settings loaded from environment variables"""
    
    # Environment
    environment: str = "development"  # development, staging, production
    
    # Database — no default, must be set in .env
    database_url: str
    
    # Google Gemini API — no default, must be set in .env
    google_api_key: str

    # LLM_project API — AI vision, scoring, prompt-search
    # Set to "" to disable and use the fallback mock inside each service.
    llm_api_url: str = ""

    # Neo4j (shared with LLM_project — garment graph)
    # Leave empty to disable Neo4j integration
    neo4j_uri: str = ""
    neo4j_user: str = ""
    neo4j_password: str = ""
    neo4j_database: str = "neo4j"

    # CORS Configuration
    cors_origins: str = ""
    cors_credentials: bool = True
    cors_methods: List[str] = ["GET", "POST", "PUT", "DELETE", "OPTIONS", "PATCH"]
    cors_headers: List[str] = ["*"]
    cors_expose_headers: List[str] = ["*"]
    
    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        case_sensitive = False
    
    def get_cors_origins(self) -> List[str]:
        """
        Returns appropriate CORS origins based on environment.
        
        Development: Allow multiple localhost origins + local network IP
        Production: Restrict to specific domains
        """
        if self.environment == "production":
            # Production: only allow specific domains
            return [
                "https://api.algostyle.app",
                "https://algostyle.app",
                "https://app.algostyle.com",
            ]
        elif self.environment == "staging":
            # Staging: allow staging domain + dev origins
            return [
                "https://staging-api.algostyle.app",
                "https://staging.algostyle.app",
                "http://localhost:3000",
                "http://localhost:8081",
            ]
        else:
            # Development: parse from env var — must be set in .env
            if not self.cors_origins:
                raise ValueError("CORS_ORIGINS must be set in .env for development environment")
            return [origin.strip() for origin in self.cors_origins.split(",")]


settings = Settings()


def get_settings() -> Settings:
    """Compatibility shim — returns the singleton Settings instance."""
    return settings

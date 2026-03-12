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
    
    # CORS Configuration
    cors_origins: str = "http://localhost:3000,http://localhost:8081,http://192.168.1.71:8000"
    cors_credentials: bool = True
    cors_methods: List[str] = ["GET", "POST", "PUT", "DELETE", "OPTIONS", "PATCH"]
    cors_headers: List[str] = ["*"]
    
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
            # Development: parse from env var or use defaults
            return [origin.strip() for origin in self.cors_origins.split(",")]


settings = Settings()

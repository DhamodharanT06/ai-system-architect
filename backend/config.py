from pydantic_settings import BaseSettings
from typing import Optional
import os
from pathlib import Path

class Settings(BaseSettings):
    groq_api_key: str = os.getenv("GROQ_API_KEY", "")
    groq_model: str = os.getenv("GROQ_MODEL", "")
    backend_port: int = int(os.getenv("BACKEND_PORT", 8000))
    frontend_url: str = os.getenv("FRONTEND_URL", "http://localhost:3000")
    debug: bool = os.getenv("DEBUG", "True").lower() == "true"
    
    class Config:
        env_file = ".env"
        case_sensitive = False

settings = Settings()

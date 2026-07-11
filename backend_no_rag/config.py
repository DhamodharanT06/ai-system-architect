from pydantic_settings import BaseSettings
from typing import Optional
import os


class Settings(BaseSettings):
    # ── Groq ──────────────────────────────────────────────────────────────
    groq_api_key: str = os.getenv("GROQ_API_KEY", "")
    groq_model:   str = os.getenv("GROQ_MODEL", "")

    # ── Server ────────────────────────────────────────────────────────────
    backend_port: int  = int(os.getenv("BACKEND_PORT", 8000))
    frontend_url: str  = os.getenv("FRONTEND_URL", "http://localhost:3000")
    debug:        bool = os.getenv("DEBUG", "True").lower() == "true"

    # ── RAG source API keys (all optional — sources are skipped if absent) ─
    tavily_api_key: str = os.getenv("TAVILY_API_KEY", "")   # https://tavily.com
    core_api_key:   str = os.getenv("CORE_API_KEY",   "")   # https://core.ac.uk/services/api
    github_token:   str = os.getenv("GITHUB_TOKEN",   "")   # GitHub PAT (raises rate limit)

    # ── RAG behaviour ─────────────────────────────────────────────────────
    rag_enabled:    bool = os.getenv("RAG_ENABLED", "True").lower() == "true"

    class Config:
        env_file      = ".env"
        case_sensitive = False


settings = Settings()


# from pydantic_settings import BaseSettings
# from typing import Optional
# import os
# from pathlib import Path

# class Settings(BaseSettings):
#     groq_api_key: str = os.getenv("GROQ_API_KEY", "")
#     groq_model: str = os.getenv("GROQ_MODEL", "")
#     backend_port: int = int(os.getenv("BACKEND_PORT", 8000))
#     frontend_url: str = os.getenv("FRONTEND_URL", "http://localhost:3000")
#     debug: bool = os.getenv("DEBUG", "True").lower() == "true"
    
#     class Config:
#         env_file = ".env"
#         case_sensitive = False

# settings = Settings()

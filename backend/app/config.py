# File: /backend/app/config.py
import os
from functools import lru_cache

from dotenv import load_dotenv

load_dotenv()


class Settings:
    """Application settings loaded from environment variables."""

    def __init__(self) -> None:
        self.PROJECT_NAME: str = os.getenv("PROJECT_NAME", "Dasan Shift Manager")
        self.APP_ENV: str = os.getenv("APP_ENV", "production")
        self.JWT_ALGORITHM: str = os.getenv("JWT_ALGORITHM", "HS256")
        self.ACCESS_TOKEN_EXPIRE_MINUTES: int = int(os.getenv("ACCESS_TOKEN_EXPIRE_MINUTES", "60"))

        self.DATABASE_URL: str | None = os.getenv("DATABASE_URL")
        self.JWT_SECRET: str | None = os.getenv("JWT_SECRET")

        # Reverse proxy / tunnel aware settings
        self.API_ROOT_PATH: str = os.getenv("API_ROOT_PATH", "")
        self.TRUSTED_HOSTS: list[str] = [
            host.strip() for host in os.getenv("TRUSTED_HOSTS", "localhost,127.0.0.1").split(",") if host.strip()
        ]

        cors_origins = os.getenv("BACKEND_CORS_ORIGINS", "")
        if not cors_origins:
            self.CORS_ALLOW_ORIGINS = []
        elif cors_origins == "*":
            self.CORS_ALLOW_ORIGINS = ["*"]
        else:
            self.CORS_ALLOW_ORIGINS = [origin.strip() for origin in cors_origins.split(",") if origin.strip()]

        allow_credentials_env = os.getenv("BACKEND_CORS_ALLOW_CREDENTIALS", "false")
        self.CORS_ALLOW_CREDENTIALS = allow_credentials_env.lower() == "true"

        if not self.DATABASE_URL:
            raise RuntimeError("DATABASE_URL is not set. Configure it in your environment.")
        if not self.JWT_SECRET:
            raise RuntimeError("JWT_SECRET is not set. Configure it in your environment.")


@lru_cache()
def get_settings() -> Settings:
    return Settings()

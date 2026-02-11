# File: /backend/app/config.py
import os
from functools import lru_cache

from dotenv import load_dotenv

load_dotenv()


class Settings:
    """Application settings loaded from environment variables.

    DATABASE_URL and JWT_SECRET are intentionally required to avoid
    accidentally committing secrets or running with unintended defaults.
    """

    def __init__(self) -> None:
        self.PROJECT_NAME: str = "Dasan Shift Manager"
        self.JWT_ALGORITHM: str = "HS256"
        self.ACCESS_TOKEN_EXPIRE_MINUTES: int = int(os.getenv("ACCESS_TOKEN_EXPIRE_MINUTES", "60"))

        self.DATABASE_URL: str | None = os.getenv("DATABASE_URL")
        self.JWT_SECRET: str | None = os.getenv("JWT_SECRET")

        cors_origins = os.getenv("BACKEND_CORS_ORIGINS", "*")
        if not cors_origins or cors_origins == "*":
            self.CORS_ALLOW_ORIGINS = ["*"]
        else:
            self.CORS_ALLOW_ORIGINS = [origin.strip() for origin in cors_origins.split(",") if origin.strip()]

        allow_credentials_env = os.getenv("BACKEND_CORS_ALLOW_CREDENTIALS")
        if allow_credentials_env is None:
            # Using "*" together with credentials is not allowed by the CORS spec.
            self.CORS_ALLOW_CREDENTIALS = self.CORS_ALLOW_ORIGINS != ["*"]
        else:
            self.CORS_ALLOW_CREDENTIALS = allow_credentials_env.lower() == "true"

        if not self.DATABASE_URL:
            raise RuntimeError("DATABASE_URL is not set. Configure it in your environment (e.g., Render secret).")
        if not self.JWT_SECRET:
            raise RuntimeError("JWT_SECRET is not set. Configure it in your environment (e.g., Render secret).")


@lru_cache()
def get_settings() -> Settings:
    return Settings()

# File: /backend/app/config.py
import os
from functools import lru_cache

from dotenv import find_dotenv, load_dotenv


def _safe_load_dotenv() -> None:
    """Load .env only when a readable file is found.

    In production, systemd EnvironmentFile can inject env vars already; in that case
    unreadable .env files should not crash app startup.
    """

    dotenv_path = find_dotenv(usecwd=True)
    if not dotenv_path:
        return
    if not os.access(dotenv_path, os.R_OK):
        return
    load_dotenv(dotenv_path=dotenv_path)


_safe_load_dotenv()


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
        default_trusted_hosts = "localhost,127.0.0.1,*.trycloudflare.com,*.cfargotunnel.com,*.workers.dev"
        self.TRUSTED_HOSTS: list[str] = [
            host.strip() for host in os.getenv("TRUSTED_HOSTS", default_trusted_hosts).split(",") if host.strip()
        ]
        self.TRUST_ALL_HOSTS = os.getenv("TRUST_ALL_HOSTS", "false").lower() == "true"

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

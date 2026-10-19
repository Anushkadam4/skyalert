"""Application settings, read from environment variables."""
import os
from dataclasses import dataclass, field


def _env_bool(name: str, default: bool = False) -> bool:
    return os.getenv(name, str(default)).lower() in {"1", "true", "yes"}


@dataclass(frozen=True)
class Settings:
    database_url: str = field(default_factory=lambda: os.getenv("DATABASE_URL", "sqlite:///./skyalert.db"))
    redis_url: str = field(default_factory=lambda: os.getenv("REDIS_URL", "redis://localhost:6379/0"))
    jwt_secret: str = field(default_factory=lambda: os.getenv("JWT_SECRET", "dev-secret-change-me"))
    jwt_ttl_minutes: int = field(default_factory=lambda: int(os.getenv("JWT_TTL_MINUTES", "1440")))
    rate_limit_per_minute: int = field(default_factory=lambda: int(os.getenv("RATE_LIMIT_PER_MINUTE", "300")))
    cors_origins: list[str] = field(
        default_factory=lambda: os.getenv("CORS_ORIGINS", "http://localhost:5173").split(",")
    )
    # Webhook delivery
    webhook_max_attempts: int = field(default_factory=lambda: int(os.getenv("WEBHOOK_MAX_ATTEMPTS", "5")))
    webhook_per_user_per_minute: int = field(default_factory=lambda: int(os.getenv("WEBHOOK_PER_USER_PER_MINUTE", "60")))
    allow_private_webhooks: bool = field(default_factory=lambda: _env_bool("ALLOW_PRIVATE_WEBHOOKS"))


settings = Settings()

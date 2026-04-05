import os
from dataclasses import dataclass


def _parse_bool(value: str | None, default: bool) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _parse_csv(value: str | None, default: list[str]) -> list[str]:
    if value is None:
        return default
    items = [item.strip() for item in value.split(",")]
    return [item for item in items if item]


@dataclass(frozen=True)
class Settings:
    app_name: str = os.getenv("APP_NAME", "Tourist Assistant")
    env: str = os.getenv("APP_ENV", "development")
    database_url: str = os.getenv("DATABASE_URL", "sqlite:///./tourist_agent.db")
    jwt_secret: str = os.getenv("JWT_SECRET", "change-this-in-production")
    jwt_access_minutes: int = int(os.getenv("JWT_ACCESS_MINUTES", "30"))
    jwt_refresh_days: int = int(os.getenv("JWT_REFRESH_DAYS", "14"))
    cors_allow_origins: tuple[str, ...] = tuple(
        _parse_csv(
            os.getenv("CORS_ALLOW_ORIGINS"),
            ["http://127.0.0.1:5173", "http://localhost:5173"],
        )
    )
    trusted_hosts: tuple[str, ...] = tuple(
        _parse_csv(
            os.getenv("TRUSTED_HOSTS"),
            ["localhost", "127.0.0.1", "testserver"],
        )
    )
    enable_trusted_host: bool = _parse_bool(os.getenv("ENABLE_TRUSTED_HOST"), False)
    create_tables_on_startup: bool = _parse_bool(os.getenv("CREATE_TABLES_ON_STARTUP"), True)
    docs_enabled: bool = _parse_bool(os.getenv("DOCS_ENABLED"), True)
    security_headers_enabled: bool = _parse_bool(os.getenv("SECURITY_HEADERS_ENABLED"), True)
    hsts_enabled: bool = _parse_bool(os.getenv("HSTS_ENABLED"), False)


settings = Settings()

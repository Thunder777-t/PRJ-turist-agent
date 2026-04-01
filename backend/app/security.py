import hashlib
from datetime import datetime, timedelta, timezone
from typing import Any

from jose import JWTError, jwt
from passlib.context import CryptContext

from .config import settings


pwd_context = CryptContext(schemes=["argon2"], deprecated="auto")
ALGORITHM = "HS256"


def hash_password(password: str) -> str:
    return pwd_context.hash(password)


def verify_password(plain_password: str, password_hash: str) -> bool:
    return pwd_context.verify(plain_password, password_hash)


def hash_refresh_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _build_expiration(minutes: int = 0, days: int = 0) -> datetime:
    return datetime.now(timezone.utc) + timedelta(minutes=minutes, days=days)


def create_access_token(user_id: str) -> tuple[str, int]:
    expires_at = _build_expiration(minutes=settings.jwt_access_minutes)
    payload: dict[str, Any] = {
        "sub": user_id,
        "type": "access",
        "exp": expires_at,
    }
    token = jwt.encode(payload, settings.jwt_secret, algorithm=ALGORITHM)
    return token, settings.jwt_access_minutes * 60


def create_refresh_token(user_id: str, session_id: str) -> tuple[str, datetime]:
    expires_at = _build_expiration(days=settings.jwt_refresh_days)
    payload: dict[str, Any] = {
        "sub": user_id,
        "sid": session_id,
        "type": "refresh",
        "exp": expires_at,
    }
    token = jwt.encode(payload, settings.jwt_secret, algorithm=ALGORITHM)
    return token, expires_at


def decode_token(token: str) -> dict[str, Any]:
    return jwt.decode(token, settings.jwt_secret, algorithms=[ALGORITHM])


def decode_token_safely(token: str) -> dict[str, Any] | None:
    try:
        return decode_token(token)
    except JWTError:
        return None


from __future__ import annotations

import os
from contextvars import ContextVar, Token
from typing import Any


_RUNTIME_PREFS: ContextVar[dict[str, Any]] = ContextVar("agent_runtime_prefs", default={})


def set_runtime_preferences(prefs: dict[str, Any] | None) -> Token:
    data = prefs if isinstance(prefs, dict) else {}
    return _RUNTIME_PREFS.set(dict(data))


def reset_runtime_preferences(token: Token) -> None:
    _RUNTIME_PREFS.reset(token)


def get_runtime_preference(key: str, default: Any = None) -> Any:
    prefs = _RUNTIME_PREFS.get({})
    if key in prefs:
        return prefs.get(key)
    return default


def get_agent_speed_mode(default: str = "quality") -> str:
    raw = get_runtime_preference("agent_speed_mode")
    if raw is None:
        raw = os.getenv("AGENT_SPEED_MODE", default)
    mode = str(raw or default).strip().lower()
    if mode in {"fast", "aggressive", "极速"}:
        return "fast"
    if mode in {"quality", "high_quality", "accurate", "精确", "高质量"}:
        return "quality"
    return "balanced"


def runtime_truthy(key: str, env_key: str, default: bool) -> bool:
    raw = get_runtime_preference(key)
    if raw is not None:
        return str(raw).strip().lower() in {"1", "true", "yes", "on"}
    env_raw = (os.getenv(env_key) or "").strip()
    if not env_raw:
        return default
    return env_raw.lower() in {"1", "true", "yes", "on"}

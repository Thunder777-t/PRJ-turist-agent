from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


DEFAULT_TIMEZONE = "Asia/Shanghai"


def _safe_timezone_name(value: Any) -> str:
    timezone_name = str(value or "").strip()
    return timezone_name or DEFAULT_TIMEZONE


def build_runtime_time_context(user_preferences: dict[str, Any] | None = None) -> dict[str, str]:
    prefs = user_preferences or {}
    timezone_name = _safe_timezone_name(prefs.get("timezone"))
    try:
        now = datetime.now(ZoneInfo(timezone_name))
    except ZoneInfoNotFoundError:
        # Some Python distributions on Windows may not bundle tzdata.
        # Fallback to local timezone clock while preserving a stable label.
        now = datetime.now().astimezone()
        timezone_name = now.tzname() or "Local"

    today = now.date()
    tomorrow = today + timedelta(days=1)
    day_after_tomorrow = today + timedelta(days=2)

    return {
        "timezone": timezone_name,
        "current_datetime": now.strftime("%Y-%m-%d %H:%M:%S"),
        "current_date": today.isoformat(),
        "current_weekday": now.strftime("%A"),
        "relative_today": today.isoformat(),
        "relative_tomorrow": tomorrow.isoformat(),
        "relative_day_after_tomorrow": day_after_tomorrow.isoformat(),
    }

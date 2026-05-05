from __future__ import annotations

import json
from typing import Any

from ..deepseek_client import DeepseekClient
from .types import TravelSlots


CLIENT = DeepseekClient()


def _to_int(value: Any) -> int | None:
    if value in (None, ""):
        return None
    try:
        number = int(str(value).strip())
    except Exception:
        return None
    if 1 <= number <= 60:
        return number
    return None


def _to_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _to_string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    items: list[str] = []
    for raw in value:
        text = str(raw).strip()
        if text:
            items.append(text)
    # keep order and dedupe
    seen = set()
    output: list[str] = []
    for item in items:
        key = item.lower()
        if key in seen:
            continue
        seen.add(key)
        output.append(item)
    return output


def _build_fallback_slots(user_preferences: dict[str, object] | None = None) -> TravelSlots:
    prefs = user_preferences or {}
    interests = prefs.get("interests") if isinstance(prefs.get("interests"), list) else []
    language = prefs.get("language") if isinstance(prefs.get("language"), str) else None
    return TravelSlots(
        interests=[str(item).strip() for item in interests if str(item).strip()],
        language=language.strip() if isinstance(language, str) and language.strip() else None,
    )


def extract_slots(
    user_input: str,
    memory_slots: dict[str, object] | None = None,
    user_preferences: dict[str, object] | None = None,
    conversation_history: list[dict[str, str]] | None = None,
) -> TravelSlots:
    if not CLIENT.enabled:
        return _build_fallback_slots(user_preferences)

    system_prompt = (
        "You extract travel requirement slots from user requests. "
        "Use latest user message first, and use history/preferences only for missing details."
    )
    developer_prompt = (
        "Return strict JSON with keys: destination, duration_days, origin, travel_time, budget, companions, "
        "interests, transport_mode, accommodation_preference, food_preference, pace_preference, language. "
        "Rules: do not hallucinate; if unknown use null (or [] for interests). "
        "Destination must be clean place name only, without trailing phrases like 'with a budget of' or '玩3天'."
    )

    payload = {
        "latest_user_input": user_input,
        "recent_history": (conversation_history or [])[-12:],
        "memory_slots": memory_slots or {},
        "user_preferences": user_preferences or {},
    }
    user_prompt = json.dumps(payload, ensure_ascii=False)

    messages = CLIENT.build_messages(
        system_prompt=system_prompt,
        developer_prompt=developer_prompt,
        user_prompt=user_prompt,
        conversation_history=None,
    )

    result = CLIENT.json_completion(messages=messages, temperature=0.0, max_tokens=900)
    if not result.get("ok"):
        return _build_fallback_slots(user_preferences)

    data: dict[str, Any] = result.get("json", {})

    slots = TravelSlots(
        destination=_to_text(data.get("destination")),
        duration_days=_to_int(data.get("duration_days")),
        origin=_to_text(data.get("origin")),
        travel_time=_to_text(data.get("travel_time")),
        budget=_to_text(data.get("budget")),
        companions=_to_text(data.get("companions")),
        interests=_to_string_list(data.get("interests")),
        transport_mode=_to_text(data.get("transport_mode")),
        accommodation_preference=_to_text(data.get("accommodation_preference")),
        food_preference=_to_text(data.get("food_preference")),
        pace_preference=_to_text(data.get("pace_preference")),
        language=_to_text(data.get("language")),
    )

    # Preference fill-in is allowed only for still-empty fields.
    prefs = user_preferences or {}
    if not slots.language and isinstance(prefs.get("language"), str):
        pref_lang = str(prefs.get("language", "")).strip()
        if pref_lang:
            slots.language = pref_lang
    if not slots.interests and isinstance(prefs.get("interests"), list):
        slots.interests = [
            str(item).strip() for item in prefs.get("interests", []) if str(item).strip()
        ]

    return slots

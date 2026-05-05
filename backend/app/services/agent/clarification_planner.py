from __future__ import annotations

import json
from typing import Any

from ..deepseek_client import DeepseekClient
from .types import TravelSlots


CLIENT = DeepseekClient()


FIELD_LABELS = {
    "destination": "destination",
    "duration_days": "duration_days",
    "origin": "origin",
    "travel_time": "travel_time",
    "budget": "budget",
    "companions": "companions",
    "interests": "interests",
    "transport_mode": "transport_mode",
    "accommodation_preference": "accommodation_preference",
    "food_preference": "food_preference",
    "pace_preference": "pace_preference",
}


def _fallback(slots: TravelSlots) -> dict[str, object]:
    known = slots.known_info()
    missing = [key for key in FIELD_LABELS if known.get(key) in (None, "", [])]
    return {
        "missing_info": missing,
        "critical_missing": [name for name in ["destination", "duration_days"] if name in missing],
        "assumptions": [],
        "clarification_questions": [],
        "should_block_without_basics": any(name in missing for name in ["destination", "duration_days"]),
    }


def plan_clarification(
    slots: TravelSlots,
    user_input: str = "",
    conversation_history: list[dict[str, str]] | None = None,
) -> dict[str, object]:
    if not CLIENT.enabled:
        return _fallback(slots)

    system_prompt = "You decide what additional information is needed to produce a reliable travel plan."
    developer_prompt = (
        "Return strict JSON keys: missing_info, critical_missing, assumptions, clarification_questions, "
        "should_block_without_basics. "
        "All list items must be short plain strings. "
        "Use field names from this allowed set when applicable: "
        "destination, duration_days, origin, travel_time, budget, companions, interests, "
        "transport_mode, accommodation_preference, food_preference, pace_preference."
    )

    payload = {
        "latest_user_input": user_input,
        "known_slots": slots.known_info(),
        "recent_history": (conversation_history or [])[-10:],
    }
    user_prompt = json.dumps(payload, ensure_ascii=False)

    messages = CLIENT.build_messages(
        system_prompt=system_prompt,
        developer_prompt=developer_prompt,
        user_prompt=user_prompt,
        conversation_history=None,
    )
    result = CLIENT.json_completion(messages=messages, temperature=0.1, max_tokens=700)
    if not result.get("ok"):
        return _fallback(slots)

    data: dict[str, Any] = result.get("json", {})

    missing_info = [str(x).strip() for x in data.get("missing_info", []) if str(x).strip()]
    critical_missing = [str(x).strip() for x in data.get("critical_missing", []) if str(x).strip()]
    assumptions = [str(x).strip() for x in data.get("assumptions", []) if str(x).strip()]
    questions = [str(x).strip() for x in data.get("clarification_questions", []) if str(x).strip()]

    should_block = bool(data.get("should_block_without_basics", False))

    return {
        "missing_info": missing_info,
        "critical_missing": critical_missing,
        "assumptions": assumptions,
        "clarification_questions": questions[:6],
        "should_block_without_basics": should_block,
    }
